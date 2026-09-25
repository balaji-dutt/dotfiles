#!/usr/bin/env python3
"""Shared review-gate logic for the Claude Code hooks in .claude/hooks/.

Subcommands (the Claude hook payload JSON is read from stdin):

  mark     PostToolUse (Write|Edit): raise a session-scoped gate when the
           edited file is a reviewable file inside this checkout.
  start    SubagentStart: record that a reviewer subagent is in flight.
  enforce  Stop: block stopping while the session's gate still has pending
           work, unless a reviewer started after the last mark is still in
           flight; clear the gate if the gated edits no longer exist.
  clear    SubagentStop: drop the reviewer's in-flight record, then clear the
           gate when its last verdict is DOTFILES_REVIEWER_RESULT=PASS as the
           final meaningful line of a text block or SubagentHandback message.

Gate file: .claude/.needs_dotfiles_review.<sanitized session_id>, JSON:
  {"timestamp": <last mark>, "firstTimestamp": <first mark>,
   "markedAt": <last mark, float>, "sessionID": "...",
   "files": ["repo/relative", ...]}
An unsuffixed .claude/.needs_dotfiles_review (legacy epoch-int format) is
accepted as a fallback and merged/cleared during migration.

In-flight file, one per running reviewer:
  .claude/.dotfiles_review_inflight.<sanitized session_id>.<sanitized agent_id>
containing the reviewer's start epoch (float). Records outside the
INFLIGHT_TTL_SECONDS window are deleted so a reviewer that never reports
cannot disable the gate.

Path policy comes from the config shared with the OpenCode plugins
(.opencode/plugins/review-loop-*.js): .opencode/opencode-tooling.config.jsonc.
Negations in exemptPaths are honored the same way as the OpenCode marker:
exempt = matches a positive pattern AND matches no "!" pattern.

Wrappers cd to the project dir first; all paths here are resolved against
the git toplevel of the current working directory, which in a git worktree
is the worktree root (each worktree gates only its own edits).
"""

import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import deque
from datetime import datetime

GATE_DIR = ".claude"
GATE_BASE = ".needs_dotfiles_review"
INFLIGHT_BASE = ".dotfiles_review_inflight"
HANDBACK_TOOL = "SubagentHandback"
LEGACY_OPENCODE_SENTINEL = os.path.join(".opencode", ".needs_dotfiles_review")
CONFIG_PATHS = (
    os.path.join(".opencode", "opencode-tooling.config.jsonc"),
    os.path.join(".opencode", "review-loop.config.jsonc"),
)
DEFAULT_MARKER_PREFIX = "DOTFILES_REVIEWER_RESULT"
DEFAULT_REVIEWER = "dotfiles-reviewer"

# Runtime artifacts of the review loop itself must never re-raise the gate.
RUNTIME_PREFIXES = (
    ".claude/.needs_dotfiles_review",
    ".claude/.dotfiles_review_inflight",
    ".opencode/.needs_dotfiles_review",
    ".opencode/.dotfiles_review_enforcer_state",
    ".opencode/node_modules/",
)
RUNTIME_SUFFIXES = (
    "-review-gate.log",
    "-review-marker.log",
    "-review-enforcer.log",
)

PASS_SLACK_SECONDS = 3.0
COMMIT_SLACK_SECONDS = 5
INFLIGHT_TTL_SECONDS = 45 * 60


