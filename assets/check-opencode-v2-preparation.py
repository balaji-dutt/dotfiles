"""Validate inactive, pinned OpenCode v2 preparation without changing files."""

from __future__ import annotations

import argparse
import copy
import fnmatch
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
from urllib.parse import unquote, urlsplit


BASELINE = "9100cf156e76e4dec337de3d8e0d5d9df7fc7821"
COMPLETED_PREPARATION = "a417636d7376dd426a811935bc2f7203472bd6ef"
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
    completed: dict[str, tuple[str, bytes]] | None = None


def protected(name: str, inventory: dict) -> bool:
    explicit = set(inventory["historicalConsumerSnapshot"]["sha256ByPath"])
    explicit.update(inventory["additionalCurrentSha256ByPath"])
    explicit.update({".gitattributes", "tests/test_repo_ops.py", LEDGER, *REGISTRIES})
    return public_name(name) and (name in explicit or name.startswith(ACTIVE_ROOTS))


def public_git_view(root: Path, revision: str, inventory: dict) -> dict:
    selected = {}
    for entry in git(root, "ls-tree", "-rz", revision).split(b"\0"):
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
    return baseline


def load_context(root: Path) -> TrustedContext:
    checked_root(root)
    frozen = {name: git(root, "show", f"{BASELINE}:{name}") for name in FROZEN_DIGESTS}
    for name, value in frozen.items():
        require(digest(value) == FROZEN_DIGESTS[name], f"baseline authority mismatch: {name}")
    inventory, contract = (decode_document(frozen[name]) for name in (INVENTORY, CONTRACT))
    baseline = public_git_view(root, BASELINE, inventory)
    completed = public_git_view(root, COMPLETED_PREPARATION, inventory)
    for name, value in frozen.items():
        require(git(root, "show", f"{COMPLETED_PREPARATION}:{name}") == value,
                f"completed frozen authority changed: {name}")
    return TrustedContext(inventory, contract, frozen, baseline, completed)


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


def verify_completed_preservation(context: TrustedContext) -> None:
    completed = context.completed
    require(completed is not None, "missing completed preparation history")
    require(set(completed) == set(context.baseline), "completed protected path set changed")
    for name, (mode, before) in context.baseline.items():
        actual_mode, after = completed[name]
        require(actual_mode == mode, f"completed protected mode changed: {name}")
        if name in REGISTRIES:
            if name.endswith(".txt"):
                require(set(before.splitlines()).issubset(after.splitlines()), "completed census entries removed")
            else:
                verify_additive(decode_document(before), decode_document(after), name)
        elif name == LEDGER:
            require(after.startswith(before), "completed phase ledger history changed")
        else:
            require(after == before, f"completed protected source bytes changed: {name}")


CONTAINER_SOURCE = "private_Documents/development/container-dotfiles/dotfiles"
OPENCODE_ROOTS = ("private_dot_config/opencode", f"{CONTAINER_SOURCE}/private_dot_config/opencode")
LOADING_ROOTS = (".opencode", ".claude", ".agents", *OPENCODE_ROOTS,
                 "dot_claude", f"{CONTAINER_SOURCE}/dot_claude")
DISCOVERY_CHILDREN = {"agent", "agents", "skill", "skills", "command", "commands", "plugin", "plugins", "prompts"}
MIRRORS = "configs/devcontainer-sync.jsonc"
MANDATORY_CONFIGS = (".opencode/opencode.jsonc", *(f"{name}/opencode.jsonc" for name in OPENCODE_ROOTS))


def active_loading_path(name: str) -> bool:
    folded = name.casefold()
    if folded in {"opencode.json", "opencode.jsonc"}:
        return True
    for prefix in LOADING_ROOTS:
        prefix = prefix.casefold()
        if folded == prefix:
            return True
        if not folded.startswith(prefix + "/"):
            continue
        parts = folded[len(prefix) + 1:].split("/")
        if parts[0] in DISCOVERY_CHILDREN or parts[0] in {"agents.md", "claude.md", "symlink_agents.md", "symlink_claude.md", "opencode.json", "opencode.jsonc"}:
            return True
        if parts[0] == "profiles" and len(parts) >= 3:
            return parts[2] in DISCOVERY_CHILDREN or parts[2] in {"opencode.json", "opencode.jsonc"}
    return False


