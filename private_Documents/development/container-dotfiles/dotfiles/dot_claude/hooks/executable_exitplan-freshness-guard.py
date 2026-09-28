#!/usr/bin/env python3
"""Deny an ExitPlanMode call whose attached plan is older than the plan file.

Claude Code snapshots the plan before a same-reply Write/Edit runs
(anthropics/claude-code#96553); PreToolUse runs after it, so the file is current.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


STALE_REASON = (
    "The plan attached to this ExitPlanMode call does not match {path}, "
    "usually because the plan file was changed in the same reply. Call "
    "ExitPlanMode again, on its own in a new reply, so the reviewer sees the "
    "current plan. If this call was already on its own, stop and tell the user."
)


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def deny_reason(payload: dict[str, Any]) -> str | None:
    if payload.get("tool_name") != "ExitPlanMode":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    plan = tool_input.get("plan")
    plan_path = tool_input.get("planFilePath")
    if not isinstance(plan, str) or not isinstance(plan_path, str) or not plan_path:
        return None
    try:
        on_disk = Path(plan_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if normalize(plan) == normalize(on_disk):
        return None
    return STALE_REASON.format(path=plan_path)


def main() -> int:
    try:
        payload = json.load(sys.stdin.buffer)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    reason = deny_reason(payload)
    if reason is None:
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
