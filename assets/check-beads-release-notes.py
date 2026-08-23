#!/usr/bin/env python3
"""Fail closed when newer Beads releases report a possible data incident."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from typing import Any


API_URL = "https://api.github.com/repos/gastownhall/beads/releases?per_page=100"
NPM_PIN_PATH = Path(
    "private_Documents/development/container-dotfiles/devcontainers/"
    "gitlab.com/servers-homelab/homelab-IaC/configs/npm_packages.txt"
)
SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
HOST_PIN_PATTERN = re.compile(
    r"^[ \t]*beads_version:[ \t]*"
    r'(?:"(?P<double>[^"\r\n]+)"|\'(?P<single>[^\'\r\n]+)\'|'
    r"(?P<bare>[^#\s]+))[ \t]*(?:#.*)?$",
    re.MULTILINE,
)
HAZARD_PATTERNS = (
    ("data loss", re.compile(r"\bdata(?:\s+|-)loss\b", re.IGNORECASE)),
    ("corruption", re.compile(r"\bcorrupt\w*", re.IGNORECASE)),
    ("retraction", re.compile(r"\bretract\w*", re.IGNORECASE)),
    ("recovery", re.compile(r"\brecover\w*", re.IGNORECASE)),
)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class GuardError(Exception):
    """An input or network failure that must block the update."""


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent bearer credentials from following an unexpected redirect."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        raise GuardError("GitHub releases API redirected unexpectedly")


@total_ordering
@dataclass(frozen=True, eq=False)
class SemVer:
    """SemVer 2.0 value whose equality and ordering ignore build metadata."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[int | str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str, *, allow_v_prefix: bool = False) -> SemVer:
        candidate = value[1:] if allow_v_prefix and value.startswith("v") else value
        match = SEMVER_PATTERN.fullmatch(candidate)
        if match is None:
            raise ValueError(f"not valid SemVer: {value!r}")

        prerelease: list[int | str] = []
        prerelease_text = match.group("prerelease")
        if prerelease_text:
            for identifier in prerelease_text.split("."):
                if identifier.isdigit():
                    if len(identifier) > 1 and identifier.startswith("0"):
                        raise ValueError(
                            f"numeric prerelease identifier has a leading zero: {value!r}"
                        )
                    prerelease.append(int(identifier))
                else:
                    prerelease.append(identifier)

        build_text = match.group("build")
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            tuple(prerelease),
            tuple(build_text.split(".")) if build_text else (),
        )

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(str(part) for part in self.prerelease)
        if self.build:
            value += "+" + ".".join(self.build)
        return value

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._core() == other._core() and self.prerelease == other.prerelease

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        if self._core() != other._core():
            return self._core() < other._core()
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            if isinstance(left, int) and isinstance(right, str):
                return True
            if isinstance(left, str) and isinstance(right, int):
                return False
            return left < right
        return len(self.prerelease) < len(other.prerelease)

    def _core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


@dataclass(frozen=True)
class Release:
    version: SemVer
    tag: str
    name: str
    body: str
    published_at: str
    url: str


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise GuardError(f"cannot read {path}: {error}") from error


def parse_host_pin(path: Path) -> SemVer:
    text = read_text(path)
    candidates = [
        line
        for line in text.splitlines()
        if re.match(r"^[ \t]*beads_version[ \t]*:", line)
    ]
    if len(candidates) != 1:
        raise GuardError(
            f"expected exactly one active beads_version in {path}, found {len(candidates)}"
        )
    match = HOST_PIN_PATTERN.fullmatch(candidates[0])
    if match is None:
        raise GuardError(f"malformed beads_version in {path}")
    value = match.group("double") or match.group("single") or match.group("bare")
    try:
        return SemVer.parse(value)
    except ValueError as error:
        raise GuardError(f"invalid beads_version in {path}: {error}") from error


def parse_npm_pin(path: Path) -> SemVer:
    candidates: list[str] = []
    for line in read_text(path).splitlines():
        stripped = line.strip()
        if stripped.startswith("@beads/bd@"):
            candidates.append(stripped.removeprefix("@beads/bd@"))
    if len(candidates) != 1:
        raise GuardError(
            f"expected exactly one active @beads/bd pin in {path}, found {len(candidates)}"
        )
    try:
        return SemVer.parse(candidates[0])
    except ValueError as error:
        raise GuardError(f"invalid @beads/bd pin in {path}: {error}") from error


def read_pins(repo_root: Path) -> SemVer:
    host = parse_host_pin(repo_root / ".chezmoidata.yaml")
    npm = parse_npm_pin(repo_root / NPM_PIN_PATH)
    if host != npm:
        raise GuardError(f"Beads pins disagree: host={host}, devcontainer={npm}")
    print(f"INFO: Beads host and devcontainer pins agree at {host}")
    return host


def load_fixture(path: Path) -> list[Any]:
    try:
        payload = json.loads(read_text(path))
    except json.JSONDecodeError as error:
        raise GuardError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(payload, list):
        raise GuardError(f"release data in {path} must be a JSON array")
    return payload