def inactive_files(context: TrustedContext) -> set[str]:
    files = {INVENTORY, CONTRACT, DESCRIPTOR, SUBSET}
    files.update(row["path"] for row in payload_rows(context) + evidence_rows(context))
    return files


def inactive_candidates(context: TrustedContext) -> set[str]:
    files = inactive_files(context)
    return files | {str(parent) for name in files for parent in PurePosixPath(name).parents if str(parent) != "."}


def reject_inactive_overlap(name: str, context: TrustedContext, *, directory: bool = False) -> None:
    folded, area = name.casefold().rstrip("/"), AREA.casefold()
    require(not (folded == area or folded.startswith(area + "/")), "declared loader exposes inactive preparation")
    if directory:
        require(bool(folded) and not area.startswith(folded + "/"), "declared loader exposes an inactive ancestor")
    if any(char in folded for char in "*?["):
        require(not any(fnmatch.fnmatchcase(item.casefold(), folded) for item in inactive_candidates(context)),
                "declared loader glob exposes inactive preparation")


def local_reference(root: Path, value: str, config: str, *, relative: bool = True) -> str | None:
    require(isinstance(value, str) and bool(value), "unverified local loader expression")
    if value.lower().startswith("file:"):
        parsed = urlsplit(value)
        require(parsed.netloc.lower() in {"", "localhost"} and not parsed.query and not parsed.fragment,
                "unverified local file URL")
        value = unquote(parsed.path)
    require(not re.search(r"%(?:2e|2f|5c)", value, re.IGNORECASE), "unverified encoded local expression")
    require(not any(char in value for char in "\\{}$\0\n\r") and not value.startswith("!")
            and value.count("[") == value.count("]"), "unverified local loader expression")
    require(not re.match(r"^[A-Za-z]:", value) and not value.startswith("//"), "unverified drive or UNC loader")
    require(":" not in value, "unverified local loader scheme")
    namespace = ""
    container = config.casefold().startswith(CONTAINER_SOURCE.casefold() + "/")
    homes = (("~/.config/opencode", OPENCODE_ROOTS[int(container)]),
             ("~/.claude", f"{CONTAINER_SOURCE}/dot_claude" if container else "dot_claude"))
    for prefix, source in homes:
        if value.casefold() == prefix or value.casefold().startswith(prefix + "/"):
            value = source + value[len(prefix):]
            namespace = source
            break
    else:
        if value.startswith("/"):
            repository = root.as_posix().rstrip("/")
            if value.casefold() == repository.casefold() or value.casefold().startswith(repository.casefold() + "/"):
                value = value[len(repository):].lstrip("/")
            else:
                require(AREA.casefold() not in posixpath.normpath(value).casefold(), "unverified external inactive reference")
                return None
        else:
            require(not value.startswith("~"), "unverified home loader expression")
            require(relative, "unverified relative global instruction")
            namespace = next((source for source in (*OPENCODE_ROOTS, "dot_claude", f"{CONTAINER_SOURCE}/dot_claude")
                              if config.casefold().startswith(source.casefold() + "/")), "")
            value = posixpath.join(str(PurePosixPath(config).parent), value)
    value = posixpath.normpath(value)
    require(not namespace or value.casefold() == namespace.casefold()
            or value.casefold().startswith(namespace.casefold() + "/"),
            "unverified escaping mapped loader namespace")
    require(value != ".." and not value.startswith("../"), "unverified escaping loader expression")
    return "" if value == "." else value


