#!/usr/bin/env python3
"""Reconcile managed Git template hooks into existing repositories.

This helper is invoked by chezmoi apply scripts. It deliberately scans only an
explicit root and that root's immediate children. Hook ownership is recorded in
the Git hooks directory so later runs can update or remove only copies that this
helper owns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


MANIFEST_NAME = ".dotfiles-managed-hooks.json"
MANIFEST_VERSION = 1


class SyncError(RuntimeError):
    """An error that should fail the reconciliation run."""


class RepositoryUnavailable(RuntimeError):
    """A repository that cannot be inspected and should be skipped."""


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class SyncResult:
    installed: int = 0
    updated: int = 0
    adopted: int = 0
    removed: int = 0
    conflicts: int = 0


def info(message: str) -> None:
    print(f"INFO: {message}")


def warning(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_git(repo: Path, arguments: Sequence[str]) -> GitResult:
    process = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return GitResult(
        process.returncode,
        process.stdout.strip(),
        process.stderr.strip(),
    )


def require_git(repo: Path, arguments: Sequence[str], operation: str) -> str:
    result = run_git(repo, arguments)
    if result.returncode != 0:
        detail = result.stderr or result.stdout or f"exit {result.returncode}"
        raise RepositoryUnavailable(f"{operation} for {repo}: {detail}")
    return result.stdout


def candidate_repositories(roots: Sequence[Path]) -> list[Path]:
    candidates: set[Path] = set()
    for raw_root in roots:
        root = raw_root.expanduser()
        if not root.is_dir():
            continue
        if (root / ".git").exists():
            candidates.add(root)
        try:
            children = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            warning(f"cannot inspect repository root {root}: {exc}")
            continue
        for child in children:
            if child.is_dir() and (child / ".git").exists():
                candidates.add(child)
    return sorted(candidates, key=lambda item: str(item).casefold())


def managed_hooks(template_hooks_dir: Path) -> dict[str, Path]:
    if not template_hooks_dir.is_dir():
        raise SyncError(f"managed template hooks directory is missing: {template_hooks_dir}")
    hooks: dict[str, Path] = {}
    for candidate in sorted(
        template_hooks_dir.iterdir(), key=lambda item: item.name.casefold()
    ):
        if candidate.name == MANIFEST_NAME or candidate.is_symlink():
            continue
        if candidate.is_file():
            hooks[candidate.name] = candidate
    return hooks


def load_manifest(hooks_dir: Path) -> dict[str, str]:
    manifest_path = hooks_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return {}
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            not isinstance(raw, dict)
            or raw.get("version") != MANIFEST_VERSION
            or not isinstance(raw.get("hooks"), dict)
        ):
            raise ValueError("unsupported manifest shape")
        hooks = raw["hooks"]
        if not all(
            isinstance(name, str)
            and name
            and "/" not in name
            and "\\" not in name
            and isinstance(digest, str)
            and len(digest) == 64
            for name, digest in hooks.items()
        ):
            raise ValueError("invalid hook ownership entry")
        return dict(hooks)
    except (OSError, ValueError) as exc:
        warning(f"ignoring invalid hook ownership manifest {manifest_path}: {exc}")
        return {}


def atomic_write_text(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_manifest(hooks_dir: Path, hooks: dict[str, str]) -> None:
    manifest_path = hooks_dir / MANIFEST_NAME
    if not hooks:
        manifest_path.unlink(missing_ok=True)
        return
    payload = json.dumps(
        {"version": MANIFEST_VERSION, "hooks": dict(sorted(hooks.items()))},
        indent=2,
        sort_keys=True,
    )
    payload += "\n"
    try:
        if manifest_path.read_text(encoding="utf-8") == payload:
            return
    except FileNotFoundError:
        pass
    atomic_write_text(manifest_path, payload)


def copy_hook(source: Path, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile_execute_bits(source: Path, destination: Path) -> None:
    source_bits = source.stat().st_mode & 0o111
    destination_mode = destination.stat().st_mode
    desired_mode = (destination_mode & ~0o111) | source_bits
    if desired_mode != destination_mode:
        destination.chmod(desired_mode)


def sync_hooks(
    repo: Path,
    hooks_dir: Path,
    sources: dict[str, Path],
) -> SyncResult:
    if hooks_dir.is_symlink():
        raise RepositoryUnavailable(
            f"preserving symlinked Git hooks directory for {repo}: {hooks_dir}"
        )
    hooks_dir.mkdir(parents=True, exist_ok=True)
    previous = load_manifest(hooks_dir)
    current: dict[str, str] = {}
    installed = updated = adopted = removed = conflicts = 0

    for name, source in sources.items():
        source_digest = sha256(source)
        destination = hooks_dir / name
        if destination.is_symlink():
            warning(f"preserving unknown symlinked hook {destination}")
            conflicts += 1
            continue
        if not destination.exists():
            copy_hook(source, destination)
            current[name] = source_digest
            installed += 1
            info(f"installed managed hook {destination}")
            continue
        if not destination.is_file():
            warning(f"preserving non-file hook path {destination}")
            conflicts += 1
            continue

        destination_digest = sha256(destination)
        if destination_digest == source_digest:
            reconcile_execute_bits(source, destination)
            current[name] = source_digest
            if previous.get(name) != source_digest:
                adopted += 1
                info(f"adopted existing managed hook {destination}")
            continue
        if previous.get(name) == destination_digest:
            copy_hook(source, destination)
            current[name] = source_digest
            updated += 1
            info(f"updated managed hook {destination}")
            continue

        warning(f"preserving unknown same-name hook {destination}")
        conflicts += 1

    for name, recorded_digest in previous.items():
        if name in sources:
            continue
        destination = hooks_dir / name
        if destination.is_symlink():
            warning(f"preserving modified retired hook {destination}")
            conflicts += 1
        elif not destination.exists():
            continue
        elif destination.is_file() and sha256(destination) == recorded_digest:
            destination.unlink()
            removed += 1
            info(f"removed retired managed hook {destination}")
        else:
            warning(f"preserving modified retired hook {destination}")
            conflicts += 1

    write_manifest(hooks_dir, current)
    return SyncResult(installed, updated, adopted, removed, conflicts)


def absolute_git_path(repo: Path, *arguments: str) -> Path:
    value = require_git(
        repo,
        ["rev-parse", "--path-format=absolute", *arguments],
        f"resolving {' '.join(arguments)}",
    )
    return Path(value)


def local_hooks_path(repo: Path) -> str | None:
    result = run_git(repo, ["config", "--local", "--get", "core.hooksPath"])
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        detail = result.stderr or result.stdout or f"exit {result.returncode}"
        raise RepositoryUnavailable(f"reading local core.hooksPath for {repo}: {detail}")
    return result.stdout or None


def effective_hooks_path(repo: Path) -> str | None:
    result = run_git(repo, ["config", "--get", "core.hooksPath"])
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        detail = result.stderr or result.stdout or f"exit {result.returncode}"
        raise RepositoryUnavailable(f"reading effective core.hooksPath for {repo}: {detail}")
    return result.stdout or None


def is_missing_beads_hooks(value: str, resolved: Path) -> bool:
    if resolved.exists():
        return False
    normalized = value.replace("\\", "/").rstrip("/")
    return normalized.endswith("/.beads/hooks") or normalized == ".beads/hooks"


def unset_local_hooks_path(repo: Path) -> None:
    result = run_git(repo, ["config", "--local", "--unset-all", "core.hooksPath"])
    if result.returncode not in (0, 5):
        detail = result.stderr or result.stdout or f"exit {result.returncode}"
        raise SyncError(f"clearing local core.hooksPath for {repo}: {detail}")


def reconcile_repository(
    repo: Path,
    default_hooks_dir: Path,
    template_hooks_dir: Path,
    sources: dict[str, Path],
) -> SyncResult | None:
    require_git(repo, ["rev-parse", "--is-inside-work-tree"], "checking repository")
    local_value = local_hooks_path(repo)
    effective_value = effective_hooks_path(repo)
    migrate_reason: str | None = None

    if effective_value:
        configured_hooks_dir = absolute_git_path(repo, "--git-path", "hooks")
        if local_value and configured_hooks_dir.resolve(strict=False) == template_hooks_dir.resolve(strict=False):
            migrate_reason = "managed template hooks path"
        elif local_value and is_missing_beads_hooks(local_value, configured_hooks_dir):
            migrate_reason = "missing .beads/hooks path"
        else:
            warning(
                f"preserving custom core.hooksPath for {repo}: "
                f"{effective_value} ({configured_hooks_dir})"
            )
            return None

    result = sync_hooks(repo, default_hooks_dir, sources)
    if migrate_reason:
        unset_local_hooks_path(repo)
        info(f"cleared {migrate_reason} in {repo}")
    return result


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template-hooks-dir",
        required=True,
        type=Path,
        help="chezmoi-managed Git template hooks directory",
    )
    parser.add_argument(
        "--repo-root",
        action="append",
        default=[],
        type=Path,
        help="repository root or parent directory (repeatable)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv if argv is not None else sys.argv[1:])
    template_hooks_dir = arguments.template_hooks_dir.expanduser().resolve(strict=False)
    try:
        sources = managed_hooks(template_hooks_dir)
    except SyncError as exc:
        error(str(exc))
        return 1

    repositories = candidate_repositories(arguments.repo_root)
    seen_hooks_dirs: set[Path] = set()
    totals = SyncResult()
    failures = 0
    for repo in repositories:
        try:
            common_dir = absolute_git_path(repo, "--git-common-dir")
            default_hooks_dir = common_dir / "hooks"
            if default_hooks_dir.is_symlink():
                warning(
                    f"preserving symlinked Git hooks directory for {repo}: "
                    f"{default_hooks_dir}"
                )
                continue
            hooks_dir_identity = default_hooks_dir.resolve(strict=False)
            if hooks_dir_identity in seen_hooks_dirs:
                continue
            seen_hooks_dirs.add(hooks_dir_identity)
            result = reconcile_repository(
                repo, default_hooks_dir, template_hooks_dir, sources
            )
            if result is None:
                continue
            totals = SyncResult(
                totals.installed + result.installed,
                totals.updated + result.updated,
                totals.adopted + result.adopted,
                totals.removed + result.removed,
                totals.conflicts + result.conflicts,
            )
        except RepositoryUnavailable as exc:
            warning(str(exc))
        except SyncError as exc:
            error(str(exc))
            failures += 1
        except OSError as exc:
            error(f"synchronizing hooks for {repo}: {exc}")
            failures += 1

    info(
        "Git hook sync summary: "
        f"repositories={len(repositories)}, installed={totals.installed}, "
        f"updated={totals.updated}, adopted={totals.adopted}, "
        f"removed={totals.removed}, conflicts={totals.conflicts}, "
        f"failures={failures}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
