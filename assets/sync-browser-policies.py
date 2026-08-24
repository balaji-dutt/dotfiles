#!/usr/bin/env python3
"""Synchronize vendored Just the Browser policy artifacts."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


REPO = "corbindavenport/just-the-browser"
POLICY_DIR = Path("configs/browser-policies/justthebrowser")
MANIFEST_PATH = POLICY_DIR / "manifest.json"
SCHEMA_REF = "../../schemas/justthebrowser-manifest.v1.schema.json"
DIFF_LINE_LIMIT = 40


@dataclass(frozen=True)
class DownloadedArtifact:
    path: str
    source_path: str
    url: str
    data: bytes
    temp_path: Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_manifest(repo_root: Path) -> dict:
    manifest_file = repo_root / MANIFEST_PATH
    with manifest_file.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise ValueError("manifest root must be an object")
    if manifest.get("$schema") != SCHEMA_REF:
        raise ValueError(f"manifest $schema must be {SCHEMA_REF!r}")
    if manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    return manifest


def expected_source_base_url(version: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{version}/"


def normalize_artifact_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"artifact path must stay inside policy dir: {value}")
    return path.as_posix()


def artifact_repo_path(repo_root: Path, artifact_path: str) -> Path:
    normalized = normalize_artifact_path(artifact_path)
    target = (repo_root / POLICY_DIR / Path(normalized)).resolve()
    policy_root = (repo_root / POLICY_DIR).resolve()
    if policy_root != target and policy_root not in target.parents:
        raise ValueError(f"artifact path escapes policy dir: {artifact_path}")
    return target


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "dotfiles-sync-browser-policies"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"failed to download {url}: {exc}") from exc


def download_artifacts(manifest: dict, temp_root: Path) -> list[DownloadedArtifact]:
    upstream = manifest.get("upstream", {})
    version = upstream.get("version")
    if not version:
        raise ValueError("manifest upstream.version is required")

    base_url = expected_source_base_url(version)
    downloads: list[DownloadedArtifact] = []
    for artifact in manifest.get("artifacts", []):
        artifact_path = normalize_artifact_path(artifact["path"])
        source_path = normalize_artifact_path(artifact.get("source_path", artifact_path))
        url = f"{base_url}{source_path}"
        data = fetch_bytes(url)
        temp_path = temp_root / Path(artifact_path)
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(data)
        downloads.append(
            DownloadedArtifact(
                path=artifact_path,
                source_path=source_path,
                url=url,
                data=data,
                temp_path=temp_path,
            )
        )
    return downloads


def decode_for_diff(data: bytes) -> list[str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    elif data.startswith(b"\xef\xbb\xbf"):
        text = data.decode("utf-8-sig", errors="replace")
    elif b"\x00" in data[:200]:
        text = data.decode("utf-16", errors="replace")
    else:
        text = data.decode("utf-8", errors="replace")
    return text.splitlines(keepends=True)


def diff_preview(name: str, local_data: bytes, upstream_data: bytes) -> list[str]:
    diff = difflib.unified_diff(
        decode_for_diff(local_data),
        decode_for_diff(upstream_data),
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


def formatted_manifest(manifest: dict) -> str:
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    text = text.replace(
        '    "browsers": [\n      "chrome",\n      "firefox"\n    ],',
        '    "browsers": ["chrome", "firefox"],',
    )
    text = text.replace(
        '    "platforms": [\n      "windows",\n      "darwin"\n    ],',
        '    "platforms": ["windows", "darwin"],',
    )
    return text


def update_manifest(manifest: dict, downloads: list[DownloadedArtifact]) -> None:
    version = manifest.get("upstream", {}).get("version")
    if not version:
        raise ValueError("manifest upstream.version is required")

    data_by_path = {download.path: download.data for download in downloads}
    for artifact in manifest.get("artifacts", []):
        artifact_path = normalize_artifact_path(artifact["path"])
        artifact["path"] = artifact_path
        artifact["source_path"] = normalize_artifact_path(
            artifact.get("source_path", artifact_path)
        )
        artifact["sha256"] = sha256_bytes(data_by_path[artifact_path])


def write_synced(repo_root: Path) -> int:
    manifest = read_manifest(repo_root)
    with tempfile.TemporaryDirectory(prefix="browser-policies-") as tmp:
        downloads = download_artifacts(manifest, Path(tmp))
        for download in downloads:
            target = artifact_repo_path(repo_root, download.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(download.data)
            print(f"synced {download.path} from {download.url}")

    update_manifest(manifest, downloads)
    with (repo_root / MANIFEST_PATH).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(formatted_manifest(manifest))
    print(f"updated {MANIFEST_PATH}")
    return 0


def check_synced(repo_root: Path) -> int:
    manifest = read_manifest(repo_root)
    upstream = manifest.get("upstream", {})
    version = upstream.get("version")
    if not version:
        raise ValueError("manifest upstream.version is required")

    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="browser-policies-") as tmp:
        downloads = download_artifacts(manifest, Path(tmp))
        download_by_path = {download.path: download for download in downloads}

        for artifact in manifest.get("artifacts", []):
            artifact_path = normalize_artifact_path(artifact["path"])
            local_path = artifact_repo_path(repo_root, artifact_path)
            if not local_path.is_file():
                problems.append(f"missing artifact: {artifact_path}")
                continue

            local_data = local_path.read_bytes()
            local_hash = sha256_bytes(local_data)
            manifest_hash = artifact.get("sha256")
            if local_hash != manifest_hash:
                problems.append(
                    f"sha256 drift for {artifact_path}: "
                    f"manifest {manifest_hash}, local {local_hash}"
                )

            download = download_by_path[artifact_path]
            upstream_hash = sha256_bytes(download.data)
            if local_data != download.data:
                problems.append(
                    f"artifact drift for {artifact_path}: "
                    f"local {local_hash}, upstream {upstream_hash}"
                )
                problems.extend(
                    f"  {line}"
                    for line in diff_preview(artifact_path, local_data, download.data)
                )

            if manifest_hash != upstream_hash:
                problems.append(
                    f"manifest hash for {artifact_path} does not match upstream: "
                    f"manifest {manifest_hash}, upstream {upstream_hash}"
                )

    if problems:
        print("ERROR: Just the Browser policy artifacts are out of sync", file=sys.stderr)
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            "Run `python3 assets/sync-browser-policies.py --write` and review the diff.",
            file=sys.stderr,
        )
        return 1

    print(f"Just the Browser policy artifacts match {version}.")
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