def current_config_names(root: Path, visible: set[str]) -> list[str]:
    candidates = {"opencode.json", "opencode.jsonc", ".opencode/opencode.json", ".opencode/opencode.jsonc"}
    for prefix in OPENCODE_ROOTS:
        candidates.update(f"{prefix}/opencode.{suffix}" for suffix in ("json", "jsonc"))
        profiles = root / prefix / "profiles"
        if os.path.lexists(profiles):
            require(safe_path(root, f"{prefix}/profiles").is_dir(), "unverified profiles directory")
            for profile in profiles.iterdir():
                require(not profile.is_symlink(), "unverified profile alias")
                if profile.is_dir():
                    candidates.update(f"{prefix}/profiles/{profile.name}/opencode.{suffix}" for suffix in ("json", "jsonc"))
    for name in candidates:
        require(not os.path.lexists(root / (name + ".tmpl")), "unverified loader configuration template")
    require(all(os.path.lexists(root / name) for name in MANDATORY_CONFIGS), "missing mandatory loading configuration")
    present = sorted(name for name in candidates if os.path.lexists(root / name))
    require(all(public_name(name) and name in visible for name in present), "unverified non-public loading configuration")
    return present


def reject_unknown_aliases(root: Path, name: str) -> None:
    current = root
    for component in PurePosixPath(name).parts:
        if any(char in component for char in "*?["):
            break
        current = current / component
        if not os.path.lexists(current):
            break
        require(not current.is_symlink() or active_loading_path(current.relative_to(root).as_posix()),
                "unverified alias outside bounded loading roots")


def verify_loading_config(root: Path, name: str, context: TrustedContext, visible: set[str]) -> None:
    config = decode_document(read_bytes(root, name))

    def inspect(value: str, *, directory: bool = False, instruction: bool = False) -> None:
        project = name in {"opencode.json", "opencode.jsonc", ".opencode/opencode.json", ".opencode/opencode.jsonc"}
        base = "opencode.jsonc" if instruction and project else name
        resolved = local_reference(root, value, base, relative=not instruction or project)
        if resolved is not None:
            require(public_name(resolved), "unverified private local declaration")
            reject_inactive_overlap(resolved, context, directory=directory)
            reject_unknown_aliases(root, resolved)
            for candidate in visible:
                if public_name(candidate) and ((directory and candidate.startswith(resolved + "/"))
                                               or fnmatch.fnmatchcase(candidate, resolved)):
                    reject_unknown_aliases(root, candidate)

    instructions = config.get("instructions", [])
    require(isinstance(instructions, list), "unverified instructions declaration")
    for value in instructions:
        require(isinstance(value, str), "unverified instruction value")
        if re.match(r"^(?:https?|git)://", value, re.IGNORECASE):
            continue
        inspect(value, instruction=True)
    skills = config.get("skills", {})
    require(isinstance(skills, dict) and isinstance(skills.get("paths", []), list), "unverified skill paths")
    for value in skills.get("paths", []):
        inspect(value, directory=True)
    plugins = config.get("plugin", [])
    require(isinstance(plugins, list), "unverified plugin declaration")
    for entry in plugins:
        value = entry[0] if isinstance(entry, list) and entry else entry
        require(isinstance(value, str), "unverified plugin identity")
        if re.match(r"^(?:https?|git|ssh)://|^git@|^github:|^@", value, re.IGNORECASE):
            continue
        if value.startswith((".", "/", "~")) or "\\" in value or value.lower().startswith("file:") or re.match(r"^[A-Za-z]:", value) or AREA.casefold() in value.casefold():
            inspect(value)
    for field in ("agent", "agents"):
        agents = config.get(field, {})
        require(isinstance(agents, dict), "unverified agent declaration")
        for agent in agents.values():
            require(isinstance(agent, dict), "unverified agent loader shape")
            for key in ("prompt", "system"):
                if key not in agent:
                    continue
                require(isinstance(agent[key], str), "unverified prompt loader shape")
                for value in re.findall(r"\{file:([^{}]+)\}", agent[key]):
                    inspect(value)
                require("{file:" not in re.sub(r"\{file:[^{}]+\}", "", agent[key]), "unverified prompt interpolation")


