#!/usr/bin/env python3
"""Validate the tracked automation inventory and report inventory drift."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


CLASSIFICATIONS = frozenset(
    {"archived", "excluded", "generated", "mirrored", "owned", "vendored-upstream"}
)
COVERAGE_STATUSES = frozenset({"covered", "not-applicable", "partial", "planned"})
LANGUAGES = frozenset(
    {
        "ansible",
        "bash",
        "batch",
        "data",
        "javascript",
        "jsonc",
        "powershell",
        "python",
        "shell",
        "typescript",
        "yaml",
        "zsh",
    }
)
OWNER_KINDS = frozenset(
    {"archive", "generator", "mirror-source", "none", "repository", "upstream"}
)
PLATFORMS = frozenset({"ci", "devcontainer", "linux", "macos", "windows", "wsl2"})
RISKS = frozenset({"critical", "high", "low", "medium"})
SIDE_EFFECTS = frozenset(
    {
        "beads",
        "filesystem",
        "git",
        "gui",
        "host-config",
        "installer",
        "network",
        "none",
        "process",
        "secrets",
    }
)
TEST_LAYERS = frozenset(
    {"contract", "integration", "platform-smoke", "provenance", "render", "static", "unit"}
)

SCRIPT_SUFFIXES = frozenset(
    {
        ".bash",
        ".bat",
        ".cmd",
        ".cjs",
        ".fish",
        ".js",
        ".mjs",
        ".ps1",
        ".psd1",
        ".psm1",
        ".py",
        ".sh",
        ".ts",
        ".tsx",
        ".zsh",
    }
)
PROSE_OR_DATA_SUFFIXES = frozenset(
    {".css", ".html", ".json", ".jsonc", ".md", ".toml", ".txt", ".xml", ".yaml", ".yml"}
)
STARTUP_NAMES = frozenset(
    {
        "dot_bash_profile",
        "dot_bashrc",
        "dot_profile",
        "dot_zprofile",
        "dot_zshenv",
        "dot_zshrc",
    }
)
SPECIAL_NAMES = frozenset({".envrc", ".gitlab-ci.yml", ".gitlab-ci.yaml"})
DEVCONTAINER_COMMAND_RE = re.compile(
    r'"(?:initializeCommand|onCreateCommand|postAttachCommand|postCreateCommand|'
    r'postStartCommand|updateContentCommand)"\s*:'
)
ANSIBLE_COMMAND_RE = re.compile(
    r"^\s*(?:-\s+)?(?:(?:ansible|community)\.[A-Za-z0-9_.]+\.)?"
    r"(?:command|raw|script|shell)\s*:",
    re.MULTILINE,
)
SHEBANG_RE = re.compile(
    r"^#!.*(?:\b(?:ba|z|fi)?sh\b|node|python|pwsh|powershell)", re.MULTILINE
)


class CheckFailure(ValueError):
    """An actionable repository, JSON, or Git failure."""


@dataclass(frozen=True)
class TrackedFile:
    path: str
    mode: str


@dataclass(frozen=True)
class Candidate:
    path: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CheckResult:
    errors: tuple[str, ...]
    candidates: tuple[Candidate, ...]
    classifications: tuple[tuple[str, str], ...]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CheckFailure(f"cannot read file: {error}") from error
    except json.JSONDecodeError as error:
        raise CheckFailure(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error


def tracked_files(repo_root: Path) -> tuple[TrackedFile, ...]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--stage", "-z"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise CheckFailure(f"cannot run git: {error}") from error
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise CheckFailure(f"git ls-files failed: {message or 'unknown error'}")

    files: list[TrackedFile] = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode = metadata.split(None, 1)[0].decode("ascii")
            path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise CheckFailure("git returned an unreadable index record") from error
        files.append(TrackedFile(path, mode))
    return tuple(sorted(files, key=lambda item: item.path))


def _script_suffix(path: str) -> str:
    base = path[:-5] if path.endswith(".tmpl") else path
    return PurePosixPath(base).suffix.casefold()


def _read_sample(repo_root: Path, tracked: TrackedFile) -> str:
    if tracked.mode not in {"100644", "100755"}:
        return ""
    try:
        return (repo_root / tracked.path).read_bytes()[:65536].decode("utf-8", "ignore")
    except OSError:
        return ""


def candidate_reasons(repo_root: Path, tracked: TrackedFile) -> tuple[str, ...]:
    path = tracked.path
    pure = PurePosixPath(path)
    name = pure.name
    base_name = name[:-5] if name.endswith(".tmpl") else name
    suffix = _script_suffix(path)
    reasons: set[str] = set()

    if tracked.mode == "100755":
        reasons.add("executable-mode")
    if suffix in SCRIPT_SUFFIXES:
        reasons.add("script-extension")
    if base_name.startswith("executable_"):
        reasons.add("chezmoi-executable")
    if pure.parts and pure.parts[0] == ".chezmoiscripts":
        reasons.add("chezmoi-lifecycle")
    if base_name in STARTUP_NAMES:
        reasons.add("shell-startup")
    if name in SPECIAL_NAMES:
        reasons.add("known-automation-file")

    sample = _read_sample(repo_root, tracked)
    if suffix not in PROSE_OR_DATA_SUFFIXES and SHEBANG_RE.search("\n".join(sample.splitlines()[:40])):
        reasons.add("shebang")

    if name in {"devcontainer.json", "devcontainer.json.tmpl"} and DEVCONTAINER_COMMAND_RE.search(sample):
        reasons.add("embedded-devcontainer-command")
    if path.startswith("ansible/") and suffix in {".yaml", ".yml"} and ANSIBLE_COMMAND_RE.search(sample):
        reasons.add("embedded-ansible-command")

    return tuple(sorted(reasons))


def discover_candidates(repo_root: Path, tracked: tuple[TrackedFile, ...]) -> tuple[Candidate, ...]:
    candidates = [
        Candidate(item.path, reasons)
        for item in tracked
        if (reasons := candidate_reasons(repo_root, item))
    ]
    return tuple(candidates)


def candidate_digest(candidates: tuple[Candidate, ...]) -> str:
    payload = "".join(
        f"{candidate.path}\t{','.join(candidate.reasons)}\n" for candidate in candidates
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def selector_regex(selector: str) -> re.Pattern[str]:
    """Compile a root-anchored glob where * stops at / and ** crosses it."""

    output = ["^"]
    index = 0
    while index < len(selector):
        character = selector[index]
        if character == "*":
            if index + 1 < len(selector) and selector[index + 1] == "*":
                output.append(".*")
                index += 2
            else:
                output.append("[^/]*")
                index += 1
        elif character == "?":
            output.append("[^/]")
            index += 1
        else:
            output.append(re.escape(character))
            index += 1
    output.append("$")
    return re.compile("".join(output))


def _check_string_list(
    value: Any,
    *,
    label: str,
    allowed: frozenset[str] | None,
    allow_empty: bool,
) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise CheckFailure(f"{label} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise CheckFailure(f"{label} must not be empty")
    if value != sorted(value):
        raise CheckFailure(f"{label} must be sorted")
    if len(value) != len(set(value)):
        raise CheckFailure(f"{label} must not contain duplicates")
    if allowed is not None:
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise CheckFailure(f"{label} contains unsupported value(s): {', '.join(unknown)}")
    return value


def _check_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CheckFailure(f"{label} must be a non-empty string")
    return value


def check_repository(repo_root: Path, manifest_path: Path | None = None) -> CheckResult:
    root = repo_root.resolve()
    selected_manifest = manifest_path or root / "configs/automation-test-inventory.json"
    if not selected_manifest.is_absolute():
        selected_manifest = root / selected_manifest

    try:
        tracked = tracked_files(root)
    except CheckFailure as error:
        return CheckResult((str(error),), (), ())
    candidates = discover_candidates(root, tracked)
    candidate_paths = {candidate.path for candidate in candidates}
    tracked_paths = {item.path for item in tracked}
    errors: list[str] = []

    try:
        manifest = load_json(selected_manifest)
    except CheckFailure as error:
        return CheckResult((f"{selected_manifest}: {error}",), candidates, ())
    if not isinstance(manifest, dict):
        return CheckResult((f"{selected_manifest}: manifest root must be an object",), candidates, ())
    if manifest.get("schema_version") != 1:
        errors.append(f"{selected_manifest}: schema_version must be 1")
    expected_digest = manifest.get("candidate_digest")
    actual_digest = candidate_digest(candidates)
    if expected_digest != actual_digest:
        errors.append(
            f"{selected_manifest}: candidate_digest must be {actual_digest!r}; "
            "review --list-candidates before updating the snapshot"
        )
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return CheckResult(tuple(errors + [f"{selected_manifest}: entries must be a list"]), candidates, ())

    entry_ids: set[str] = set()
    classified: dict[str, str] = {}
    classifications: list[tuple[str, str]] = []
    previous_id = ""

    for index, entry in enumerate(entries):
        prefix = f"{selected_manifest}: entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} must be an object")
            continue
        try:
            entry_id = _check_nonempty_string(entry.get("id"), f"{prefix}.id")
            classification = _check_nonempty_string(
                entry.get("classification"), f"{prefix}.classification"
            )
            if classification not in CLASSIFICATIONS:
                raise CheckFailure(f"{prefix}.classification is unsupported: {classification}")
            if entry_id in entry_ids:
                raise CheckFailure(f"{prefix}.id duplicates {entry_id!r}")
            if previous_id and entry_id < previous_id:
                raise CheckFailure(f"{prefix}.id must be sorted after {previous_id!r}")
            previous_id = entry_id
            entry_ids.add(entry_id)

            paths = _check_string_list(
                entry.get("paths"), label=f"{prefix}.paths", allowed=None, allow_empty=False
            )
            _check_string_list(
                entry.get("languages"),
                label=f"{prefix}.languages",
                allowed=LANGUAGES,
                allow_empty=False,
            )
            _check_string_list(
                entry.get("platforms"),
                label=f"{prefix}.platforms",
                allowed=PLATFORMS,
                allow_empty=False,
            )
            risk = _check_nonempty_string(entry.get("risk"), f"{prefix}.risk")
            if risk not in RISKS:
                raise CheckFailure(f"{prefix}.risk is unsupported: {risk}")
            side_effects = _check_string_list(
                entry.get("side_effects"),
                label=f"{prefix}.side_effects",
                allowed=SIDE_EFFECTS,
                allow_empty=False,
            )
            if "none" in side_effects and len(side_effects) != 1:
                raise CheckFailure(f"{prefix}.side_effects cannot combine 'none' with other values")
            _check_string_list(
                entry.get("test_layers"),
                label=f"{prefix}.test_layers",
                allowed=TEST_LAYERS,
                allow_empty=True,
            )

            owner = entry.get("owner")
            if not isinstance(owner, dict):
                raise CheckFailure(f"{prefix}.owner must be an object")
            owner_kind = _check_nonempty_string(owner.get("kind"), f"{prefix}.owner.kind")
            if owner_kind not in OWNER_KINDS:
                raise CheckFailure(f"{prefix}.owner.kind is unsupported: {owner_kind}")
            _check_nonempty_string(owner.get("name"), f"{prefix}.owner.name")
            if "source" in owner:
                _check_nonempty_string(owner["source"], f"{prefix}.owner.source")

            coverage = entry.get("coverage")
            if not isinstance(coverage, dict):
                raise CheckFailure(f"{prefix}.coverage must be an object")
            status = _check_nonempty_string(coverage.get("status"), f"{prefix}.coverage.status")
            if status not in COVERAGE_STATUSES:
                raise CheckFailure(f"{prefix}.coverage.status is unsupported: {status}")
            suite_id = coverage.get("suite_id")
            work_item = coverage.get("work_item")
            test_paths = _check_string_list(
                coverage.get("test_paths"),
                label=f"{prefix}.coverage.test_paths",
                allowed=None,
                allow_empty=True,
            )
            if classification == "owned":
                _check_nonempty_string(suite_id, f"{prefix}.coverage.suite_id")
                _check_nonempty_string(work_item, f"{prefix}.coverage.work_item")
                if status == "not-applicable":
                    raise CheckFailure(f"{prefix}.coverage.status cannot be not-applicable for owned automation")
            elif classification != "owned":
                _check_nonempty_string(entry.get("rationale"), f"{prefix}.rationale")
            if status in {"covered", "partial"} and not test_paths:
                raise CheckFailure(f"{prefix}.coverage.test_paths must not be empty for {status} coverage")
            for test_path in test_paths:
                if test_path not in tracked_paths:
                    errors.append(f"{prefix}.coverage.test_paths references untracked path {test_path!r}")

            for selector in paths:
                has_glob = "*" in selector or "?" in selector
                matches = sorted(
                    path for path in candidate_paths if selector_regex(selector).fullmatch(path)
                )
                if not matches:
                    if not has_glob and selector not in tracked_paths:
                        errors.append(f"{prefix}.paths contains untracked path {selector!r}")
                    elif not has_glob and selector not in candidate_paths:
                        errors.append(f"{prefix}.paths contains non-candidate path {selector!r}")
                    else:
                        errors.append(f"{prefix}.paths selector matches no candidates: {selector!r}")
                    continue
                for path in matches:
                    if path in classified:
                        errors.append(
                            f"{prefix}.paths duplicates candidate {path!r} already classified by {classified[path]!r}"
                        )
                        continue
                    classified[path] = entry_id
                    classifications.append((path, classification))
        except CheckFailure as error:
            errors.append(str(error))

    for path in sorted(candidate_paths - set(classified)):
        reasons = ", ".join(next(item.reasons for item in candidates if item.path == path))
        errors.append(f"unclassified automation candidate {path!r} ({reasons})")

    return CheckResult(tuple(errors), candidates, tuple(sorted(classifications)))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: parent of assets/)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="inventory path, absolute or relative to --repo-root",
    )
    parser.add_argument(
        "--list-candidates",
        action="store_true",
        help="list tracked candidates and discovery reasons without reading the manifest",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    root = args.repo_root.resolve()
    if args.list_candidates:
        try:
            candidates = discover_candidates(root, tracked_files(root))
        except CheckFailure as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1
        for candidate in candidates:
            print(f"{candidate.path}\t{','.join(candidate.reasons)}")
        print(f"Automation inventory candidates: {len(candidates)}")
        print(f"Candidate digest: {candidate_digest(candidates)}")
        return 0

    result = check_repository(root, args.manifest)
    if result.errors:
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    counts = Counter(classification for _, classification in result.classifications)
    summary = ", ".join(f"{name}={counts[name]}" for name in sorted(counts))
    print(f"Automation inventory OK: {len(result.candidates)} candidates ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
