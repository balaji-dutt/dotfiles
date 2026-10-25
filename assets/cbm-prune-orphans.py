#!/usr/bin/env python3
"""Delete codebase-memory-mcp indexes whose repository root no longer exists.

Repo-only helper. It is never applied to a chezmoi target and is never
templated. .chezmoiscripts/run_after_zz-prune-codebase-memory-mcp.sh.tmpl
runs it with --apply on macOS, Linux, and WSL2 applies. Native Windows has no
caller yet.

A project is deleted only when its root path is absent and the root's parent
directory exists and is not empty. An unmounted volume leaves either no
parent or an empty mount-point directory, so indexes on a missing NFS share or
external disk are kept. A root that cannot be checked is kept. Roots passed
with --keep are never deleted.

Usage:
  cbm-prune-orphans.py [--apply] [--cbm PATH] [--keep PATH]...

Without --apply the helper only reports what it would delete.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PAGE_SIZE = 500
SESSION_PROBE = "codebase-memory"
CLI_TIMEOUT_SECONDS = 180
LOOKUP_TIMEOUT_SECONDS = 30


class CbmError(Exception):
    pass


@dataclass(frozen=True)
class Project:
    name: str
    root: str


def is_wsl() -> bool:
    return bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in platform.release().lower()


def is_windows_path(value: str) -> bool:
    if value.lower().endswith(".exe"):
        return True
    parts = Path(value).parts
    return len(parts) > 2 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha()


def is_native_candidate(candidate: str | None, wsl: bool) -> bool:
    if not candidate:
        return False
    path = Path(candidate)
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    if not wsl:
        return True
    return not any(is_windows_path(value) for value in (candidate, os.path.realpath(candidate)))


def resolve_cbm() -> str | None:
    wsl = is_wsl()
    mise = shutil.which("mise")
    if is_native_candidate(mise, wsl):
        try:
            found = subprocess.run(
                [mise, "which", "codebase-memory-mcp"],
                capture_output=True,
                text=True,
                check=False,
                timeout=LOOKUP_TIMEOUT_SECONDS,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            found = ""
        if is_native_candidate(found, wsl):
            return found
    found = shutil.which("codebase-memory-mcp")
    return found if is_native_candidate(found, wsl) else None


def run_tool(cbm: str, tool: str, *args: str) -> dict:
    try:
        result = subprocess.run(
            [cbm, "cli", "--json", tool, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise CbmError(
            f"codebase-memory-mcp cli {tool} did not finish within {CLI_TIMEOUT_SECONDS} seconds"
        ) from error
    except OSError as error:
        raise CbmError(f"codebase-memory-mcp cli {tool} could not start: {error}") from error
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError:
        envelope = None
    if result.returncode != 0 or not isinstance(envelope, dict) or envelope.get("isError"):
        detail = " ".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        raise CbmError(f"codebase-memory-mcp cli {tool} failed: {detail or f'exit {result.returncode}'}")
    content = envelope.get("structuredContent")
    if not isinstance(content, dict):
        raise CbmError(f"codebase-memory-mcp cli {tool} returned no structured content")
    return content


def list_projects(cbm: str) -> list[Project]:
    projects: list[Project] = []
    offset = 0
    while True:
        page = run_tool(
            cbm, "list_projects", "--format", "json",
            "--limit", str(PAGE_SIZE), "--offset", str(offset),
        )
        for entry in page.get("projects", []):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            root = entry.get("root_path")
            if isinstance(name, str) and name:
                projects.append(Project(name, root if isinstance(root, str) else ""))
        next_offset = page.get("next_offset")
        if not page.get("has_more") or not isinstance(next_offset, int) or next_offset <= offset:
            return projects
        offset = next_offset


def normalize(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


@dataclass(frozen=True)
class Verdict:
    delete: bool
    reason: str
    report: bool = False


def classify(project: Project, kept: set[str]) -> Verdict:
    root = project.root
    if not root or not os.path.isabs(root):
        return Verdict(False, "root path is not absolute")
    try:
        os.lstat(root)
        return Verdict(False, "root exists")
    except FileNotFoundError:
        pass
    except OSError as error:
        return Verdict(False, f"root cannot be checked ({error.strerror})", report=True)
    if normalize(root) in kept:
        return Verdict(False, "root is protected")
    parent = os.path.dirname(os.path.normpath(root))
    try:
        with os.scandir(parent) as entries:
            populated = next(entries, None) is not None
    except FileNotFoundError:
        return Verdict(False, "parent directory is missing; its volume may be unmounted", report=True)
    except OSError as error:
        return Verdict(False, f"parent directory cannot be read ({error.strerror})", report=True)
    if not populated:
        return Verdict(False, "parent directory is empty; it may be an unmounted mount point", report=True)
    return Verdict(True, "root is missing")


def sessions_active() -> bool:
    pgrep = shutil.which("pgrep")
    if pgrep is None:
        return False
    try:
        probe = subprocess.run(
            [pgrep, SESSION_PROBE], capture_output=True, check=False, timeout=LOOKUP_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def fail_or_skip(error: CbmError) -> int:
    if sessions_active():
        print(
            "WARNING: codebase-memory-mcp CLI failed while CBM sessions are active; "
            f"orphaned indexes were not pruned this run. {error}",
            file=sys.stderr,
        )
        return 0
    print(f"ERROR: {error}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="delete orphaned projects")
    parser.add_argument("--cbm", help="codebase-memory-mcp binary (default: resolve native binary)")
    parser.add_argument("--keep", action="append", default=[], metavar="PATH",
                        help="root path that is never deleted; repeatable")
    args = parser.parse_args(argv)

    cbm = args.cbm or resolve_cbm()
    if not cbm:
        print("INFO: native codebase-memory-mcp is not available; skipping index pruning.")
        return 0

    kept = {normalize(path) for path in args.keep}
    try:
        projects = list_projects(cbm)
    except CbmError as error:
        return fail_or_skip(error)

    orphans: list[Project] = []
    for project in projects:
        verdict = classify(project, kept)
        if verdict.delete:
            orphans.append(project)
        elif verdict.report:
            print(f"INFO: keeping {project.name} ({project.root}): {verdict.reason}")

    verb = "deleting" if args.apply else "would delete"
    for project in orphans:
        print(f"INFO: {verb} {project.name} ({project.root}): root is missing")
        if args.apply:
            try:
                run_tool(cbm, "delete_project", "--project", project.name)
            except CbmError as error:
                return fail_or_skip(error)

    summary = "deleted" if args.apply else "orphaned"
    print(f"INFO: codebase-memory-mcp: {len(orphans)} of {len(projects)} indexes {summary}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
