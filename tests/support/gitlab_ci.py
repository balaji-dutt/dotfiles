"""Read top-level job blocks from the repository GitLab CI file."""

from __future__ import annotations

import re
from pathlib import Path


CI_PATH = Path(__file__).resolve().parents[2] / ".gitlab-ci.yml"


def top_level_block(text: str, name: str) -> str:
    lines = text.splitlines()
    start = next(
        index for index, line in enumerate(lines) if line == f"{name}:"
    )
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.fullmatch(r"[A-Za-z0-9_.-]+:", lines[index]):
            end = index
            break
    return "\n".join(lines[start:end]) + "\n"