def run_git(root, args):
    try:
        proc = subprocess.run(
            ["git", "-C", root] + args,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [line.strip().replace("\r", "") for line in proc.stdout.splitlines() if line.strip()]


def git_toplevel(directory):
    lines = run_git(directory, ["rev-parse", "--show-toplevel"])
    return lines[0] if lines else None


def same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def strip_jsonc(text):
    out = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return re.sub(r",\s*([}\]])", r"\1", "".join(out))


def load_config(root):
    cfg = {
        "exempt": [],
        "marker_prefix": DEFAULT_MARKER_PREFIX,
        "reviewer": DEFAULT_REVIEWER,
    }
    for rel in CONFIG_PATHS:
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.loads(strip_jsonc(fh.read()))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        exempt = data.get("exemptPaths")
        if isinstance(exempt, list):
            cfg["exempt"] = [p for p in exempt if isinstance(p, str) and p]
        if isinstance(data.get("resultMarkerPrefix"), str) and data["resultMarkerPrefix"]:
            cfg["marker_prefix"] = data["resultMarkerPrefix"]
        if isinstance(data.get("reviewerAgent"), str) and data["reviewerAgent"]:
            cfg["reviewer"] = data["reviewerAgent"]
        break
    return cfg


def glob_to_regex(pattern):
    pat = pattern.replace("\\", "/").lower()
    out = []
    i = 0
    while i < len(pat):
        ch = pat[i]
        if ch == "*":
            if pat[i : i + 2] == "**":
                out.append(".*")
                i += 2
                if i < len(pat) and pat[i] == "/":
                    i += 1  # "**/" also matches zero directories
            else:
                out.append("[^/]*")
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def normalize_rel(path):
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def is_exempt(rel, exempt_patterns):
    p = normalize_rel(rel).lower()
    positive = [glob_to_regex(q) for q in exempt_patterns if not q.startswith("!")]
    negative = [glob_to_regex(q[1:]) for q in exempt_patterns if q.startswith("!")]
    if not any(rx.match(p) for rx in positive):
        return False
    return not any(rx.match(p) for rx in negative)


def is_runtime_artifact(rel):
    p = normalize_rel(rel).lower()
    if os.path.basename(p) == ".ds_store":
        return True
    if any(p.startswith(prefix) for prefix in RUNTIME_PREFIXES):
        return True
    return any(p.endswith(suffix) for suffix in RUNTIME_SUFFIXES)


def sanitize_session_id(session_id):
    return re.sub(r"[^A-Za-z0-9._-]", "_", session_id)


def gate_path(root, session_id):
    base = os.path.join(root, GATE_DIR, GATE_BASE)
    if session_id:
        return base + "." + sanitize_session_id(session_id)
    return base


def read_gate(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            raw = fh.read().strip()
    except OSError:
        return None
    if raw.isdigit():
        return {"timestamp": int(raw), "files": []}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"files": []}
    return data if isinstance(data, dict) else {"files": []}


def gate_epoch(path):
    data = read_gate(path) or {}
    try:
        return int(data.get("timestamp"))
    except (TypeError, ValueError):
        pass
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return 0


def gate_candidates(root, session_id):
    paths = []
    for candidate in (
        gate_path(root, session_id) if session_id else None,
        gate_path(root, ""),
        os.path.join(root, LEGACY_OPENCODE_SENTINEL),
    ):
        if candidate and candidate not in paths and os.path.exists(candidate):
            paths.append(candidate)
    return paths


def mark_epoch(path):
    data = read_gate(path) or {}
    marked_at = data.get("markedAt")
    if isinstance(marked_at, (int, float)) and not isinstance(marked_at, bool):
        return float(marked_at)
    # Whole-second stamps round up so a same-second launch never counts as
    # starting after the edit.
    return float(gate_epoch(path) + 1)


def last_mark_epoch(root, session_id):
    return max((mark_epoch(p) for p in gate_candidates(root, session_id)), default=0.0)


def inflight_prefix(root, session_id):
    return os.path.join(
        root, GATE_DIR, INFLIGHT_BASE + "." + sanitize_session_id(session_id) + "."
    )


def inflight_path(root, session_id, agent_id):
    return inflight_prefix(root, session_id) + sanitize_session_id(agent_id)


def read_inflight(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return float(fh.read().strip())
    except (OSError, ValueError):
        return None


def inflight_records(root, session_id):
    prefix = inflight_prefix(root, session_id)
    return {path: read_inflight(path) for path in glob.glob(glob.escape(prefix) + "*")}


def remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def normalize_host_path(path):
    if not path:
        return ""
    path = os.path.expanduser(path)
    if re.match(r"^[A-Za-z]:[\\/]", path) and os.sep == "/":
        try:
            proc = subprocess.run(
                ["cygpath", "-u", path], capture_output=True, text=True, timeout=5
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return path


def parse_iso_to_epoch(ts):
    if not ts:
        return None
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------- mark ----


def cmd_mark(payload, root):
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return 0

    file_path = os.path.expanduser(file_path)
    if not os.path.isabs(file_path):
        file_path = os.path.join(payload.get("cwd") or root, file_path)
    file_path = os.path.realpath(file_path)

    # Inside-this-checkout test: the file's git toplevel must be this root.
    # A prefix test would misclassify nested worktrees (worktrees/<branch>/).
    directory = os.path.dirname(file_path)
    while directory and not os.path.isdir(directory):
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    top = git_toplevel(directory) if os.path.isdir(directory) else None
    if not top or not same_path(top, root):
        return 0

    rel = normalize_rel(os.path.relpath(file_path, os.path.realpath(root)))
    if rel.startswith("../"):
        return 0
    if is_runtime_artifact(rel):
        return 0
    if is_exempt(rel, load_config(root)["exempt"]):
        return 0

    write_gate(root, payload.get("session_id") or "", rel)
    return 0


def write_gate(root, session_id, rel):
    path = gate_path(root, session_id)
    marked_at = time.time()
    now = int(marked_at)
    files = set()
    first = now

    existing = read_gate(path)
    if existing:
        files.update(f for f in existing.get("files") or [] if isinstance(f, str))
        try:
            first = min(first, int(existing.get("firstTimestamp") or existing.get("timestamp")))
        except (TypeError, ValueError):
            pass

    # Absorb and retire the unsuffixed legacy gate once a session ID is known.
    unsuffixed = gate_path(root, "")
    if session_id and os.path.exists(unsuffixed):
        legacy = read_gate(unsuffixed) or {}
        files.update(f for f in legacy.get("files") or [] if isinstance(f, str))
        try:
            first = min(first, int(legacy.get("firstTimestamp") or legacy.get("timestamp")))
        except (TypeError, ValueError):
            pass
        try:
            os.remove(unsuffixed)
        except OSError:
            pass

    files.add(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        "timestamp": now,
        "firstTimestamp": first,
        "markedAt": marked_at,
        "sessionID": session_id,
        "files": sorted(files),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


# --------------------------------------------------------------- start ----


def cmd_start(payload, root):
    session_id = payload.get("session_id")
    agent_id = payload.get("agent_id")
    if not all(isinstance(v, str) and v for v in (session_id, agent_id)):
        return 0
    if payload.get("agent_type") != load_config(root)["reviewer"]:
        return 0
    path = inflight_path(root, session_id, agent_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("%.6f\n" % time.time())
    return 0


# ------------------------------------------------------------- enforce ----


def find_gate(root, session_id):
    candidates = []
    if session_id:
        candidates.append(gate_path(root, session_id))
    candidates.append(gate_path(root, ""))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def git_pending(root, files):
    scope = ["--"] + files if files else []
    lines = []
    for args in (
        ["diff", "--name-only"],
        ["diff", "--name-only", "--cached"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        lines += run_git(root, args + scope)
    return sorted({normalize_rel(line) for line in lines})


def committed_since(root, files, first_ts):
    # Committing does not waive the review: a commit touching gated files
    # after the first mark still counts as pending work.
    if not files or not first_ts:
        return False
    out = run_git(root, ["log", "-1", "--format=%ct", "--"] + files)
    try:
        return bool(out) and int(out[0]) >= first_ts - COMMIT_SLACK_SECONDS
    except ValueError:
        return False


def classify_inflight(root, session_id, last_mark, now):
    """Return (active start epochs, whether a stale record was seen).
    Records outside the TTL window are deleted; unparseable ones are ignored."""
    active = []
    stale = False
    if not session_id:
        return active, stale
    for path, started in inflight_records(root, session_id).items():
        if started is None:
            continue
        if not 0 <= now - started < INFLIGHT_TTL_SECONDS:
            stale = True
            remove_quietly(path)
        elif started > last_mark:
            active.append(started)
        else:
            stale = True
    return active, stale


def build_reason(files, cfg, stale_reviewer=False):
    prefix = cfg["marker_prefix"]
    reviewer = cfg["reviewer"]
    if files:
        quoted = " ".join(shlex.quote(f) for f in files)
        scope = (
            "Scope the review to the files this session edited:\n"
            "  git diff -- {q}\n"
            "  git diff --cached -- {q}\n"
            "  git status --short -- {q}\n"
        ).format(q=quoted)
    else:
        scope = (
            "Review the latest git changes "
            "(git diff, git diff --cached, git status --short).\n"
        )
    stale = ""
    if stale_reviewer:
        stale = (
            "A {r} run that started before the latest gated edit, or more "
            "than {m} minutes ago, does not count as in flight.\n\n"
        ).format(r=reviewer, m=INFLIGHT_TTL_SECONDS // 60)
    return (
        "Dotfiles review required before stopping.\n\n{t}"
        "Run the {r} subagent now.\n{s}"
        "\nThe reviewer's FINAL line must be exactly one of:\n"
        "{p}=PASS\n{p}=FAIL\n\n"
        "If FAIL: fix the Must-fix issues and rerun the reviewer. Once PASS "
        "is recorded, the SubagentStop hook clears the review gate "
        "automatically.\n"
    ).format(r=reviewer, s=scope, p=prefix, t=stale)


def cmd_enforce(payload, root):
    session_id = payload.get("session_id") or ""
    path = find_gate(root, session_id)
    if not path:
        return 0

    gate = read_gate(path) or {}
    files = [f for f in gate.get("files") or [] if isinstance(f, str)]
    try:
        first_ts = int(gate.get("firstTimestamp") or gate.get("timestamp"))
    except (TypeError, ValueError):
        first_ts = 0

    cfg = load_config(root)
    pending = git_pending(root, files or None)
    if not files:
        # Legacy gate without a file list: judge the whole repo, minus
        # exempt paths and the review loop's own artifacts.
        pending = [
            p
            for p in pending
            if not is_exempt(p, cfg["exempt"]) and not is_runtime_artifact(p)
        ]
    if not pending and not committed_since(root, files, first_ts):
        # Gated edits vanished (reverted / never materialized): stand down.
        try:
            os.remove(path)
        except OSError:
            pass
        return 0

    now = time.time()
    active, stale = classify_inflight(root, session_id, last_mark_epoch(root, session_id), now)
    if active:
        minutes = int((now - min(active)) // 60)
        age = "less than a minute" if minutes < 1 else "{m} min".format(m=minutes)
        message = (
            "Dotfiles review in flight ({r} started {a} ago); the gate "
            "re-checks when it reports."
        ).format(r=cfg["reviewer"], a=age)
        out = {"systemMessage": message}
    else:
        out = {"decision": "block", "reason": build_reason(files, cfg, stale_reviewer=stale)}
    json.dump(out, sys.stdout)
    print()
    return 0


# --------------------------------------------------------------- clear ----


def extract_texts(content):
    texts = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif not isinstance(block, dict):
                continue
            elif block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    texts.append(text)
            elif block.get("type") == "tool_use" and block.get("name") == HANDBACK_TOOL:
                # Background subagents report to their caller through this
                # tool call, so the verdict can exist only in its input.
                tool_input = block.get("input")
                message = tool_input.get("message") if isinstance(tool_input, dict) else None
                if isinstance(message, str):
                    texts.append(message)
    return texts


def marker_from_text(text, pass_token, fail_token):
    """Return "PASS"/"FAIL" for a strict terminal marker, "INVALID" for a
    malformed one (multiple markers, or marker not the final meaningful
    line), None when no marker line is present."""
    meaningful = [line.strip() for line in text.splitlines() if line.strip()]
    markers = [line for line in meaningful if line in (pass_token, fail_token)]
    if not markers:
        return None
    if len(markers) > 1 or meaningful[-1] != markers[0]:
        return "INVALID"
    return "PASS" if markers[0] == pass_token else "FAIL"


def transcript_has_pass(path, min_epoch, marker_prefix):
    """True when the last record carrying a marker is a fresh, lone PASS.
    A later FAIL, malformed marker, or stale PASS outranks an earlier PASS."""
    pass_token = marker_prefix + "=PASS"
    fail_token = marker_prefix + "=FAIL"
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            lines = deque(fh, maxlen=800)
    except OSError:
        return False

    passed = False
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("isMeta") is True or obj.get("type") == "user":
            continue
        message = obj.get("message")
        if not isinstance(message, dict):
            continue

        verdicts = set()
        for text in extract_texts(message.get("content")):
            verdict = marker_from_text(text, pass_token, fail_token)
            if verdict:
                verdicts.add(verdict)
        if not verdicts:
            continue

        epoch = parse_iso_to_epoch(obj.get("timestamp") or "")
        passed = (
            verdicts == {"PASS"}
            and epoch is not None
            and epoch + PASS_SLACK_SECONDS >= min_epoch
        )
    return passed


def cmd_clear(payload, root):
    session_id = payload.get("session_id") or ""
    agent_id = payload.get("agent_id")
    started = None
    if session_id and isinstance(agent_id, str) and agent_id:
        record = inflight_path(root, session_id, agent_id)
        started = read_inflight(record)
        remove_quietly(record)

    gates = gate_candidates(root, session_id)
    if not gates:
        return 0

    cfg = load_config(root)
    agent_type = payload.get("agent_type")
    if agent_type and agent_type != cfg["reviewer"]:
        return 0

    min_epoch = last_mark_epoch(root, session_id)
    if started is not None and started <= min_epoch:
        # This reviewer began before the latest gated edit and never saw it.
        return 0

    # The main transcript is only a fallback for payloads that name no agent
    # transcript; otherwise its text could override the reviewer's verdict.
    transcript = normalize_host_path(
        payload.get("agent_transcript_path") or payload.get("transcript_path") or ""
    )
    if not transcript:
        return 0

    # Retry briefly to allow transcript flush.
    for _ in range(15):
        if transcript_has_pass(transcript, min_epoch, cfg["marker_prefix"]):
            for gate in gates:
                remove_quietly(gate)
            return 0
        time.sleep(0.2)
    return 0


# ---------------------------------------------------------------- main ----


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = "" if sys.stdin.isatty() else sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    cwd = os.getcwd()
    root = git_toplevel(cwd) or os.path.realpath(cwd)

    if command == "mark":
        return cmd_mark(payload, root)
    if command == "start":
        return cmd_start(payload, root)
    if command == "enforce":
        return cmd_enforce(payload, root)
    if command == "clear":
        return cmd_clear(payload, root)
    print("review_gate.py: unknown subcommand %r" % command, file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - hooks must never break the session
        print("review_gate.py: %s" % exc, file=sys.stderr)
        sys.exit(0)
