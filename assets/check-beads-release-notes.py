#!/usr/bin/env python3
"""Fail closed when newer Beads releases report a possible data incident, or when
beads-sync's clone-local FK list no longer matches the proposed bd release.
Also print, as INFO only, the notes for every release the pin change adopts."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from typing import Any


API_URL = "https://api.github.com/repos/gastownhall/beads/releases?per_page=100"
RELEASES_API_PATH = "/repos/gastownhall/beads/releases"
RELEASES_API_PATHS = frozenset((RELEASES_API_PATH, "/repositories/1074561042/releases"))
CLONE_LOCAL_FKS_SOURCE = "cmd/bd/doctor/fix/clone_local_fks.go"
CONTENTS_API_PATH = f"/repos/gastownhall/beads/contents/{CLONE_LOCAL_FKS_SOURCE}"
CONTENTS_API_PATHS = frozenset((CONTENTS_API_PATH,))
CHANGELOG_SOURCE = "CHANGELOG.md"
CHANGELOG_API_PATH = f"/repos/gastownhall/beads/contents/{CHANGELOG_SOURCE}"
CHANGELOG_API_PATHS = frozenset((CHANGELOG_API_PATH,))
CHANGELOG_URL = "https://github.com/gastownhall/beads/blob/v{version}/CHANGELOG.md"
CHANGELOG_VERSION_HEADING = re.compile(r"^## \[(?P<version>[^\]\s]+)\]")
MARKDOWN_HEADING = re.compile(r"^(?P<marks>#{1,6})[ \t]+(?P<title>.+?)[ \t#]*$")
NOTE_HEADING = re.compile(
    r"upgrad|known issue|breaking|behaviou?r|migrat|deprecat|release notes",
    re.IGNORECASE,
)
GIT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@-]*$")
ACKS_PATH = Path("configs/beads-release-note-acks.json")
ACK_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NOTE_LINE_LIMIT = 120
NOTE_INTRO_LIMIT = 30
NOTE_HAZARD_LIMIT = 25
NOTE_LINE_WIDTH = 400
SYNC_SCRIPT_PATHS = (Path("assets/beads-sync.sh"), Path("assets/beads-sync.ps1"))
UPSTREAM_FK_BLOCK = re.compile(
    r"var CloneLocalFKs = \[\]CloneLocalFK\{\n(?P<body>.*?)\n\}", re.DOTALL
)
UPSTREAM_FK_ENTRY = re.compile(
    r'\{Table:\s*"(?P<table>[a-z_]+)",\s*Constraint:\s*"(?P<constraint>[a-z_]+)",'
    r'\s*Column:\s*"(?P<column>[a-z_]+)",\s*RefTable:\s*"(?P<ref_table>[a-z_]+)",'
    r'\s*RefColumn:\s*"(?P<ref_column>[a-z_]+)"\}'
)
SYNC_FK_ENTRY = re.compile(
    r"""^\s+["'](?P<spec>(?:[a-z_]+\|){4}[a-z_]+)["']\s*$""", re.MULTILINE
)
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
CONTROL_CHARACTERS = re.compile(
    r"[\x00-\x1f\x7f-\x9f\xad\u061c\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]"
)
LINE_BREAK = re.compile(r"\r\n?|\n")
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
    except (OSError, UnicodeDecodeError) as error:
        raise GuardError(f"cannot read {path}: {error}") from error


def parse_host_pin(path: Path) -> SemVer:
    return parse_host_pin_text(read_text(path), str(path))


def parse_host_pin_text(text: str, path: str) -> SemVer:
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


def require_github_api_url(
    url: str, *, paths: frozenset[str] = RELEASES_API_PATHS, kind: str = "pagination URL"
) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise GuardError(f"GitHub API returned an invalid {kind}") from error
    if (
        parts.scheme != "https"
        or parts.hostname != "api.github.com"
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.path not in paths
        or parts.fragment
    ):
        raise GuardError(f"refusing a {kind} outside the Beads GitHub API")
    return url