def verify_aliases(root: Path, visible: set[str], context: TrustedContext) -> int:
    aliases = {}
    for name in sorted(visible):
        if not active_loading_path(name) or not public_name(name):
            continue
        source = safe_path(root, name, link_leaf=True)
        if source.is_symlink():
            target = os.readlink(source)
            logical = name
        elif PurePosixPath(name).name.startswith("symlink_"):
            target = read_bytes(root, name).decode("utf-8").removesuffix("\n")
            logical = str(PurePosixPath(name).with_name(PurePosixPath(name).name[len("symlink_"):]))
        else:
            continue
        relative_name(logical)
        require(logical.casefold() not in aliases, "unverified alias case collision")
        aliases[logical.casefold()] = (logical, target)
    for start in aliases:
        current, seen = aliases[start][0], set()
        for _ in range(16):
            matches = [name for name in aliases if current.casefold() == name or current.casefold().startswith(name + "/")]
            if not matches:
                require(public_name(current) and os.path.lexists(root / current), "unverified dangling or private alias")
                final = safe_path(root, current)
                if final.is_dir():
                    for candidate in visible:
                        if public_name(candidate) and candidate.startswith(current + "/"):
                            reject_unknown_aliases(root, candidate)
                break
            name = max(matches, key=len)
            require(name not in seen, "unverified alias cycle")
            seen.add(name)
            logical, target = aliases[name]
            resolved = local_reference(root, target, logical)
            require(resolved is not None, "unverified external alias")
            current = posixpath.normpath(posixpath.join(resolved, current[len(logical):].lstrip("/")))
            reject_inactive_overlap(current, context, directory=True)
        else:
            require(not any(current.casefold() == name or current.casefold().startswith(name + "/") for name in aliases), "unverified alias hop limit")
            require(public_name(current) and os.path.lexists(root / current), "unverified dangling alias")
            safe_path(root, current)
    return len(aliases)


def verify_mirrors(root: Path, visible: set[str], context: TrustedContext) -> None:
    require(MIRRORS in visible, "missing public mirror declarations")
    document = decode_document(read_bytes(root, MIRRORS))
    mirrors = document.get("shared", {}).get("mirrors")
    require(isinstance(mirrors, list), "unverified mirror declaration schema")
    for mirror in mirrors:
        require(isinstance(mirror, dict), "unverified mirror declaration")
        source, target = mirror.get("source_root"), mirror.get("target_root")
        require(isinstance(source, str) and isinstance(target, str), "unverified mirror roots")
        relative_name(source)
        relative_name(target)
        reject_inactive_overlap(target, context, directory=True)
        reject_unknown_aliases(root, target)
        require(source.casefold() not in {name.casefold() for name in inactive_files(context)}, "unverified mirror file source root")
        include, exclude = mirror.get("include", ["**"]), mirror.get("exclude", [])
        require(isinstance(include, list) and isinstance(exclude, list)
                and all(isinstance(item, str) for item in include + exclude), "unverified mirror patterns")
        for name in inactive_files(context):
            if not name.casefold().startswith(source.casefold() + "/"):
                continue
            relative = name[len(source) + 1:]
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in include) and not any(fnmatch.fnmatchcase(relative, pattern) for pattern in exclude):
                require(not active_loading_path(f"{target}/{relative}"), "mirror projects inactive preparation into discovery")


def verify_current_loading(root: Path, context: TrustedContext) -> dict:
    visible = {name.decode() for name in git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0") if name}
    names = current_config_names(root, visible)
    aliases = verify_aliases(root, visible, context)
    for name in names:
        verify_loading_config(root, name, context, visible)
    verify_mirrors(root, visible, context)
    return {"configurations": len(names), "aliases": aliases, "scope": "bounded public declarations; external content and plugin code not qualified"}


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
    verify_completed_preservation(context)
    loading = verify_current_loading(root, context)
    return {"phase": "DV2-020", "payloadFiles": 14, "evidenceFiles": 3,
            "operationalReady": False, "aggregate": "withheld", "status": "PASS",
            "historicalPreservation": {"baseline": BASELINE, "completed": COMPLETED_PREPARATION, "status": "PASS"},
            "currentLoading": loading,
            "proof": "pinned bytes, selected closure, historical preparation audit and bounded current declarations; "
                     "CLI PASS does not prove chezmoi exclusion or runtime qualification"}


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
