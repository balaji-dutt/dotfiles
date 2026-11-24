#!/usr/bin/env python3
"""Read the Beads and Dolt pins at a git ref, and fetch those exact upstream
release binaries into a private prefix without touching the installed ones."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PIN_PATTERN = (
    r"^[ \t]*{key}:[ \t]*"
    r'(?:"(?P<double>[^"\r\n]+)"|\'(?P<single>[^\'\r\n]+)\'|(?P<bare>[^#\s]+))'
    r"[ \t]*(?:#.*)?\r?$"
)
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
GIT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@()~^-]*$")
PREFIX_MARKER = ".beads-toolchain"
DIGEST_PATTERN = re.compile(r"^sha256:(?P<hex>[0-9a-f]{64})$")
DOWNLOAD_HOSTS = frozenset(
    ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
)
MAX_ARCHIVE_BYTES = 400 * 1024 * 1024
MAX_BINARY_BYTES = 600 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Tool:
    name: str
    pin_key: str
    repo: str
    asset: str
    version_line: str


TOOLS = {
    "bd": Tool(
        "bd", "beads_version", "gastownhall/beads",
        "beads_{version}_{os}_{arch}.tar.gz", "bd version",
    ),
    "dolt": Tool(
        "dolt", "dolt_version", "dolthub/dolt", "dolt-{os}-{arch}.tar.gz", "dolt version"
    ),
}


class ToolchainError(Exception):
    """Bad input, an untrusted download, or a prefix that cannot be used."""


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        del req, fp, code, msg, headers, newurl
        raise ToolchainError("GitHub API redirected unexpectedly")


class DownloadRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        require_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def require_version(value: str, what: str) -> str:
    if not VERSION_PATTERN.fullmatch(value):
        raise ToolchainError(f"invalid {what} version {value!r}")
    return value


def require_download_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise ToolchainError("invalid download URL") from error
    if (
        parts.scheme != "https"
        or parts.hostname not in DOWNLOAD_HOSTS
        or port is not None
        or parts.username is not None
        or parts.password is not None
    ):
        raise ToolchainError("refusing a download URL outside GitHub release hosts")
    return url


def parse_pins(text: str, where: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for tool in TOOLS.values():
        matches = list(
            re.finditer(PIN_PATTERN.format(key=re.escape(tool.pin_key)), text, re.MULTILINE)
        )
        if len(matches) != 1:
            raise ToolchainError(
                f"expected exactly one {tool.pin_key} in {where}, found {len(matches)}"
            )
        match = matches[0]
        value = match.group("double") or match.group("single") or match.group("bare")
        pins[tool.name] = require_version(value, tool.pin_key)
    return pins


def read_pins(repo_root: Path, ref: str | None) -> dict[str, str]:
    if ref is None:
        path = repo_root / ".chezmoidata.yaml"
        try:
            return parse_pins(path.read_text(encoding="utf-8"), str(path))
        except (OSError, UnicodeDecodeError) as error:
            raise ToolchainError(f"cannot read {path}: {error}") from error
    if not GIT_REF_PATTERN.fullmatch(ref):
        raise ToolchainError(f"refusing git ref {ref!r}")
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"{ref}:.chezmoidata.yaml"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolchainError(f"git could not read {ref}: {error}") from error
    if completed.returncode != 0:
        raise ToolchainError(f"git could not read .chezmoidata.yaml at {ref}")
    return parse_pins(completed.stdout, f".chezmoidata.yaml at {ref}")


def host_platform() -> tuple[str, str]:
    system = {"Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower()
    )
    if system is None or arch is None:
        raise ToolchainError(
            f"unsupported platform {platform.system()}/{platform.machine()}"
        )
    return system, arch


def asset_name(tool: Tool, version: str, system: str, arch: str) -> str:
    return tool.asset.format(version=version, os=system, arch=arch)


def release_api_url(tool: Tool, version: str) -> str:
    return f"https://api.github.com/repos/{tool.repo}/releases/tags/v{version}"


def find_asset(tool: Tool, version: str, name: str, timeout: float, opener: Any | None = None) -> tuple[str, str]:
    """Return (download URL, sha256 hex) that the GitHub API publishes for an asset."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "dotfiles-beads-toolchain/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(release_api_url(tool, version), headers=headers)
    opener = opener or urllib.request.build_opener(NoRedirects)
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ToolchainError(
            f"GitHub API returned HTTP {error.code} for {tool.repo} v{version}"
        ) from error
    except (
        urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException,
        UnicodeDecodeError, json.JSONDecodeError,
    ) as error:
        raise ToolchainError(f"GitHub API request for {tool.repo} failed: {error}") from error
    assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        raise ToolchainError(f"{tool.repo} v{version} release data has no assets")
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == name:
            digest = DIGEST_PATTERN.fullmatch(str(asset.get("digest") or ""))
            if digest is None:
                raise ToolchainError(f"{name} has no sha256 digest in the GitHub API")
            url = asset.get("browser_download_url")
            expected = f"https://github.com/{tool.repo}/releases/download/v{version}/{name}"
            if url != expected:
                raise ToolchainError(f"{name} has an unexpected download URL")
            return expected, digest.group("hex")
    raise ToolchainError(f"{tool.repo} v{version} publishes no asset named {name}")