def github_headers(accept: str) -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {
        "Accept": accept,
        "User-Agent": "dotfiles-beads-release-guard/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_releases(timeout: float, *, opener: Any | None = None) -> list[Any]:
    headers = github_headers("application/vnd.github+json")

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
    except (
        urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException
    ) as error:
        raise GuardError(f"GitHub releases API request failed: {error}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GuardError(f"GitHub releases API returned invalid JSON: {error}") from error
    return releases


def fetch_contents(
    api_path: str,
    paths: frozenset[str],
    source: str,
    version: SemVer,
    timeout: float,
    *,
    opener: Any | None = None,
    recheck: str = "",
) -> str:
    url = require_github_api_url(
        f"https://api.github.com{api_path}?ref=v{version}",
        paths=paths,
        kind="contents URL",
    )
    request = urllib.request.Request(
        url, headers=github_headers("application/vnd.github.raw+json")
    )
    opener = opener or urllib.request.build_opener(RejectRedirects)
    try:
        with opener.open(request, timeout=timeout) as response:
            require_github_api_url(response.geturl(), paths=paths, kind="contents URL")
            return response.read().decode("utf-8")
    except GuardError:
        raise
    except urllib.error.HTTPError as error:
        raise GuardError(
            f"GitHub contents API returned HTTP {error.code} for {source}{recheck}"
        ) from error
    except (
        urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException
    ) as error:
        raise GuardError(f"GitHub contents API request failed: {error}{recheck}") from error
    except UnicodeDecodeError as error:
        raise GuardError(f"{source} is not UTF-8{recheck}") from error


def fetch_clone_local_fks_source(
    version: SemVer, timeout: float, *, opener: Any | None = None
) -> str:
    return fetch_contents(
        CONTENTS_API_PATH,
        CONTENTS_API_PATHS,
        CLONE_LOCAL_FKS_SOURCE,
        version,
        timeout,
        opener=opener,
        recheck=f"; re-verify the clone-local FK list against bd v{version} by hand",
    )


def fetch_changelog(version: SemVer, timeout: float, *, opener: Any | None = None) -> str:
    return fetch_contents(
        CHANGELOG_API_PATH, CHANGELOG_API_PATHS, CHANGELOG_SOURCE, version, timeout,
        opener=opener,
    )


def parse_upstream_clone_local_fks(text: str) -> set[tuple[str, ...]]:
    block = UPSTREAM_FK_BLOCK.search(text)
    if not block:
        raise GuardError(
            f"cannot find CloneLocalFKs in {CLONE_LOCAL_FKS_SOURCE}; "
            "re-verify the clone-local FK list by hand"
        )
    body = block.group("body")
    entries = [
        (m["table"], m["constraint"], m["column"], m["ref_table"], m["ref_column"])
        for m in UPSTREAM_FK_ENTRY.finditer(body)
    ]
    if not entries or len(entries) != body.count("{Table:"):
        raise GuardError(
            f"cannot parse every CloneLocalFKs entry in {CLONE_LOCAL_FKS_SOURCE}; "
            "re-verify the clone-local FK list by hand"
        )
    return set(entries)


def read_sync_clone_local_fks(repo_root: Path, path: Path) -> set[tuple[str, ...]]:
    entries = {
        tuple(m["spec"].split("|"))
        for m in SYNC_FK_ENTRY.finditer(read_text(repo_root / path))
    }
    if not entries:
        raise GuardError(f"no clone-local FK list found in {path.as_posix()}")
    return entries


def check_clone_local_fks(
    proposed: SemVer, upstream: set[tuple[str, ...]], repo_root: Path
) -> int:
    drift = False
    for path in SYNC_SCRIPT_PATHS:
        local = read_sync_clone_local_fks(repo_root, path)
        for table, constraint, *_ in sorted(upstream - local):
            drift = True
            print(
                f"ERROR: bd {proposed} defines clone-local FK {table}.{constraint} "
                f"that {path.as_posix()} does not re-link",
                file=sys.stderr,
            )
        for table, constraint, *_ in sorted(local - upstream):
            drift = True
            print(
                f"ERROR: {path.as_posix()} re-links {table}.{constraint}, "
                f"which bd {proposed} does not define",
                file=sys.stderr,
            )
    if drift:
        print(
            f"ERROR: update the clone-local FK lists in beads-sync to match "
            f"{CLONE_LOCAL_FKS_SOURCE} at v{proposed}",
            file=sys.stderr,
        )
        return 1
    print(f"OK: beads-sync's clone-local FK list matches bd {proposed}")
    return 0


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


def sanitize_notes(text: str) -> str:
    return CONTROL_CHARACTERS.sub(" ", ANSI_ESCAPE.sub("", text))


def normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", sanitize_notes(line)).strip()


def hazard_hits(text: str) -> list[tuple[str, str]]:
    """Return (pattern label, matched line) pairs; a match that wraps spans its lines."""
    text = "\n".join(
        CONTROL_CHARACTERS.sub(" ", ANSI_ESCAPE.sub("", line))
        for line in LINE_BREAK.split(text)
    )
    found: list[tuple[int, str, str]] = []
    for label, pattern in HAZARD_PATTERNS:
        for match in pattern.finditer(text):
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            line = normalize_line(text[start : len(text) if end == -1 else end])
            found.append((start, label, line))
    hits: list[tuple[str, str]] = []
    for _, label, line in sorted(found):
        if (label, line) not in hits:
            hits.append((label, line))
    return hits


def load_acks(repo_root: Path) -> frozenset[tuple[str, str]]:
    path = repo_root / ACKS_PATH
    if not path.exists():
        return frozenset()
    try:
        payload = json.loads(read_text(path))
    except json.JSONDecodeError as error:
        raise GuardError(f"invalid JSON in {ACKS_PATH.as_posix()}: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise GuardError(f"{ACKS_PATH.as_posix()} must be an object with schema_version 1")
    entries = payload.get("acks")
    if not isinstance(entries, list):
        raise GuardError(f"{ACKS_PATH.as_posix()} must hold an acks array")
    labels = {label for label, _ in HAZARD_PATTERNS}
    acks: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        where = f"{ACKS_PATH.as_posix()} ack {index}"
        if not isinstance(entry, dict):
            raise GuardError(f"{where} is not an object")
        for field in ("line", "pattern", "reason", "reviewed"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise GuardError(f"{where} has invalid or missing {field}")
        if entry["pattern"] not in labels:
            raise GuardError(f"{where} names unknown pattern {entry['pattern']!r}")
        if not ACK_DATE_PATTERN.fullmatch(entry["reviewed"]):
            raise GuardError(f"{where} needs a YYYY-MM-DD reviewed date")
        acks.add((entry["pattern"], normalize_line(entry["line"])))
    return frozenset(acks)


def evaluate(
    proposed: SemVer,
    releases: dict[SemVer, Release],
    acks: frozenset[tuple[str, str]] = frozenset(),
) -> int:
    if proposed not in releases:
        raise GuardError(f"proposed Beads release {proposed} is absent from GitHub releases")
    successors = sorted(
        (release for version, release in releases.items() if version > proposed),
        key=lambda release: release.version,
    )
    if not successors:
        print(f"OK: no published Beads releases are newer than {proposed}")
        report_stale_acks(acks, set())
        return 0

    hazardous = False
    seen: set[tuple[str, str]] = set()
    for release in successors:
        print(
            f"INFO: newer Beads release {release.tag} published "
            f"{release.published_at}: {release.url}"
        )
        for label, line in hazard_hits(f"{release.name}\n{release.body}"):
            first = (label, line) not in seen
            seen.add((label, line))
            if (label, line) in acks:
                if first:
                    print(f"INFO: acknowledged {label!r} match in {release.tag}: {line}")
                continue
            hazardous = True
            if first:
                print(
                    f"ERROR: {release.tag} release notes match {label!r}: {line}",
                    file=sys.stderr,
                )
                print(
                    "ERROR:   to acknowledge after review, add to "
                    f"{ACKS_PATH.as_posix()}: "
                    + json.dumps(
                        {
                            "line": line,
                            "pattern": label,
                            "reason": "<why this is not an incident>",
                            "reviewed": "<YYYY-MM-DD>",
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                )
    report_stale_acks(acks, seen)
    if hazardous:
        print(
            "ERROR: newer Beads release notes require manual incident review",
            file=sys.stderr,
        )
        return 1
    print(
        f"OK: {len(successors)} newer Beads release(s) contain no "
        "unacknowledged hazard terms"
    )
    return 0


def report_stale_acks(
    acks: frozenset[tuple[str, str]], seen: set[tuple[str, str]]
) -> None:
    for label, line in sorted(acks - seen):
        print(
            f"INFO: stale acknowledgement in {ACKS_PATH.as_posix()} "
            f"matches no newer release ({label!r}): {line}"
        )


def default_base_ref() -> str:
    branch = (
        os.environ.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME")
        or os.environ.get("CI_DEFAULT_BRANCH")
        or "main"
    )
    return f"origin/{branch}"


def read_base_pin(repo_root: Path, ref: str) -> SemVer:
    if not GIT_REF_PATTERN.fullmatch(ref):
        raise GuardError(f"refusing base ref {ref!r}")
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
        raise GuardError(f"git could not read {ref}: {error}") from error
    if completed.returncode != 0:
        raise GuardError(f"git could not read .chezmoidata.yaml at {ref}")
    return parse_host_pin_text(completed.stdout, f".chezmoidata.yaml at {ref}")


def changelog_sections(text: str) -> dict[SemVer, list[str]]:
    sections: dict[SemVer, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = None
            match = CHANGELOG_VERSION_HEADING.match(line)
            if match:
                try:
                    version = SemVer.parse(match.group("version"), allow_v_prefix=True)
                except ValueError:
                    continue
                current = sections.setdefault(version, [])
            continue
        if current is not None:
            current.append(line)
    return sections


def note_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Return (title, body) for each heading that reads like upgrade guidance."""
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    level = 0
    fenced = False
    for line in lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced
        match = None if fenced else MARKDOWN_HEADING.match(line)
        if match:
            depth = len(match.group("marks"))
            if current is not None and depth <= level:
                current = None
            if current is None and NOTE_HEADING.search(match.group("title")):
                current = []
                level = depth
                sections.append((match.group("title"), current))
                continue
        if current is not None:
            current.append(line)
    return sections


def intro_lines(lines: list[str]) -> list[str]:
    intro: list[str] = []
    for line in lines:
        if MARKDOWN_HEADING.match(line):
            break
        intro.append(line)
    return intro


def print_note_block(title: str, lines: list[str], limit: int = NOTE_LINE_LIMIT) -> None:
    cleaned = [sanitize_notes(line).rstrip()[:NOTE_LINE_WIDTH] for line in lines]
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    if not cleaned:
        return
    print(f"INFO:   {normalize_line(title)}:")
    for line in cleaned[:limit]:
        print(f"INFO:   | {line}")
    if len(cleaned) > limit:
        print(f"INFO:   | ... {len(cleaned) - limit} more line(s); read the source")


def report_adopted_range(
    current: SemVer,
    proposed: SemVer,
    base_ref: str,
    releases: dict[SemVer, Release],
    changelog: str | None,
) -> None:
    if current == proposed:
        print(f"INFO: Beads pin {proposed} is unchanged from {base_ref}; no adopted range")
        return
    if current > proposed:
        print(
            f"INFO: Beads pin moves down from {current} ({base_ref}) to {proposed}; "
            "no adopted range"
        )
        return
    sections = changelog_sections(changelog) if changelog is not None else {}
    versions = sorted(
        version
        for version in set(releases) | set(sections)
        if current < version <= proposed
    )
    print(
        f"INFO: this change adopts {len(versions)} Beads release(s) after "
        f"{current} ({base_ref}), up to {proposed}"
    )
    for version in versions:
        release = releases.get(version)
        where = release.url if release else "no GitHub release"
        print(f"INFO: adopted Beads release {version}: {where}")
        print(
            f"INFO:   changelog section [{version}] in "
            + CHANGELOG_URL.format(version=proposed)
        )
        printed = False
        body_lines = sections.get(version)
        if body_lines is not None:
            print_note_block("Summary", intro_lines(body_lines), NOTE_INTRO_LIMIT)
            for title, lines in note_sections(body_lines):
                print_note_block(f"CHANGELOG {title}", lines)
                printed = True
        release_lines: list[str] = []
        if release:
            for line in release.body.splitlines():
                if re.match(r"^##\s+Changelog\s*$", line):
                    break
                release_lines.append(line)
            for title, lines in note_sections(release_lines):
                print_note_block(f"Release page {title}", lines)
                printed = True
        if not printed:
            print("INFO:   no upgrade-notes or known-issues section found for this release")
        hits = hazard_hits("\n".join((body_lines or []) + release_lines))
        for label, line in hits[:NOTE_HAZARD_LIMIT]:
            print(f"INFO:   mentions {label!r}: {line[:NOTE_LINE_WIDTH]}")
        if len(hits) > NOTE_HAZARD_LIMIT:
            print(
                f"INFO:   ... {len(hits) - NOTE_HAZARD_LIMIT} more hazard-term line(s) "
                "in this release's notes"
            )


def adopted_range_report(
    args: argparse.Namespace,
    repo_root: Path,
    proposed: SemVer,
    releases: dict[SemVer, Release],
) -> None:
    """Informational only: every failure here is reported and swallowed."""
    try:
        base_ref = args.base_ref or default_base_ref()
        current = read_base_pin(repo_root, base_ref)
        changelog: str | None = None
        if current < proposed:
            try:
                if args.changelog_file:
                    changelog = read_text(args.changelog_file.resolve())
                elif args.releases_file:
                    print(f"INFO: {CHANGELOG_SOURCE} not read: no --changelog-file given")
                else:
                    changelog = fetch_changelog(proposed, args.timeout)
            except GuardError as error:
                print(f"INFO: {CHANGELOG_SOURCE} not read: {error}")
        report_adopted_range(current, proposed, base_ref, releases, changelog)
    except Exception as error:  # noqa: BLE001
        print(f"INFO: adopted-range report skipped: {normalize_line(str(error))}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the two Beads pins, print the notes of every release "
            "the pin change adopts, scan newer GitHub release notes for "
            "incident language, and check beads-sync's clone-local FK "
            "list against the proposed release."
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
        "--clone-local-fks-file",
        type=Path,
        help=f"read bd's {CLONE_LOCAL_FKS_SOURCE} from this file instead of the API",
    )
    parser.add_argument(
        "--changelog-file",
        type=Path,
        help=f"read bd's {CHANGELOG_SOURCE} from this file instead of the API",
    )
    parser.add_argument(
        "--base-ref",
        help=(
            "git ref holding the current pin, for the adopted-range report "
            "(default: origin/ plus the merge request target branch, else the "
            "CI default branch, else main)"
        ),
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
        repo_root = args.repo_root.resolve()
        proposed = read_pins(repo_root)
        items = (
            load_fixture(args.releases_file.resolve())
            if args.releases_file
            else fetch_releases(args.timeout)
        )
        releases = parse_releases(items)
        acks = load_acks(repo_root)
        adopted_range_report(args, repo_root, proposed, releases)
        notes_rc = evaluate(proposed, releases, acks)
        source = (
            read_text(args.clone_local_fks_file.resolve())
            if args.clone_local_fks_file
            else fetch_clone_local_fks_source(proposed, args.timeout)
        )
        fks_rc = check_clone_local_fks(
            proposed, parse_upstream_clone_local_fks(source), repo_root
        )
        return max(notes_rc, fks_rc)
    except GuardError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
