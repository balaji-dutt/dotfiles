#!/usr/bin/env python3
"""Synchronize vendored Plannotator slash-command artifacts."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


REPO = "backnotprop/plannotator"
MANIFEST_PATH = Path("configs/plannotator-assets.json")
CHEZMOIDATA_PATH = Path(".chezmoidata.yaml")
VERSION_KEY = "plannotator_version"
ALLOWED_TARGET_PREFIXES = (
    "dot_claude/skills/",
    "private_dot_config/opencode/commands/",
)
DIFF_LINE_LIMIT = 40


@dataclass(frozen=True)
class DownloadedArtifact:
    path: str
    url: str
    data: bytes


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_manifest(repo_root: Path) -> dict:
    manifest_file = repo_root / MANIFEST_PATH
    with manifest_file.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not manifest.get("upstream", {}).get("version"):
        raise ValueError("manifest upstream.version is required")
    return manifest


def expected_release_url(version: str) -> str:
    return f"https://github.com/{REPO}/releases/tag/{version}"


def expected_source_base_url(version: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{version}/"


def pinned_version(repo_root: Path) -> str:
    """Return `v` + the plannotator_version pinned in .chezmoidata.yaml.

    Parsed with a regex rather than a YAML library: the key is a top-level
    scalar, the pattern is anchored under MULTILINE so an indented key cannot
    match, and this repo does not otherwise require PyYAML.
    """
    data_file = repo_root / CHEZMOIDATA_PATH
    pattern = re.compile(rf'^{VERSION_KEY}:\s*"?([^"\s#]+)"?', re.MULTILINE)
    match = pattern.search(data_file.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"{VERSION_KEY} not found in {CHEZMOIDATA_PATH}")
    return f"v{match.group(1)}"


def normalize_artifact_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"artifact path must stay repo-relative: {value}")
    normalized = path.as_posix()
    if not normalized.startswith(ALLOWED_TARGET_PREFIXES):
        allowed = ", ".join(ALLOWED_TARGET_PREFIXES)
        raise ValueError(f"artifact path must start with one of {allowed}: {value}")
    return normalized


def normalize_source_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"source path must stay repo-relative: {value}")
    return path.as_posix()


def artifact_repo_path(repo_root: Path, normalized_path: str) -> Path:
    """Resolve an already-normalized artifact path inside the repo."""
    target = (repo_root / Path(normalized_path)).resolve()
    root = repo_root.resolve()
    if root not in target.parents:
        raise ValueError(f"artifact path escapes repo root: {normalized_path}")
    return target


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "dotfiles-sync-plannotator-assets"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"failed to download {url}: {exc}") from exc


def download_artifacts(manifest: dict) -> list[DownloadedArtifact]:
    base_url = expected_source_base_url(manifest["upstream"]["version"])
    downloads: list[DownloadedArtifact] = []
    for artifact in manifest.get("artifacts", []):
        artifact_path = normalize_artifact_path(artifact["path"])
        url = f"{base_url}{normalize_source_path(artifact['source_path'])}"
        downloads.append(
            DownloadedArtifact(path=artifact_path, url=url, data=fetch_bytes(url))
        )
    return downloads


def diff_preview(name: str, local_data: bytes, upstream_data: bytes) -> list[str]:
    diff = difflib.unified_diff(
        local_data.decode("utf-8", errors="replace").splitlines(keepends=True),
        upstream_data.decode("utf-8", errors="replace").splitlines(keepends=True),
        fromfile=f"local/{name}",
        tofile=f"upstream/{name}",
        n=3,
    )
    lines = list(diff)
    if len(lines) > DIFF_LINE_LIMIT:
        hidden = len(lines) - DIFF_LINE_LIMIT
        lines = lines[:DIFF_LINE_LIMIT]
        lines.append(f"... ({hidden} more diff lines hidden)\n")
    return [line.rstrip("\n") for line in lines]


def update_manifest(manifest: dict, downloads: list[DownloadedArtifact]) -> None:
    upstream = manifest["upstream"]
    upstream["release_url"] = expected_release_url(upstream["version"])
    upstream["source_base_url"] = expected_source_base_url(upstream["version"])

    data_by_path = {download.path: download.data for download in downloads}
    for artifact in manifest.get("artifacts", []):
        artifact["path"] = normalize_artifact_path(artifact["path"])
        artifact["source_path"] = normalize_source_path(artifact["source_path"])
        artifact["sha256"] = sha256_bytes(data_by_path[artifact["path"]])


def write_synced(repo_root: Path) -> int:
    manifest = read_manifest(repo_root)

    # The Renovate-managed CLI pin is the single source of truth for which tag
    # to vendor. Without this, a pin bump makes --check fail and --write
    # re-download the manifest's own stale tag, so the failure never clears.
    manifest["upstream"]["version"] = pinned_version(repo_root)

    downloads = download_artifacts(manifest)
    for download in downloads:
        target = artifact_repo_path(repo_root, download.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(download.data)
        print(f"synced {download.path} from {download.url}")

    update_manifest(manifest, downloads)
    with (repo_root / MANIFEST_PATH).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"updated {MANIFEST_PATH}")
    print(
        "Run `bash ./assets/sync-devcontainer-assets.sh` to mirror the Claude "
        "skills into container-dotfiles."
    )
    return 0


def check_synced(repo_root: Path) -> int:
    manifest = read_manifest(repo_root)
    upstream = manifest["upstream"]
    version = upstream["version"]

    problems: list[str] = []

    # The vendored files call `plannotator` subcommands by name, so a CLI bump
    # that renames one would break them silently. Coupling the vendored tag to
    # the pin turns that into a --check failure instead.
    pinned = pinned_version(repo_root)
    if version != pinned:
        problems.append(
            f"upstream.version drift: manifest {version}, but "
            f"{CHEZMOIDATA_PATH}:{VERSION_KEY} pins {pinned[1:]} "
            f"(expected tag {pinned})"
        )

    expected_release = expected_release_url(version)
    expected_base = expected_source_base_url(version)
    if upstream.get("release_url") != expected_release:
        problems.append(
            "upstream.release_url drift: "
            f"expected {expected_release}, found {upstream.get('release_url')}"
        )
    if upstream.get("source_base_url") != expected_base:
        problems.append(
            "upstream.source_base_url drift: "
            f"expected {expected_base}, found {upstream.get('source_base_url')}"
        )

    downloads = {download.path: download for download in download_artifacts(manifest)}

    for artifact in manifest.get("artifacts", []):
        artifact_path = normalize_artifact_path(artifact["path"])
        local_path = artifact_repo_path(repo_root, artifact_path)
        if not local_path.is_file():
            problems.append(f"missing artifact: {artifact_path}")
            continue

        local_data = local_path.read_bytes()
        local_hash = sha256_bytes(local_data)
        if local_hash != artifact.get("sha256"):
            problems.append(
                f"sha256 drift for {artifact_path}: "
                f"manifest {artifact.get('sha256')}, local {local_hash}"
            )

        upstream_data = downloads[artifact_path].data
        if local_data != upstream_data:
            problems.append(
                f"artifact drift for {artifact_path}: "
                f"local {local_hash}, upstream {sha256_bytes(upstream_data)}"
            )
            problems.extend(
                f"  {line}"
                for line in diff_preview(artifact_path, local_data, upstream_data)
            )

    if problems:
        print("ERROR: Plannotator vendored artifacts are out of sync", file=sys.stderr)
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            "Run `python3 assets/sync-plannotator-assets.py --write` and review the diff.",
            file=sys.stderr,
        )
        return 1

    print(f"Plannotator vendored artifacts match {version}.")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="rewrite vendored artifacts")
    mode.add_argument("--check", action="store_true", help="validate without writing")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo_root = Path.cwd()
    try:
        if args.check:
            return check_synced(repo_root)
        return write_synced(repo_root)
    except Exception as exc:  # noqa: BLE001 - script should report concise failures
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
