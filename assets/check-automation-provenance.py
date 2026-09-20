#!/usr/bin/env python3
"""Verify committed generated, mirrored, and vendored automation provenance."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


SCHEMA_REF = "./schemas/automation-provenance.v2.schema.json"
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


class CheckFailure(RuntimeError):
    """A deterministic policy or repository validation failure."""


@dataclass(frozen=True)
class TrackedFile:
    path: str
    mode: str


@dataclass(frozen=True)
class CheckResult:
    errors: tuple[str, ...]
    summaries: tuple[str, ...]


def fail(message: str) -> NoReturn:
    raise CheckFailure(message)


def load_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {label} {path}: {error}")


def strip_jsonc_comments(text: str) -> str:
    output: list[str] = []
    in_string = False
    quote = ""
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            quote = char
            output.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and following == "*":
            index += 2
            while index + 1 < len(text) and text[index : index + 2] != "*/":
                index += 1
            if index + 1 >= len(text):
                fail("unterminated block comment in JSONC manifest")
            index += 2
            continue
        output.append(char)
        index += 1
    return "".join(output)


def strip_jsonc_trailing_commas(text: str) -> str:
    output: list[str] = []
    in_string = False
    quote = ""
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            quote = char
            output.append(char)
            index += 1
            continue
        if char == ",":
            following = index + 1
            while following < len(text) and text[following] in " \t\r\n":
                following += 1
            if following < len(text) and text[following] in "]}":
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def load_jsonc(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(strip_jsonc_trailing_commas(strip_jsonc_comments(text)))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read JSONC manifest {path}: {error}")


def run_git(
    repo_root: Path, *arguments: str, input: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            input=input,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        fail(f"cannot execute Git: {error}")
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        fail(f"git {' '.join(arguments)} failed: {message}")
    return result


def tracked_files(repo_root: Path) -> dict[str, TrackedFile]:
    output = run_git(repo_root, "ls-files", "--stage", "-z").stdout
    tracked: dict[str, TrackedFile] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            fail("unexpected git ls-files --stage output")
        mode = fields[0].decode("ascii")
        path = raw_path.decode("utf-8", "surrogateescape")
        if path in tracked:
            fail(f"Git index has multiple stages for {path!r}")
        tracked[path] = TrackedFile(path, mode)
    return tracked


def safe_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{label} must be a non-empty repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value.startswith("./"):
        fail(f"{label} contains unsafe path {value!r}")
    return value


def string_list(
    value: object,
    label: str,
    *,
    paths: bool = False,
    allow_empty: bool = False,
    require_sorted: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        fail(f"{label} must be {'an' if allow_empty else 'a non-empty'} array")
    items: list[str] = []
    for index, item in enumerate(value):
        if paths:
            items.append(safe_path(item, f"{label}[{index}]"))
        elif not isinstance(item, str) or not item:
            fail(f"{label}[{index}] must be a non-empty string")
        else:
            items.append(item)
    if len(items) != len(set(items)):
        fail(f"{label} must not contain duplicates")
    if require_sorted and items != sorted(items):
        fail(f"{label} must be sorted")
    return tuple(items)


def object_value(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def require_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    actual = set(value)
    missing = keys - actual
    extra = actual - keys
    if missing:
        fail(f"{label} is missing key(s): {', '.join(sorted(missing))}")
    if extra:
        fail(f"{label} has unsupported key(s): {', '.join(sorted(extra))}")


def digest_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def file_payload(path: Path) -> bytes:
    try:
        if path.is_symlink():
            return os.readlink(path).encode("utf-8", "surrogateescape")
        return path.read_bytes()
    except OSError as error:
        fail(f"cannot read {path}: {error}")


def normalized_payload(repo_root: Path, tracked: TrackedFile) -> bytes:
    path = repo_root / tracked.path
    payload = file_payload(path)
    if tracked.mode == "120000" or path.is_symlink() or b"\r\n" not in payload:
        return payload
    normalized = payload.replace(b"\r\n", b"\n")
    cleaned_id = run_git(
        repo_root, "hash-object", f"--path={tracked.path}", "--stdin", input=payload
    ).stdout
    normalized_id = run_git(
        repo_root, "hash-object", "--no-filters", "--stdin", input=normalized
    ).stdout
    return normalized if cleaned_id == normalized_id else payload


def load_policy(repo_root: Path, policy_path: Path) -> dict[str, Any]:
    payload = object_value(load_json(policy_path, label="provenance policy"), "policy")
    require_keys(
        payload,
        {
            "$schema",
            "schema_version",
            "generated",
            "mirrors",
            "espanso",
            "statusline",
            "unslop",
            "forbidden_behavioral_roots",
        },
        "policy",
    )
    if payload.get("$schema") != SCHEMA_REF:
        fail(f"policy $schema must be {SCHEMA_REF!r}")
    if payload.get("schema_version") != 2:
        fail("policy schema_version must be 2")
    schema_path = (policy_path.parent / SCHEMA_REF).resolve()
    try:
        schema_path.relative_to(repo_root.resolve())
    except ValueError:
        fail("policy $schema resolves outside the repository")
    if not schema_path.is_file():
        fail(f"policy schema is missing: {schema_path}")
    return payload


def check_generated(
    repo_root: Path,
    tracked: dict[str, TrackedFile],
    section: object,
) -> str:
    generated = object_value(section, "generated")
    require_keys(generated, {"manifest", "accepted_divergences"}, "generated")
    manifest_path = safe_path(generated["manifest"], "generated.manifest")
    manifest = object_value(
        load_json(repo_root / manifest_path, label="generated manifest"), "generated manifest"
    )
    if manifest.get("schemaVersion") != 1 or not isinstance(manifest.get("files"), list):
        fail("generated manifest must have schemaVersion 1 and a files array")

    raw_exceptions = generated["accepted_divergences"]
    if not isinstance(raw_exceptions, list):
        fail("generated.accepted_divergences must be an array")
    exceptions: dict[str, dict[str, Any]] = {}
    previous_path = ""
    for index, raw in enumerate(raw_exceptions):
        exception = object_value(raw, f"generated.accepted_divergences[{index}]")
        require_keys(
            exception,
            {"path", "accepted_digest", "required_markers", "rationale"},
            f"generated.accepted_divergences[{index}]",
        )
        path = safe_path(exception["path"], f"generated.accepted_divergences[{index}].path")
        if previous_path and path < previous_path:
            fail("generated.accepted_divergences must be sorted by path")
        previous_path = path
        if path in exceptions:
            fail(f"generated exception duplicates {path!r}")
        digest = exception["accepted_digest"]
        if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
            fail(f"generated exception {path!r} has an invalid accepted_digest")
        string_list(exception["required_markers"], f"generated exception {path!r} markers")
        if not isinstance(exception["rationale"], str) or not exception["rationale"].strip():
            fail(f"generated exception {path!r} needs a rationale")
        exceptions[path] = exception

    seen: set[str] = set()
    accepted: set[str] = set()
    for index, raw in enumerate(manifest["files"]):
        entry = object_value(raw, f"generated manifest files[{index}]")
        for field in ("path", "source", "sourceDigest", "digest"):
            if field not in entry:
                fail(f"generated manifest files[{index}] is missing {field}")
        path = safe_path(entry["path"], f"generated manifest files[{index}].path")
        if path in seen:
            fail(f"generated manifest path is duplicated: {path}")
        seen.add(path)
        if path not in tracked:
            fail(f"generated manifest path is not tracked: {path}")
        expected = entry["digest"]
        source_digest = entry["sourceDigest"]
        if not isinstance(expected, str) or not DIGEST_PATTERN.fullmatch(expected):
            fail(f"generated manifest digest is invalid for {path}")
        if not isinstance(source_digest, str) or not DIGEST_PATTERN.fullmatch(source_digest):
            fail(f"generated manifest sourceDigest is invalid for {path}")
        if not isinstance(entry["source"], str) or not entry["source"]:
            fail(f"generated manifest source is invalid for {path}")
        payload = normalized_payload(repo_root, tracked[path])
        actual = digest_bytes(payload)
        exception = exceptions.get(path)
        if actual == expected:
            if exception is not None:
                fail(f"generated exception is stale because {path} matches its manifest")
            continue
        if exception is None:
            fail(f"generated output drifted from its manifest: {path}")
        if actual != exception["accepted_digest"]:
            fail(f"accepted generated output changed: {path} is {actual}")
        text = payload.decode("utf-8", "replace")
        source_marker = f"source: {entry['source']}"
        digest_marker = f"sourceDigest: {source_digest}"
        if source_marker not in text or digest_marker not in text:
            fail(f"accepted generated output lost source provenance markers: {path}")
        for marker in exception["required_markers"]:
            if marker not in text:
                fail(f"accepted generated output {path} is missing marker {marker!r}")
        accepted.add(path)

    unused = set(exceptions) - seen
    if unused:
        fail(f"generated exception path is absent from manifest: {', '.join(sorted(unused))}")
    return f"generated: verified (accepted exceptions: {', '.join(sorted(accepted)) or 'none'})"


def path_matches(path: str, patterns: tuple[str, ...]) -> bool:
    posix = PurePosixPath(path).as_posix()
    return any(fnmatch.fnmatch(posix, pattern) for pattern in patterns)


def check_mirrors(
    repo_root: Path,
    tracked: dict[str, TrackedFile],
    section: object,
) -> str:
    mirrors_policy = object_value(section, "mirrors")
    require_keys(mirrors_policy, {"manifest"}, "mirrors")
    manifest_path = safe_path(mirrors_policy["manifest"], "mirrors.manifest")
    manifest = object_value(load_jsonc(repo_root / manifest_path), "mirror manifest")
    schema_ref = "./schemas/devcontainer-sync.v1.schema.json"
    if manifest.get("$schema") != schema_ref:
        fail(f"mirror manifest $schema must be {schema_ref!r}")
    if manifest.get("schema_version") != 1:
        fail("mirror manifest schema_version must be 1")
    try:
        raw_mirrors = manifest["shared"]["mirrors"]
    except (KeyError, TypeError):
        fail("mirror manifest is missing shared.mirrors")
    if not isinstance(raw_mirrors, list) or not raw_mirrors:
        fail("mirror manifest shared.mirrors must be a non-empty array")

    target_owners: dict[str, str] = {}
    for index, raw in enumerate(raw_mirrors):
        spec = object_value(raw, f"mirror[{index}]")
        name = spec.get("name")
        if not isinstance(name, str) or not name:
            fail(f"mirror[{index}].name must be a non-empty string")
        source_root = safe_path(spec.get("source_root"), f"mirror {name}.source_root").rstrip("/")
        target_root = safe_path(spec.get("target_root"), f"mirror {name}.target_root").rstrip("/")
        include = string_list(
            spec.get("include"), f"mirror {name}.include", require_sorted=False
        )
        exclude = string_list(
            spec.get("exclude", []),
            f"mirror {name}.exclude",
            allow_empty=True,
            require_sorted=False,
        )
        cleanup = spec.get("cleanup_managed", False)
        if not isinstance(cleanup, bool):
            fail(f"mirror {name}.cleanup_managed must be boolean")
        source_prefix = source_root + "/"
        target_prefix = target_root + "/"
        managed: set[str] = set()
        for source_path in sorted(path for path in tracked if path.startswith(source_prefix)):
            relative = source_path[len(source_prefix) :]
            if not path_matches(relative, include) or path_matches(relative, exclude):
                continue
            managed.add(relative)
            target_path = target_prefix + relative
            previous_owner = target_owners.get(target_path)
            if previous_owner is not None:
                fail(f"mirror target {target_path} is owned by both {previous_owner} and {name}")
            target_owners[target_path] = name
            if target_path not in tracked:
                fail(f"mirror target is missing from Git: {target_path}")
            source = tracked[source_path]
            target = tracked[target_path]
            if source.mode != target.mode:
                fail(f"mirror mode drift: {source_path} ({source.mode}) != {target_path} ({target.mode})")
            if file_payload(repo_root / source_path) != file_payload(repo_root / target_path):
                fail(f"mirror content drift: {source_path} != {target_path}")
        if cleanup:
            for target_path in sorted(path for path in tracked if path.startswith(target_prefix)):
                relative = target_path[len(target_prefix) :]
                if path_matches(relative, include) and not path_matches(relative, exclude) and relative not in managed:
                    fail(f"cleanup-managed mirror has stale tracked target: {target_path}")
    return "mirrors: tracked mappings verified"


def check_espanso(repo_root: Path, tracked: dict[str, TrackedFile], section: object) -> str:
    espanso = object_value(section, "espanso")
    require_keys(espanso, {"chains"}, "espanso")
    chains = espanso["chains"]
    if not isinstance(chains, list) or not chains:
        fail("espanso.chains must be a non-empty array")
    previous_source = ""
    for index, raw in enumerate(chains):
        chain = object_value(raw, f"espanso.chains[{index}]")
        require_keys(chain, {"source", "template", "renders"}, f"espanso.chains[{index}]")
        source = safe_path(chain["source"], f"espanso.chains[{index}].source")
        template = safe_path(chain["template"], f"espanso.chains[{index}].template")
        renders = string_list(chain["renders"], f"espanso.chains[{index}].renders", paths=True)
        if previous_source and source < previous_source:
            fail("espanso.chains must be sorted by source")
        previous_source = source
        for path in (source, template, *renders):
            if path not in tracked:
                fail(f"Espanso provenance path is not tracked: {path}")
        expected_include = '{{- include "' + source + '" -}}'
        if (repo_root / template).read_text(encoding="utf-8").strip() != expected_include:
            fail(f"Espanso shared template does not include its canonical source: {template}")
        template_name = template.removeprefix(".chezmoitemplates/")
        expected_render = '{{- template "' + template_name + '" . -}}'
        for render in renders:
            if (repo_root / render).read_text(encoding="utf-8").strip() != expected_render:
                fail(f"Espanso platform wrapper does not delegate to shared template: {render}")
    return "espanso: template chains verified"


def check_statusline(repo_root: Path, tracked: dict[str, TrackedFile], section: object) -> str:
    statusline = object_value(section, "statusline")
    require_keys(statusline, {"copies", "sync_command"}, "statusline")
    copies = string_list(statusline["copies"], "statusline.copies", paths=True)
    if len(copies) != 2:
        fail("statusline.copies must contain exactly two paths")
    payloads: list[bytes] = []
    versions: list[str] = []
    version_pattern = re.compile(rb'^CLAUDE_PACE_VERSION="(v[^"]+)"\r?$', re.MULTILINE)
    for path in copies:
        if path not in tracked:
            fail(f"statusline copy is not tracked: {path}")
        payload = normalized_payload(repo_root, tracked[path])
        payloads.append(payload)
        matches = version_pattern.findall(payload)
        if len(matches) != 1:
            fail(f"statusline copy must declare exactly one CLAUDE_PACE_VERSION: {path}")
        versions.append(matches[0].decode("utf-8"))
    if payloads[0] != payloads[1]:
        fail("statusline vendored copies differ")
    if versions[0] != versions[1]:
        fail("statusline vendored versions differ")
    command = string_list(
        statusline["sync_command"], "statusline.sync_command", require_sorted=False
    )
    if command != ("assets/sync-statusline.sh", "--check"):
        fail("statusline.sync_command must use assets/sync-statusline.sh --check")
    if command[0] not in tracked or tracked[command[0]].mode != "100755":
        fail("statusline sync command must be a tracked executable")
    return f"statusline: local copies verified at {versions[0]}"


def tracked_tree(
    repo_root: Path,
    tracked: dict[str, TrackedFile],
    root: str,
) -> tuple[dict[str, bytes], str]:
    prefix = root.rstrip("/") + "/"
    files = sorted(path for path in tracked if path.startswith(prefix))
    if not files:
        fail(f"tracked tree has no files: {root}")
    payloads: dict[str, bytes] = {}
    digest = hashlib.sha256()
    for tracked_path in files:
        name = tracked_path[len(prefix) :]
        payload = normalized_payload(repo_root, tracked[tracked_path])
        payloads[name] = payload
        digest.update((name + "\0").encode("utf-8"))
        digest.update(payload)
    return payloads, "sha256:" + digest.hexdigest()


def check_unslop(
    repo_root: Path,
    tracked: dict[str, TrackedFile],
    section: object,
) -> str:
    unslop = object_value(section, "unslop")
    require_keys(
        unslop,
        {"canonical_root", "copies", "entrypoints", "snapshot", "tree_digest"},
        "unslop",
    )
    canonical_root = safe_path(unslop["canonical_root"], "unslop.canonical_root")
    copies = string_list(unslop["copies"], "unslop.copies", paths=True)
    entrypoints = string_list(unslop["entrypoints"], "unslop.entrypoints")
    snapshot = safe_path(unslop["snapshot"], "unslop.snapshot")
    tree_digest = unslop["tree_digest"]
    if not isinstance(tree_digest, str) or not DIGEST_PATTERN.fullmatch(tree_digest):
        fail("unslop.tree_digest must be a sha256 digest")
    if not (repo_root / snapshot).is_file():
        fail(f"unslop source snapshot is missing: {snapshot}")
    canonical, actual_digest = tracked_tree(repo_root, tracked, canonical_root)
    if actual_digest != tree_digest:
        fail(f"unslop canonical snapshot changed: expected {tree_digest}, found {actual_digest}")
    for entrypoint in entrypoints:
        if entrypoint not in canonical:
            fail(f"unslop canonical tree is missing entrypoint: {entrypoint}")
    for copy_root in copies:
        copy, _ = tracked_tree(repo_root, tracked, copy_root)
        if copy.keys() != canonical.keys():
            missing = sorted(canonical.keys() - copy.keys())
            extra = sorted(copy.keys() - canonical.keys())
            fail(f"unslop tree membership drift at {copy_root}; missing={missing}, extra={extra}")
        for name, payload in canonical.items():
            if copy[name] != payload:
                fail(f"unslop content drift: {copy_root}/{name}")
    return "unslop: snapshot and copies verified"


def check_forbidden_roots(
    repo_root: Path,
    tracked: dict[str, TrackedFile],
    roots_value: object,
) -> str:
    roots = string_list(roots_value, "forbidden_behavioral_roots", paths=True)
    for root in roots:
        if not root.endswith("/"):
            fail(f"forbidden behavioral root must end with '/': {root}")
    registry = object_value(
        load_json(repo_root / "configs/test-suites.json", label="test suite registry"),
        "test suite registry",
    )
    steps = registry.get("steps")
    if not isinstance(steps, list):
        fail("test suite registry steps must be an array")
    for index, raw in enumerate(steps):
        step = object_value(raw, f"test suite step[{index}]")
        for path in step.get("covers", []):
            if isinstance(path, str) and any(path.startswith(root) for root in roots):
                fail(f"test suite {step.get('id')!r} covers forbidden root path {path}")
        for argument in step.get("argv", []):
            if isinstance(argument, str) and any(root in argument for root in roots):
                fail(f"test suite {step.get('id')!r} executes forbidden root {argument}")

    inventory = object_value(
        load_json(repo_root / "configs/automation-test-inventory.json", label="automation inventory"),
        "automation inventory",
    )
    entries = inventory.get("entries")
    if not isinstance(entries, list):
        fail("automation inventory entries must be an array")
    archive_owned = any(
        isinstance(entry, dict)
        and entry.get("classification") == "archived"
        and "archive/**" in entry.get("paths", [])
        for entry in entries
    )
    if "archive/" in roots and not archive_owned:
        fail("automation inventory must classify archive/** as archived")
    tracked_node_modules = sorted(
        path for path in tracked if path.startswith(".opencode/node_modules/")
    )
    if tracked_node_modules:
        fail("tracked .opencode/node_modules automation is forbidden")
    return "behavioral roots: exclusions verified"


def check_repository(repo_root: Path, policy_path: Path | None = None) -> CheckResult:
    root = repo_root.resolve()
    selected_policy = policy_path or root / "configs/automation-provenance.json"
    if not selected_policy.is_absolute():
        selected_policy = root / selected_policy
    try:
        tracked = tracked_files(root)
        policy = load_policy(root, selected_policy)
    except CheckFailure as error:
        return CheckResult((str(error),), ())
    errors: list[str] = []
    summaries: list[str] = []
    for check, section in (
        (check_generated, "generated"),
        (check_mirrors, "mirrors"),
        (check_espanso, "espanso"),
        (check_statusline, "statusline"),
        (check_unslop, "unslop"),
        (check_forbidden_roots, "forbidden_behavioral_roots"),
    ):
        try:
            summaries.append(check(root, tracked, policy[section]))
        except CheckFailure as error:
            errors.append(str(error))
    return CheckResult(tuple(errors), tuple(summaries))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify generated, mirrored, and vendored automation provenance."
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--policy", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = check_repository(args.repo_root, args.policy)
    if result.errors:
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for summary in result.summaries:
        print(summary)
    print("Automation provenance OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
