"""Validate inactive, pinned OpenCode v2 preparation without changing files."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


BASELINE = "9100cf156e76e4dec337de3d8e0d5d9df7fc7821"
AREA = "configs/opencode-v2"
INVENTORY = f"{AREA}/source/inventory.json"
CONTRACT = f"{AREA}/source/consumer-contract.jsonc"
DESCRIPTOR = f"{AREA}/source/preparation.jsonc"
EVIDENCE = f"{AREA}/source/owner-evidence"
OWNER_MANIFEST = f"{EVIDENCE}/metadata/preparation-manifest.json"
PAYLOAD = f"{AREA}/prepared/owner"
SUBSET = f"{AREA}/prepared/metadata/subset-manifest.json"
CHECKER = "assets/check-opencode-v2-preparation.py"
TESTS = "tests/test_opencode_v2_preparation.py"
LEDGER = "docs/agents/opencode-v2-preparation.md"
REGISTRIES = (
    "configs/test-suites.json",
    "configs/automation-test-inventory.json",
    "configs/automation-candidates.txt",
)
FROZEN_DIGESTS = {
    INVENTORY: "926db2206d525b61129ea290058c728a6f31ccc6e9d128baaeb0222cfa6e9d7e",
    CONTRACT: "e4ad61fd61a6b54fc7058665989fa7dd9cdeb86271959b7c9e3d5daebc798f57",
}
ACTIVE_ROOTS = (
    ".opencode/",
    "private_dot_config/opencode/",
    "dot_claude/",
    "private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/",
    "private_Documents/development/container-dotfiles/dotfiles/dot_claude/",
)
MANAGED_ASSETS = {
    "AGENTS.md", "agents", "commands", "opencode-notifier.json",
    "opencode-profile.sh", "opencode-quota", "opencode.jsonc", "plugins",
    "profiles", "prompts", "skills", "tui.json",
}


class CheckFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def document(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def decode_document(data: bytes) -> dict:
    text = data.decode("utf-8")
    cleaned = []
    quoted = escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quoted:
            cleaned.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            cleaned.append(char)
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            require(end >= 0, "unterminated JSONC comment")
            cleaned.append(" " * (end + 2 - index))
            index = end + 2
            continue
        else:
            cleaned.append(char)
        index += 1
    text = "".join(cleaned)
    cleaned = []
    quoted = escaped = False
    for index, char in enumerate(text):
        if quoted:
            cleaned.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            cleaned.append(char)
        elif char == "," and text[index + 1:].lstrip().startswith(("}", "]")):
            continue
        else:
            cleaned.append(char)

    def pairs(items: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise CheckFailure("non-finite JSON constant")

    result = json.loads("".join(cleaned), object_pairs_hook=pairs, parse_constant=constant)
    require(isinstance(result, dict), "expected a JSON object")
    return result


def relative_name(name: str) -> str:
    require(isinstance(name, str) and bool(name), "empty or non-string path")
    require(not name.startswith("/") and "\\" not in name and ":" not in name,
            f"non-portable relative path: {name}")
    parts = name.split("/")
    require(all(part and part not in (".", "..") and part == part.rstrip(" .")
                and not re.search(r"[\x00-\x1f]", part) for part in parts),
            f"unsafe relative path: {name}")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}
    require(not any(part.split(".")[0].casefold() in reserved for part in parts),
            f"reserved Windows path: {name}")
    return name


def public_name(name: str) -> bool:
    parts = (part.casefold() for part in PurePosixPath(name).parts)
    return not any(
        part in {".ssh", ".aws", ".gnupg", ".kube", "node_modules", ".git"}
        or part.startswith(".env") or part.endswith(".env") or ".env." in part
        or part in {"auth.json", "credentials.json", "credentials", "secrets.json"}
        for part in parts
    )


def checked_root(root: Path) -> Path:
    require(".." not in root.parts, "root traversal is not allowed")
    root = root.absolute()
    for component in reversed((root, *root.parents)):
        require(not component.is_symlink(), f"symlink ancestor: {component.name}")
        require(component.is_dir(), f"missing root ancestor: {component.name}")
    require(not any(part.casefold() in {".opencode", ".claude", ".agents", "dot_claude"}
                    for part in root.parts),
             "preparation root is inside a discovery directory")
    require(not any(a.casefold() in {".config", "private_dot_config"} and b.casefold() == "opencode"
                    for a, b in zip(root.parts, root.parts[1:])),
            "preparation root is inside an active configuration directory")
    return root


def safe_path(root: Path, name: str, *, link_leaf: bool = False) -> Path:
    relative_name(name)
    require(public_name(name), f"non-public input refused: {name}")
    current = checked_root(root)
    parts = name.split("/")
    for index, part in enumerate(parts):
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            require(link_leaf and index == len(parts) - 1, f"symlink input refused: {name}")
        elif index < len(parts) - 1:
            require(stat.S_ISDIR(info.st_mode), f"non-directory ancestor: {name}")
    return current


def read_bytes(root: Path, name: str) -> bytes:
    file_path = safe_path(root, name)
    require(file_path.is_file(), f"not a regular file: {name}")
    return file_path.read_bytes()


def tree_files(root: Path, name: str) -> set[str]:
    directory = safe_path(root, name)
    require(directory.is_dir(), f"not a directory: {name}")
    found = set()
    aliases = set()
    for entry in directory.iterdir():
        child = f"{name}/{entry.name}"
        relative_name(child)
        require(entry.name.casefold() not in aliases, f"case-alias collision: {child}")
        aliases.add(entry.name.casefold())
        require(not entry.is_symlink(), f"symlink input refused: {child}")
        if entry.is_dir():
            found.update(tree_files(root, child))
        else:
            require(entry.is_file(), f"special file refused: {child}")
            found.add(child)
    return found


def git(root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    environment = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *arguments], input=input_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=120,
        check=False,
    )
    require(result.returncode == 0, f"Git query failed: {arguments[0]}")
    return result.stdout


@dataclass(frozen=True)
class TrustedContext:
    inventory: dict
    contract: dict
    frozen: dict[str, bytes]
    baseline: dict[str, tuple[str, bytes]]


def protected(name: str, inventory: dict) -> bool:
    explicit = set(inventory["historicalConsumerSnapshot"]["sha256ByPath"])
    explicit.update(inventory["additionalCurrentSha256ByPath"])
    explicit.update({".gitattributes", "tests/test_repo_ops.py", LEDGER, *REGISTRIES})
    return public_name(name) and (name in explicit or name.startswith(ACTIVE_ROOTS))


def load_context(root: Path) -> TrustedContext:
    checked_root(root)
    frozen = {name: git(root, "show", f"{BASELINE}:{name}") for name in FROZEN_DIGESTS}
    for name, value in frozen.items():
        require(digest(value) == FROZEN_DIGESTS[name], f"baseline authority mismatch: {name}")
    inventory, contract = (decode_document(frozen[name]) for name in (INVENTORY, CONTRACT))
    selected = {}
    for entry in git(root, "ls-tree", "-rz", BASELINE).split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        name = raw_name.decode()
        if kind == "blob" and protected(name, inventory):
            relative_name(name)
            selected[name] = (mode, oid)
    requested = b"".join((oid + "\n").encode() for _, oid in selected.values())
    batch = git(root, "cat-file", "--batch", input_bytes=requested)
    offset = 0
    baseline = {}
    for name, (mode, oid) in selected.items():
        end = batch.index(b"\n", offset)
        header = batch[offset:end].decode().split()
        require(header[:2] == [oid, "blob"], "invalid baseline Git object")
        size = int(header[2])
        baseline[name] = (mode, batch[end + 1:end + 1 + size])
        offset = end + size + 2
    return TrustedContext(inventory, contract, frozen, baseline)


def payload_rows(context: TrustedContext) -> list[dict]:
    rows = []
    for selected in context.inventory["selection"]["files"]:
        prefix = f"reference-{context.inventory['references']['nativeReference']['version']}/"
        require(selected["ownerManifestPath"].startswith(prefix), "unexpected owner reference path")
        destination = f"{PAYLOAD}/{selected['ownerManifestPath'][len(prefix):]}"
        relative_name(destination)
        rows.append({"path": destination, **selected})
    return rows


def evidence_rows(context: TrustedContext) -> list[dict]:
    rows = [{"path": OWNER_MANIFEST, **context.inventory["producerEvidence"]["manifest"]}]
    rows[0]["path"] = OWNER_MANIFEST
    rows.extend({"path": f"{EVIDENCE}/{row['source']}", "digest": row["sourceDigest"]}
                for row in payload_rows(context) if row["source"].startswith("agents/"))
    return rows


def overlay_inputs(context: TrustedContext) -> list[dict]:
    rows = []
    seen = set()
    for surface in context.contract["surfaces"]:
        names = list(surface.get("sources", []))
        for key in ("configuration", "source"):
            if key in surface:
                names.append(surface[key])
        for name in names:
            if "/" not in name and "root" in surface:
                name = f"{surface['root']}/{name}"
            if name in context.baseline and name not in seen:
                rows.append({"surface": surface["id"], "path": name,
                             "digest": digest(context.baseline[name][1])})
                seen.add(name)
        for prefix in surface.get("roots", []):
            for name in sorted(context.baseline):
                if name.startswith(prefix + "/") and name not in seen:
                    rows.append({"surface": surface["id"], "path": name,
                                 "digest": digest(context.baseline[name][1])})
                    seen.add(name)
    return rows


def expected_descriptor(context: TrustedContext) -> dict:
    inventory = context.inventory
    return {
        "schemaVersion": 1, "format": "dotfiles-opencode-v2-preparation",
        "phase": "DV2-020", "purpose": "inactive-selected-base-preparation", "issue": "dots-0lqe",
        "consumerBaseline": BASELINE, "ownerCommit": inventory["references"]["owner"]["commit"],
        "nativeReference": inventory["references"]["nativeReference"],
        "operationalReady": False, "operationalAggregate": "withheld",
        "registrationAllowed": False, "activationAllowed": False,
        "payloadFiles": 14, "evidenceFiles": 3, "actualProducingBuild": None,
        "frozenInputs": [{"path": name, "digest": digest(context.frozen[name])}
                         for name in (INVENTORY, CONTRACT)],
        "evidence": [{"path": row["path"], "digest": row["digest"]}
                     for row in evidence_rows(context)],
        "selectedOwnerPaths": [row["ownerManifestPath"] for row in payload_rows(context)],
        "overlayInputs": overlay_inputs(context), "overlaysAppliedToPayload": False,
        "transforms": ["select exact committed owner output", "preserve emitted bytes without rewriting"],
        "preservationTreeDigest": digest(canonical({name: [mode, digest(blob)]
                                                  for name, (mode, blob) in context.baseline.items()})),
        "withheldDependencies": inventory["selection"]["withheldDependencies"],
        "proofLimits": ["selected closure, not a runnable agent or the full owner package",
                        "offline consumer models, not native engine or live helper qualification",
                        "actual producer build remains unknown", "native Windows runtime not established"],
    }


def expected_subset(context: TrustedContext, owner: dict, descriptor: bytes) -> dict:
    input_rows = [{"path": name, "digest": digest(context.frozen[name])}
                  for name in (INVENTORY, CONTRACT)]
    input_rows.extend({"path": row["path"], "digest": row["digest"]}
                      for row in evidence_rows(context))
    input_rows.append({"path": DESCRIPTOR, "digest": digest(descriptor)})
    return {
        "schemaVersion": 1, "format": "dotfiles-opencode-v2-subset-manifest", "phase": "DV2-020",
        "purpose": "inactive-selected-base-evidence", "consumerBaseline": BASELINE,
        "ownerCommit": context.inventory["references"]["owner"]["commit"],
        "ownerManifest": context.inventory["producerEvidence"]["manifest"],
        "ownerManifestEvidence": OWNER_MANIFEST, "producerEvidence": context.inventory["producerEvidence"],
        "nativeReference": context.inventory["references"]["nativeReference"],
        "orderedConsumerInputs": input_rows, "ownerInputAuthority": f"{OWNER_MANIFEST}#/inputs",
        "orderedOwnerInputsDigest": digest(canonical(owner["inputs"])),
        "ownerProjectionAuthority": f"{OWNER_MANIFEST}#/projections",
        "orderedOwnerProjectionsDigest": digest(canonical(owner["projections"])),
        "overlayInputs": overlay_inputs(context), "overlaysAppliedToPayload": False,
        "files": payload_rows(context), "payloadFiles": 14, "evidenceFiles": 3,
        "selectedBundleFiles": {"prompt-engineer": 4, "prompt-optimizer": 3,
                                "skill-creator": 3, "skill-reviewer": 2},
        "operationalRows": owner["dispositions"]["operational"]["agents"],
        "operationalAggregate": owner["dispositions"]["operational"]["aggregate"],
        "withheldDependencies": context.inventory["selection"]["withheldDependencies"],
        "operationalReady": False, "overallReady": False, "actualProducingBuild": None,
        "registrationAllowed": False, "activationAllowed": False,
    }


def verify_additive(before: object, after: object, name: str) -> None:
    if isinstance(before, dict):
        require(isinstance(after, dict), f"registration type changed: {name}")
        for key, value in before.items():
            require(key in after, f"registration field removed: {name}")
            verify_additive(value, after[key], name)
    elif isinstance(before, list):
        require(isinstance(after, list), f"registration type changed: {name}")
        remaining = iter(after)
        require(all(any(canonical(value) == canonical(candidate) for candidate in remaining) for value in before),
                f"existing registration changed: {name}")
    else:
        require(canonical(before) == canonical(after), f"existing registration changed: {name}")


def verify_preservation(root: Path, context: TrustedContext) -> None:
    names = {name.decode() for name in git(root, "ls-files", "-z", "--cached", "--others",
                                         "--exclude-standard").split(b"\0") if name}
    current = {name for name in names if protected(name, context.inventory)}
    require(current == set(context.baseline), "protected public source path set changed")
    variances = {row["path"]: row for row in context.inventory["checkoutByteVariance"]}
    for name, (mode, blob) in context.baseline.items():
        file_path = safe_path(root, name, link_leaf=mode == "120000")
        if mode == "120000":
            require(file_path.is_symlink(), f"protected link type changed: {name}")
            value = os.readlink(file_path).encode()
        else:
            require(file_path.is_file(), f"protected source type changed: {name}")
            actual_mode = "100755" if file_path.stat().st_mode & 0o111 else "100644"
            require(actual_mode == mode, f"protected mode changed: {name}")
            value = read_bytes(root, name)
        if name in REGISTRIES:
            if name.endswith(".json"):
                verify_additive(decode_document(blob), decode_document(value), name)
            else:
                require(set(blob.splitlines()) <= set(value.splitlines()), "census entries removed")
        elif name == LEDGER:
            require(value.startswith(blob), "historical ledger changed rather than appended")
        elif value != blob:
            record = variances.get(name, {})
            require(record.get("committedDigest") == digest(blob)
                    and record.get("workingDigest") == digest(value)
                    and value.replace(b"\r\n", b"\n") == blob,
                    f"protected source bytes changed: {name}")
            attributes = git(root, "check-attr", "text", "eol", "--", name).decode()
            require(": text: set" in attributes and ": eol: crlf" in attributes,
                    f"unverified checkout EOL: {name}")


def check_repository(root: Path, trusted: TrustedContext | None = None) -> dict:
    require(os.name == "posix", "preparation checker requires a POSIX checkout")
    root = checked_root(root)
    context = trusted if trusted is not None else load_context(root)
    for name, expected in context.frozen.items():
        require(read_bytes(root, name) == expected, f"frozen authority changed: {name}")
    rows = payload_rows(context)
    require(len(rows) == 14 and len({row["path"] for row in rows}) == 14,
            "selection must contain exactly 14 unique files")
    evidence = evidence_rows(context)
    require(len(evidence) == 3, "selection must have exactly three evidence files")
    expected_paths = {INVENTORY, CONTRACT, DESCRIPTOR, SUBSET}
    expected_paths.update(row["path"] for row in rows + evidence)
    require(tree_files(root, AREA) == expected_paths, "missing, extra or stale preparation files")
    for row in evidence:
        require(digest(read_bytes(root, row["path"])) == row["digest"],
                f"immutable owner evidence mismatch: {row['path']}")
    owner = decode_document(read_bytes(root, OWNER_MANIFEST))
    require(owner["overallReady"] is False, "owner readiness must remain false")
    require(all(row["status"] == "blocked" for row in owner["dispositions"]["operational"]["agents"]),
            "owner operational row is not blocked")
    outputs = {row["path"]: row for row in owner["outputs"]}
    inputs = {row["path"]: row["digest"] for row in owner["inputs"]}
    for row in rows:
        require(outputs.get(row["ownerManifestPath"]) == {key: value for key, value in row.items()
                                                        if key not in {"path", "ownerManifestPath"}}
                | {"path": row["ownerManifestPath"]}, "selection differs from owner output authority")
        require(inputs.get(row["source"]) == row["sourceDigest"], "canonical source input mismatch")
        require(digest(read_bytes(root, row["path"])) == row["digest"],
                f"selected output mismatch: {row['path']}")
        if row["source"].startswith("skills/"):
            require(row["sourceDigest"] == row["digest"], "retained skill is not verbatim")
    descriptor = read_bytes(root, DESCRIPTOR)
    require(canonical(decode_document(descriptor)) == canonical(expected_descriptor(context)),
            "consumer descriptor mismatch")
    require(canonical(decode_document(read_bytes(root, SUBSET)))
            == canonical(expected_subset(context, owner, descriptor)),
            "subset provenance, order, closure or blocked status mismatch")
    verify_preservation(root, context)
    return {"phase": "DV2-020", "payloadFiles": 14, "evidenceFiles": 3,
            "operationalReady": False, "aggregate": "withheld", "status": "PASS",
            "proof": "pinned bytes, selected closure, bounded source preservation; not runtime qualification"}


def merge_objects(base: dict, overlay: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        result[key] = (merge_objects(result[key], value)
                       if isinstance(result.get(key), dict) and isinstance(value, dict)
                       else copy.deepcopy(value))
    return result


def rebase_references(value: object, directory: str) -> object:
    if isinstance(value, str):
        match = re.fullmatch(r"\{file:(\.\.?/[^{}]+)\}", value)
        return "{file:" + posixpath.normpath(posixpath.join(directory, match[1])) + "}" if match else value
    if isinstance(value, dict):
        return {key: rebase_references(child, directory) for key, child in value.items()}
    if isinstance(value, list):
        return [rebase_references(child, directory) for child in value]
    return copy.deepcopy(value)


def normalize_profiles(names: list[str]) -> list[str]:
    result = []
    for name in names or ["defaults"]:
        name = "defaults" if name == "chatgpt" else name
        require(bool(re.fullmatch(r"[A-Za-z0-9_-]+", name)), "invalid profile token")
        if name not in result:
            result.append(name)
    return result


def compose_contract(global_input: tuple[dict, str] | None, defaults: tuple[dict, str],
                     profiles: list[tuple[str, dict, str]], workspace: tuple[dict, str],
                     fallback_models: dict[str, str]) -> dict:
    names = normalize_profiles([row[0] for row in profiles])
    if all(name == "defaults" for name in names):
        return {"route": "normal", "configDir": None, "profiles": names}
    base, directory = global_input if global_input is not None else defaults
    result = rebase_references(base, directory)
    if global_input is not None:
        result = merge_objects(result, rebase_references(*defaults))
    seen = set()
    for name, value, directory in profiles:
        name = "defaults" if name == "chatgpt" else name
        if name != "defaults" and name not in seen:
            result = merge_objects(result, rebase_references(value, directory))
        seen.add(name)
    workspace_value = rebase_references(*workspace)
    if isinstance(workspace_value.get("agent"), dict):
        result = merge_objects(result, {"agent": workspace_value["agent"]})

    def transform(value: object) -> object:
        if isinstance(value, dict):
            output = {key: transform(child) for key, child in value.items()}
            model = output.get("model")
            if isinstance(model, str):
                if "anthropic-api" in names and model.startswith("anthropic/"):
                    model = "anthropic-api/" + model[len("anthropic/"):]
                if "api-fallback" in names:
                    model = fallback_models.get(model, model)
                output["model"] = model
            return output
        if isinstance(value, list):
            return [transform(child) for child in value]
        return value

    return {"route": "composed", "profiles": names, "config": transform(result)}


def runtime_selection(names: list[str], platform: str, composed: bool) -> str:
    names = normalize_profiles(names)
    if all(name == "defaults" for name in names):
        return "normal"
    if composed:
        return "runtime"
    require(platform != "windows" or not {"anthropic-api", "api-fallback"}.intersection(names),
            "Windows runtime-required profile refused")
    return "static-fallback"


def preferred_workspace(names: set[str], *, composer: bool) -> str | None:
    order = ("opencode.jsonc", "opencode.json") if composer else ("opencode.json", "opencode.jsonc")
    return next((name for name in order if name in names), None)


def plan_trial_supported(names: list[str]) -> bool:
    return all(name == "defaults" for name in normalize_profiles(names))


def launcher_arguments(arguments: list[str], *, kind: str, approve_auto: bool) -> list[str]:
    result = list(arguments)
    if kind == "opencode" and approve_auto and "--auto" not in result:
        result.append("--auto")
    return result


def managed_path(name: str) -> bool:
    relative_name(name)
    parts = name.split("/")
    generated = {"node_modules", "package.json", "package-lock.json", "bun.lock", ".gitignore"}
    directories = {"agents", "commands", "opencode-quota", "plugins", "profiles", "prompts", "skills"}
    return (parts[0] in MANAGED_ASSETS and (len(parts) == 1 or parts[0] in directories)
            and not any(part in generated for part in parts))


def materialization_plan(raw_host: set[str] | None, mirror: set[str], previous_owned: set[str],
                         local: set[str], generated: set[str], conflicts: set[str]) -> dict:
    selected = raw_host if raw_host is not None else mirror
    source = {name for name in selected if managed_path(name)}
    return {"route": "raw-host" if raw_host is not None else "fallback-mirror",
            "write": source, "remove": previous_owned - source - generated,
            "preserve": local | generated, "backup": source & conflicts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    try:
        print(json.dumps(check_repository(arguments.root), sort_keys=True))
        return 0
    except (CheckFailure, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
