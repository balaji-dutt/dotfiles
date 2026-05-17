#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import sys
import unicodedata


BRANCH_TYPES = (
    "feat",
    "fix",
    "chore",
    "docs",
    "refactor",
    "test",
    "build",
    "ci",
)

DISALLOWED_REF_CHARS = set(" ~^:?*[\\")


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_value.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-./")


def validate_ref_name(ref_name: str) -> None:
    if not ref_name:
        raise ValueError("Generated branch name is empty.")
    if ref_name == "@":
        raise ValueError("Generated branch name cannot be '@'.")
    if ref_name.startswith("/") or ref_name.endswith("/"):
        raise ValueError("Generated branch name cannot start or end with '/'.")
    if "//" in ref_name:
        raise ValueError("Generated branch name cannot contain '//'.")
    if ".." in ref_name:
        raise ValueError("Generated branch name cannot contain '..'.")
    if "@{" in ref_name:
        raise ValueError("Generated branch name cannot contain '@{'.")
    if ref_name.endswith("."):
        raise ValueError("Generated branch name cannot end with '.'.")

    for char in ref_name:
        if ord(char) < 32 or ord(char) == 127 or char in DISALLOWED_REF_CHARS:
            raise ValueError(f"Generated branch name contains invalid character: {char!r}.")

    for component in ref_name.split("/"):
        if not component:
            raise ValueError("Generated branch name cannot contain empty path components.")
        if component.startswith("."):
            raise ValueError("Generated branch name components cannot start with '.'.")
        if component.endswith(".lock"):
            raise ValueError("Generated branch name components cannot end with '.lock'.")


def build_session_name(branch_type: str, description: str) -> str:
    slug = slugify(description)
    if not slug:
        raise ValueError("Description must contain at least one letter or number.")

    ref_name = f"{branch_type}/{slug}"
    validate_ref_name(ref_name)
    return ref_name


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a PR-friendly Agent of Empires session name."
    )
    parser.add_argument("--type", choices=BRANCH_TYPES, required=True)
    parser.add_argument("--description", required=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        session_name = build_session_name(args.type, args.description)
    except ValueError as exc:
        print(f"aoe-session-name: {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(session_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