def download(url: str, sha256: str, destination: Path, timeout: float, opener: Any | None = None) -> None:
    request = urllib.request.Request(
        require_download_url(url), headers={"User-Agent": "dotfiles-beads-toolchain/1.0"}
    )
    opener = opener or urllib.request.build_opener(DownloadRedirects)
    digest = hashlib.sha256()
    total = 0
    try:
        with opener.open(request, timeout=timeout) as response, destination.open("wb") as out:
            require_download_url(response.geturl())
            while chunk := response.read(CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ToolchainError(f"{url} is larger than {MAX_ARCHIVE_BYTES} bytes")
                digest.update(chunk)
                out.write(chunk)
    except urllib.error.HTTPError as error:
        raise ToolchainError(f"download of {url} returned HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise ToolchainError(f"download of {url} failed: {error}") from error
    if digest.hexdigest() != sha256:
        raise ToolchainError(
            f"sha256 mismatch for {url}: expected {sha256}, got {digest.hexdigest()}"
        )


def extract_binary(archive: Path, name: str, destination: Path) -> None:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            candidates = [
                member
                for member in tar.getmembers()
                if member.isfile() and Path(member.name).name == name
            ]
            if len(candidates) != 1:
                raise ToolchainError(
                    f"expected one {name} binary in {archive.name}, found {len(candidates)}"
                )
            member = candidates[0]
            if member.size > MAX_BINARY_BYTES:
                raise ToolchainError(f"{name} in {archive.name} is unexpectedly large")
            source = tar.extractfile(member)
            if source is None:
                raise ToolchainError(f"cannot read {name} from {archive.name}")
            with source, destination.open("wb") as out:
                shutil.copyfileobj(source, out, CHUNK_BYTES)
    except (tarfile.TarError, OSError, EOFError) as error:
        raise ToolchainError(f"cannot extract {name} from {archive.name}: {error}") from error
    destination.chmod(0o755)


def reported_version(tool: Tool, binary: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="beads-toolchain-home-") as home:
        dolt_config = Path(home) / ".dolt"
        dolt_config.mkdir()
        (dolt_config / "config_global.json").write_text(
            json.dumps({"metrics.disabled": "true"}), encoding="utf-8"
        )
        env = {
            "HOME": home,
            "XDG_CONFIG_HOME": str(Path(home) / "config"),
            "XDG_DATA_HOME": str(Path(home) / "data"),
            "XDG_CACHE_HOME": str(Path(home) / "cache"),
            "PATH": os.defpath,
            "BD_DISABLE_METRICS": "1",
        }
        try:
            completed = subprocess.run(
                [str(binary), "version"],
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                cwd=home,
                env=env,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ToolchainError(f"{binary} version failed: {error}") from error
    match = re.search(
        rf"(?m)^{re.escape(tool.version_line)}\s+v?([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)",
        completed.stdout,
    )
    if completed.returncode != 0 or match is None:
        raise ToolchainError(f"{binary} version did not report a version")
    return match.group(1)


def claim_prefix(prefix: Path) -> None:
    """Create the prefix, or accept one this helper created; refuse any other directory."""
    marker = prefix / PREFIX_MARKER
    try:
        if prefix.exists() and not prefix.is_dir():
            raise ToolchainError(f"{prefix} is not a directory")
        if prefix.is_dir() and any(prefix.iterdir()) and not marker.is_file():
            raise ToolchainError(
                f"refusing {prefix}: it holds files and no {PREFIX_MARKER} marker, "
                "so it was not created by this helper"
            )
        (prefix / "bin").mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError as error:
        raise ToolchainError(f"cannot use {prefix} as a toolchain prefix: {error}") from error


def install(tool: Tool, version: str, prefix: Path, timeout: float) -> Path:
    system, arch = host_platform()
    name = asset_name(tool, version, system, arch)
    claim_prefix(prefix)
    binary = prefix / "bin" / tool.name
    stamp = prefix / f"{tool.name}.json"
    url, sha256 = find_asset(tool, version, name, timeout)
    wanted = {"version": version, "asset": name, "sha256": sha256}
    try:
        if (
            binary.is_file()
            and json.loads(stamp.read_text(encoding="utf-8")) == wanted
            and reported_version(tool, binary) == version
        ):
            print(f"INFO: {tool.name} {version} already present at {binary}")
            return binary
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ToolchainError):
        pass
    try:
        stamp.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{tool.name}-", dir=prefix) as work:
            archive = Path(work) / name
            download(url, sha256, archive, timeout)
            staged = Path(work) / tool.name
            extract_binary(archive, tool.name, staged)
            reported = reported_version(tool, staged)
            if reported != version:
                raise ToolchainError(
                    f"{name} reports version {reported}, expected {version}"
                )
            os.replace(staged, binary)
        stamp.write_text(json.dumps(wanted, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise ToolchainError(f"cannot install {tool.name} into {prefix}: {error}") from error
    print(f"OK: installed {tool.name} {version} ({name}, sha256 {sha256}) at {binary}")
    return binary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read the Beads and Dolt pins at a git ref, or fetch pinned bd and "
            "dolt release binaries into a private prefix."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    pins = commands.add_parser("pins", help="print the bd and dolt pins as JSON")
    pins.add_argument("--repo-root", type=Path, default=Path.cwd())
    pins.add_argument("--ref", help="git ref to read (default: the working tree file)")
    fetch = commands.add_parser(
        "fetch",
        help=(
            "download, verify and unpack bd and/or dolt into PREFIX/bin; PREFIX "
            "must be new, empty, or one this helper created"
        ),
    )
    fetch.add_argument("--bd", help="bd version to fetch, without a v prefix")
    fetch.add_argument("--dolt", help="dolt version to fetch, without a v prefix")
    fetch.add_argument("--prefix", type=Path, required=True)
    fetch.add_argument("--timeout", type=float, default=120.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "pins":
            print(json.dumps(read_pins(args.repo_root.resolve(), args.ref), sort_keys=True))
            return 0
        requested = {
            name: require_version(value, name)
            for name, value in (("bd", args.bd), ("dolt", args.dolt))
            if value is not None
        }
        if not requested:
            raise ToolchainError("fetch needs --bd, --dolt, or both")
        prefix = args.prefix.resolve()
        for name, version in requested.items():
            install(TOOLS[name], version, prefix, args.timeout)
        return 0
    except ToolchainError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
