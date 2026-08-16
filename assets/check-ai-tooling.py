#!/usr/bin/env python3
"""Check AI runtime declarations against the repository support matrix."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_DEPENDENCIES = frozenset(
    {
        "beads",
        "claude-code",
        "codebase-memory-mcp",
        "deepwiki",
        "dolt",
        "jq",
        "opencode",
        "plannotator",
    }
)
SUPPORTED_SOURCE_FORMATS = frozenset(
    {"claude-agent-frontmatter", "claude-mcp-json", "opencode-jsonc"}
)
DECISION_RE = re.compile(
    r"^\*\*(?:Supported|Unsupported)(?:\s+[^*]+)?\*\*\s+—\s+\S"
)


class CheckFailure(ValueError):
    """An actionable configuration or parsing failure."""


@dataclass(frozen=True)
class Declaration:
    kind: str
    identity: str
    source: Path


@dataclass(frozen=True)
class CheckResult:
    errors: tuple[str, ...]
    declarations: tuple[Declaration, ...]


def strip_jsonc_comments(text: str) -> str:
    """Remove JSONC comments without changing string contents."""

    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        character = text[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue

        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue

        if character == "/" and index + 1 < len(text):
            marker = text[index + 1]
            if marker == "/":
                index += 2
                while index < len(text) and text[index] not in "\r\n":
                    index += 1
                continue
            if marker == "*":
                index += 2
                while index + 1 < len(text) and text[index : index + 2] != "*/":
                    output.append("\n" if text[index] == "\n" else " ")
                    index += 1
                if index + 1 >= len(text):
                    raise CheckFailure("unterminated JSONC block comment")
                index += 2
                continue

        output.append(character)
        index += 1

    if in_string:
        raise CheckFailure("unterminated JSON string")
    return "".join(output)


def strip_jsonc_trailing_commas(text: str) -> str:
    """Remove commas followed only by whitespace and a closing delimiter."""

    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        character = text[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            index += 1
            continue

        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue

        if character == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue

        output.append(character)
        index += 1

    return "".join(output)


def load_json(path: Path, *, jsonc: bool = False) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CheckFailure(f"cannot read file: {error}") from error

    if jsonc:
        text = strip_jsonc_trailing_commas(strip_jsonc_comments(text))
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        label = "JSONC" if jsonc else "JSON"
        raise CheckFailure(
            f"invalid {label} at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error


def normalize_identity(value: str) -> str:
    normalized = value.strip().strip('"\'').replace("\\", "/")
    normalized = normalized.rsplit("/", 1)[-1].casefold()
    for suffix in (".exe", ".cmd", ".ps1"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


def command_identity(value: Any) -> str:
    if isinstance(value, list):
        if not value or not isinstance(value[0], str):
            raise CheckFailure("local MCP command must start with a string executable")
        return normalize_identity(value[0])
    if not isinstance(value, str) or not value.strip():
        raise CheckFailure("local MCP command must be a non-empty string or array")
    token = value.strip().split(maxsplit=1)[0]
    return normalize_identity(token)


def discover_opencode(path: Path) -> list[Declaration]:
    payload = load_json(path, jsonc=True)
    if not isinstance(payload, dict):
        raise CheckFailure("OpenCode configuration root must be an object")
    servers = payload.get("mcp", {})
    if not isinstance(servers, dict):
        raise CheckFailure("OpenCode mcp field must be an object")

    declarations: list[Declaration] = []
    for name, server in servers.items():
        if not isinstance(name, str) or not isinstance(server, dict):
            raise CheckFailure("OpenCode MCP entries must be named objects")
        if server.get("enabled") is False:
            continue
        server_type = server.get("type")
        if server_type == "local":
            declarations.append(
                Declaration("local-command", command_identity(server.get("command")), path)
            )
        elif server_type == "remote":
            if not isinstance(server.get("url"), str) or not server["url"].strip():
                raise CheckFailure(f"remote MCP server {name!r} must have a URL")
            declarations.append(Declaration("remote-mcp", name.casefold(), path))
        else:
            raise CheckFailure(
                f"enabled OpenCode MCP server {name!r} must have type local or remote"
            )
    return declarations


def discover_claude_mcp(path: Path) -> list[Declaration]:
    payload = load_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), dict):
        raise CheckFailure("Claude MCP configuration must contain a servers object")

    declarations: list[Declaration] = []
    for name, server in payload["servers"].items():
        if not isinstance(name, str) or not isinstance(server, dict):
            raise CheckFailure("Claude MCP entries must be named objects")
        if server.get("enabled") is False:
            continue
        transport = server.get("transport")
        if transport == "stdio":
            declarations.append(
                Declaration("local-command", command_identity(server.get("command")), path)
            )
        elif transport in {"http", "sse"}:
            if not isinstance(server.get("url"), str) or not server["url"].strip():
                raise CheckFailure(f"remote MCP server {name!r} must have a URL")
            declarations.append(Declaration("remote-mcp", name.casefold(), path))
        else:
            raise CheckFailure(
                f"enabled Claude MCP server {name!r} has unsupported transport {transport!r}"
            )
    return declarations


def yaml_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith("["):
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            raise CheckFailure(f"invalid inline command array: {error.msg}") from error
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def discover_claude_agent(path: Path) -> list[Declaration]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise CheckFailure(f"cannot read file: {error}") from error
    if not lines or lines[0].strip() != "---":
        return []
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as error:
        raise CheckFailure("unterminated Claude agent frontmatter") from error

    frontmatter = lines[1:end]
    parent_matches = [
        (index, line)
        for index, line in enumerate(frontmatter)
        if re.match(r"^\s*mcpServers\s*:", line)
    ]
    if not parent_matches:
        return []
    parent_index, parent_line = parent_matches[0]
    if parent_line.strip() != "mcpServers:":
        raise CheckFailure("inline mcpServers frontmatter is unsupported")
    parent_indent = len(frontmatter[parent_index]) - len(frontmatter[parent_index].lstrip())
    server_indent: int | None = None
    current_name: str | None = None
    servers: dict[str, dict[str, Any]] = {}

    for line in frontmatter[parent_index + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= parent_indent:
            break
        stripped = line.strip()
        server_match = re.fullmatch(r"([A-Za-z0-9_.-]+):", stripped)
        if server_match and (server_indent is None or indent == server_indent):
            server_indent = indent
            current_name = server_match.group(1)
            servers[current_name] = {}
            continue
        if current_name is None or server_indent is None or indent <= server_indent:
            raise CheckFailure("malformed mcpServers frontmatter block")
        if ":" not in stripped:
            raise CheckFailure(f"malformed MCP property for server {current_name!r}")
        key, raw_value = stripped.split(":", 1)
        servers[current_name][key.strip()] = yaml_scalar(raw_value)

    declarations: list[Declaration] = []
    for name, server in servers.items():
        if server.get("enabled") is False:
            continue
        server_type = server.get("type", "stdio" if "command" in server else None)
        if server_type == "stdio":
            declarations.append(
                Declaration("local-command", command_identity(server.get("command")), path)
            )
        elif server_type in {"http", "sse"}:
            if not isinstance(server.get("url"), str) or not server["url"].strip():
                raise CheckFailure(f"remote frontmatter MCP server {name!r} must have a URL")
            declarations.append(Declaration("remote-mcp", name.casefold(), path))
        else:
            raise CheckFailure(
                f"enabled frontmatter MCP server {name!r} has unsupported type {server_type!r}"
            )
    return declarations


DISCOVERERS = {
    "claude-agent-frontmatter": discover_claude_agent,
    "claude-mcp-json": discover_claude_mcp,
    "opencode-jsonc": discover_opencode,
}


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def split_markdown_row(line: str) -> list[str]:
    content = line.strip()
    if content.startswith("|"):
        content = content[1:]
    if content.endswith("|"):
        content = content[:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in content:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def matrix_rows(path: Path) -> tuple[dict[str, tuple[list[str], int]], list[str]]:
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        return {}, [f"{path}: cannot read support matrix: {error}"]

    rows: dict[str, tuple[list[str], int]] = {}
    for line_number, line in enumerate(lines, 1):
        if not line.lstrip().startswith("|"):
            continue
        cells = split_markdown_row(line)
        if not cells:
            continue
        raw_name = cells[0]
        name = raw_name.replace("`", "").strip()
        if name in {"Tool", "Foundation", "Helper"} or re.fullmatch(r":?-+:?", name):
            continue
        if name in rows:
            errors.append(f"{path}:{line_number}: duplicate matrix row {name!r}")
            continue
        rows[name] = (cells, line_number)
    return rows, errors


def check_repository(repo_root: Path, policy_path: Path | None = None) -> CheckResult:
    root = repo_root.resolve()
    selected_policy = policy_path or root / "configs/ai-tooling-support.json"
    if not selected_policy.is_absolute():
        selected_policy = root / selected_policy
    errors: list[str] = []

    try:
        policy = load_json(selected_policy)
    except CheckFailure as error:
        return CheckResult((f"{selected_policy}: {error}",), ())
    if not isinstance(policy, dict):
        return CheckResult((f"{selected_policy}: policy root must be an object",), ())
    if policy.get("schema_version") != 1:
        errors.append(f"{selected_policy}: schema_version must be 1")

    matrix_value = policy.get("matrix")
    if not isinstance(matrix_value, str) or not matrix_value.strip():
        errors.append(f"{selected_policy}: matrix must be a repository-relative path")
        matrix_path = root / "docs/inventory/ai-tooling.md"
    else:
        matrix_path = root / matrix_value

    dependencies = policy.get("dependencies")
    dependency_by_id: dict[str, dict[str, Any]] = {}
    command_aliases: dict[str, str] = {}
    mcp_aliases: dict[str, str] = {}
    if not isinstance(dependencies, list):
        errors.append(f"{selected_policy}: dependencies must be an array")
        dependencies = []
    for index, dependency in enumerate(dependencies):
        location = f"{selected_policy}: dependencies[{index}]"
        if not isinstance(dependency, dict):
            errors.append(f"{location} must be an object")
            continue
        dependency_id = dependency.get("id")
        matrix_row = dependency.get("matrix_row")
        if not isinstance(dependency_id, str) or not dependency_id:
            errors.append(f"{location}.id must be a non-empty string")
            continue
        if dependency_id in dependency_by_id:
            errors.append(f"{location}: duplicate dependency id {dependency_id!r}")
            continue
        if not isinstance(matrix_row, str) or not matrix_row:
            errors.append(f"{location}.matrix_row must be a non-empty string")
        dependency_by_id[dependency_id] = dependency
        for field, aliases, target in (
            ("command_aliases", dependency.get("command_aliases"), command_aliases),
            ("mcp_server_aliases", dependency.get("mcp_server_aliases"), mcp_aliases),
        ):
            if not isinstance(aliases, list) or not all(
                isinstance(alias, str) and alias.strip() for alias in aliases
            ):
                errors.append(f"{location}.{field} must be an array of non-empty strings")
                continue
            for alias in aliases:
                normalized = normalize_identity(alias)
                previous = target.get(normalized)
                if previous is not None and previous != dependency_id:
                    errors.append(
                        f"{location}.{field}: alias {alias!r} is already owned by {previous!r}"
                    )
                target[normalized] = dependency_id

    for missing in sorted(REQUIRED_DEPENDENCIES - dependency_by_id.keys()):
        errors.append(f"{selected_policy}: missing required dependency classification {missing!r}")

    rows, row_errors = matrix_rows(matrix_path)
    errors.extend(row_errors)
    for dependency_id, dependency in dependency_by_id.items():
        row_name = dependency.get("matrix_row")
        if not isinstance(row_name, str):
            continue
        row = rows.get(row_name)
        if row is None:
            errors.append(
                f"{matrix_path}: dependency {dependency_id!r} references missing matrix row {row_name!r}"
            )
            continue
        cells, line_number = row
        if len(cells) != 5:
            errors.append(
                f"{matrix_path}:{line_number}: row {row_name!r} must contain one name and four platform cells"
            )
            continue
        for platform, cell in zip(
            ("Native Windows", "WSL2", "macOS", "Devcontainer"), cells[1:]
        ):
            if not DECISION_RE.match(cell):
                errors.append(
                    f"{matrix_path}:{line_number}: {row_name!r} {platform} must start with an explicit Supported/Unsupported decision followed by an owner or reason"
                )

    declarations: list[Declaration] = []
    sources = policy.get("sources")
    if not isinstance(sources, list):
        errors.append(f"{selected_policy}: sources must be an array")
        sources = []
    seen_paths: set[tuple[str, Path]] = set()
    for index, source in enumerate(sources):
        location = f"{selected_policy}: sources[{index}]"
        if not isinstance(source, dict):
            errors.append(f"{location} must be an object")
            continue
        source_format = source.get("format")
        patterns = source.get("patterns")
        if source_format not in SUPPORTED_SOURCE_FORMATS:
            errors.append(f"{location}.format is unsupported: {source_format!r}")
            continue
        if not isinstance(patterns, list) or not all(
            isinstance(pattern, str) and pattern for pattern in patterns
        ):
            errors.append(f"{location}.patterns must be an array of non-empty strings")
            continue
        for pattern in patterns:
            matches = sorted(path for path in root.glob(pattern) if path.is_file())
            if not matches:
                errors.append(f"{location}: pattern {pattern!r} matched no files")
                continue
            for path in matches:
                key = (source_format, path.resolve())
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                try:
                    declarations.extend(DISCOVERERS[source_format](path))
                except CheckFailure as error:
                    errors.append(f"{relative_path(path, root)}: {error}")

    grouped: dict[tuple[str, str], list[Path]] = {}
    for declaration in declarations:
        grouped.setdefault((declaration.kind, declaration.identity), []).append(
            declaration.source
        )
    for (kind, identity), paths in sorted(grouped.items()):
        aliases = command_aliases if kind == "local-command" else mcp_aliases
        if identity in aliases:
            continue
        locations = ", ".join(
            sorted({relative_path(path, root) for path in paths})
        )
        label = "local MCP command" if kind == "local-command" else "remote MCP server"
        errors.append(
            f"unclassified {label} {identity!r} declared in {locations}; add a policy classification and support-matrix row"
        )

    return CheckResult(tuple(errors), tuple(declarations))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the script's parent repository)",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=None,
        help="policy path, absolute or relative to --repo-root",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = check_repository(args.repo_root, args.policy)
    if result.errors:
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    identities = {(item.kind, item.identity) for item in result.declarations}
    print(
        "AI tooling drift check passed: "
        f"{len(result.declarations)} declarations, {len(identities)} runtime identities."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