def next_link(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        match = re.match(r'\s*<([^>]+)>\s*;\s*rel="([^"]+)"', part)
        if match and match.group(2) == "next":
            return match.group(1)
    return None


def require_github_api_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise GuardError("GitHub releases API returned an invalid pagination URL") from error
    if (
        parts.scheme != "https"
        or parts.hostname != "api.github.com"
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.path != "/repos/gastownhall/beads/releases"
        or parts.fragment
    ):
        raise GuardError("refusing a pagination URL outside the Beads GitHub API")
    return url


def fetch_releases(timeout: float, *, opener: Any | None = None) -> list[Any]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "dotfiles-beads-release-guard/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    releases: list[Any] = []
    url: str | None = API_URL
    seen_urls: set[str] = set()
    opener = opener or urllib.request.build_opener(RejectRedirects)
    try:
        while url:
            url = require_github_api_url(url)
            if url in seen_urls or len(seen_urls) >= 20:
                raise GuardError("GitHub release pagination is cyclic or unexpectedly long")
            seen_urls.add(url)
            request = urllib.request.Request(url, headers=headers)
            with opener.open(request, timeout=timeout) as response:
                require_github_api_url(response.geturl())
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, list):
                    raise GuardError("GitHub releases API returned a non-array response")
                releases.extend(payload)
                url = next_link(response.headers.get("Link"))
    except GuardError:
        raise
    except urllib.error.HTTPError as error:
        remaining = error.headers.get("X-RateLimit-Remaining")
        detail = " (rate limit exhausted)" if remaining == "0" else ""
        raise GuardError(f"GitHub releases API returned HTTP {error.code}{detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise GuardError(f"GitHub releases API request failed: {error}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GuardError(f"GitHub releases API returned invalid JSON: {error}") from error
    return releases


def require_string(item: dict[str, Any], field: str, tag: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value:
        raise GuardError(f"release {tag!r} has invalid or missing {field}")
    return value


def parse_releases(items: list[Any]) -> dict[SemVer, Release]:
    parsed: dict[SemVer, Release] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise GuardError(f"release record {index} is not an object")
        draft = item.get("draft")
        if not isinstance(draft, bool):
            raise GuardError(f"release record {index} has invalid or missing draft flag")
        if draft:
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not tag:
            raise GuardError(f"release record {index} has invalid or missing tag_name")
        try:
            version = SemVer.parse(tag, allow_v_prefix=True)
        except ValueError:
            print(f"WARNING: ignoring non-SemVer Beads release tag {tag!r}", file=sys.stderr)
            continue
        if version in parsed:
            raise GuardError(f"multiple published releases have SemVer version {version}")

        name = item.get("name")
        body = item.get("body")
        if name is not None and not isinstance(name, str):
            raise GuardError(f"release {tag!r} has invalid name")
        if body is not None and not isinstance(body, str):
            raise GuardError(f"release {tag!r} has invalid body")
        parsed[version] = Release(
            version=version,
            tag=tag,
            name=name or "",
            body=body or "",
            published_at=require_string(item, "published_at", tag),
            url=require_string(item, "html_url", tag),
        )
    return parsed


def excerpt(text: str, start: int, end: int, radius: int = 80) -> str:
    snippet = text[max(0, start - radius) : min(len(text), end + radius)]
    snippet = CONTROL_CHARACTERS.sub(" ", snippet)
    snippet = re.sub(r"\s+", " ", snippet).strip()
    if start > radius:
        snippet = "..." + snippet
    if end + radius < len(text):
        snippet += "..."
    return snippet


def sanitize_notes(text: str) -> str:
    return CONTROL_CHARACTERS.sub(" ", ANSI_ESCAPE.sub("", text))


def evaluate(proposed: SemVer, releases: dict[SemVer, Release]) -> int:
    if proposed not in releases:
        raise GuardError(f"proposed Beads release {proposed} is absent from GitHub releases")
    successors = sorted(
        (release for version, release in releases.items() if version > proposed),
        key=lambda release: release.version,
    )
    if not successors:
        print(f"OK: no published Beads releases are newer than {proposed}")
        return 0

    hazardous = False
    for release in successors:
        print(
            f"INFO: newer Beads release {release.tag} published "
            f"{release.published_at}: {release.url}"
        )
        notes = sanitize_notes(f"{release.name}\n{release.body}")
        for label, pattern in HAZARD_PATTERNS:
            match = pattern.search(notes)
            if match:
                hazardous = True
                print(
                    f"ERROR: {release.tag} release notes match {label!r}: "
                    f"{excerpt(notes, match.start(), match.end())}",
                    file=sys.stderr,
                )
    if hazardous:
        print(
            "ERROR: newer Beads release notes require manual incident review",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {len(successors)} newer Beads release(s) contain no hazard terms")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the two Beads pins and scan newer GitHub release notes "
            "for incident language."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="repository root containing .chezmoidata.yaml (default: current directory)",
    )
    parser.add_argument(
        "--releases-file",
        type=Path,
        help="read a GitHub releases JSON array from this file instead of the API",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="GitHub API timeout in seconds (default: 30)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        proposed = read_pins(args.repo_root.resolve())
        items = (
            load_fixture(args.releases_file.resolve())
            if args.releases_file
            else fetch_releases(args.timeout)
        )
        return evaluate(proposed, parse_releases(items))
    except GuardError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
