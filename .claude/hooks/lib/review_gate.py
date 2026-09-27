#!/usr/bin/env python3
"""Shared review-gate logic for the Claude Code hooks in .claude/hooks/.

Subcommands (the Claude hook payload JSON is read from stdin):

  mark       PostToolUse (Write|Edit): raise a session-scoped gate when the
             edited file is a reviewable file inside this checkout.
  snapshot   PreToolUse (Bash|PowerShell): fingerprint the checkout's dirty
             set before the command runs.
  mark-bash  PostToolUse / PostToolUseFailure (Bash|PowerShell): mark the
             reviewable paths whose fingerprint the command changed.
  start      SubagentStart: record that a reviewer subagent is in flight.
  enforce    Stop: reconcile snapshots no Post hook consumed, then block
             stopping while the session's gate still has pending work,
             unless a reviewer started after the last mark is still in
             flight; clear the gate if the gated edits no longer exist.
  clear      SubagentStop: drop the reviewer's in-flight record, then clear
             the gate when its last verdict is DOTFILES_REVIEWER_RESULT=PASS
             as the final meaningful line of a text block or
             SubagentHandback message.

Gate file: .claude/.needs_dotfiles_review.<sanitized session_id>, JSON:
  {"timestamp": <last mark>, "firstTimestamp": <first mark>,
   "markedAt": <last mark, float>, "sessionID": "...",
   "files": ["repo/relative", ...],
   "roots": {"<other checkout>": ["checkout/relative", ...]}}  (optional)
An unsuffixed .claude/.needs_dotfiles_review (legacy epoch-int format) is
accepted as a fallback and merged/cleared during migration.

In-flight file, one per running reviewer:
  .claude/.dotfiles_review_inflight.<sanitized session_id>.<sanitized agent_id>
containing the reviewer's start epoch (float). Records outside the
INFLIGHT_TTL_SECONDS window are deleted so a reviewer that never reports
cannot disable the gate.

Bash snapshots live outside the checkout, one per tool call, in
$CLAUDE_REVIEW_GATE_STATE_DIR or <tempdir>/claude-review-gate[-<uid>]:
  <sha256(session_id)[:16]>-<sha256(tool_use_id)[:16]>.json
  {"root": ..., "session_id": ..., "background": bool, "created": <epoch>,
   "taken": <epoch of the current baseline>,
   "fingerprints": {"repo/relative": [kind, ...], ...}}
Only dirty paths (git status) are fingerprinted, so a path absent from the
baseline was clean. Snapshots older than SNAPSHOT_TTL_SECONDS are pruned.

Path policy comes from the config shared with the OpenCode plugins
(.opencode/plugins/review-loop-*.js): .opencode/opencode-tooling.config.jsonc.
Negations in exemptPaths are honored the same way as the OpenCode marker:
exempt = matches a positive pattern AND matches no "!" pattern.

Wrappers cd to the project dir first. Its git toplevel is "home": the gate,
lock and in-flight records live there. Edits are also accepted from the
session's work root, the payload cwd's checkout when it is another checkout
of home's repository (Claude Code's EnterWorktree moves cwd but not
CLAUDE_PROJECT_DIR); they are gated under "roots". Other checkouts, sibling
worktrees included, gate only their own edits.
"""

import glob
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from collections import deque
from contextlib import contextmanager
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
    ".claude/settings.local.json",
)
RUNTIME_SUFFIXES = (
    "-review-gate.log",
    "-review-marker.log",
    "-review-enforcer.log",
)

PASS_SLACK_SECONDS = 3.0
COMMIT_SLACK_SECONDS = 5
INFLIGHT_TTL_SECONDS = 45 * 60

STATE_DIR_ENV = "CLAUDE_REVIEW_GATE_STATE_DIR"
SNAPSHOT_TTL_SECONDS = 60 * 60
# Applied only at the owning session's Stop, after a final diff.
FOREGROUND_SNAPSHOT_TTL_SECONDS = 15 * 60
HASH_BUDGET_BYTES = 64 * 1024 * 1024
RACY_MTIME_SECONDS = 2
HOME_SNAPSHOT = "\0home"
SNAPSHOT_GLOB = "[0-9a-f]" * 16 + "-" + "[0-9a-f]" * 16 + ".json*"
SNAPSHOT_GIT_TIMEOUT = 5
# Shared by all run_git_raw calls in one hook run; below the 10 s hook timeout.
HOOK_GIT_BUDGET = 8
GIT_DEADLINE = [None]


def run_git_raw(root, args, timeout=SNAPSHOT_GIT_TIMEOUT):
    if GIT_DEADLINE[0] is not None:
        remaining = GIT_DEADLINE[0] - time.monotonic()
        if remaining <= 0:
            return None
        timeout = min(timeout, remaining)
    try:
        proc = subprocess.run(
            ["git", "-C", root] + args,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


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


def is_reviewable(rel, cfg):
    return not is_runtime_artifact(rel) and not is_exempt(rel, cfg["exempt"])


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
        cwd = normalize_host_path(payload.get("cwd") or "") or root
        file_path = os.path.join(cwd, file_path)
    file_path = os.path.realpath(file_path)

    # Compare git toplevels; a path-prefix test misclassifies nested worktrees.
    directory = os.path.dirname(file_path)
    while directory and not os.path.isdir(directory):
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    top = git_toplevel(directory) if os.path.isdir(directory) else None
    if not top:
        return 0
    checkout = root if same_path(top, root) else work_root(payload, root)
    if not same_path(top, checkout):
        return 0

    rel = normalize_rel(os.path.relpath(file_path, os.path.realpath(checkout)))
    if rel.startswith("../"):
        return 0
    if not is_reviewable(rel, load_config(root)):
        return 0

    write_gate(root, payload.get("session_id") or "", [rel], checkout=checkout)
    return 0


def git_common_dir(directory):
    """(toplevel, realpath of the git common dir) for a checkout, or (None, None).
    Relative output is relative to `directory`; this works before git 2.31."""
    out = run_git_raw(directory, ["rev-parse", "--show-toplevel", "--git-common-dir"])
    if out is None:
        return None, None
    lines = [line.strip() for line in os.fsdecode(out).splitlines() if line.strip()]
    if len(lines) < 2:
        return None, None
    return lines[0], os.path.realpath(os.path.join(directory, lines[1]))


def work_root(payload, home):
    """The checkout the session is working in: the payload cwd's toplevel when
    it is another checkout of home's repository, otherwise home."""
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return home
    directory = os.path.realpath(normalize_host_path(cwd))
    if not os.path.isdir(directory):
        return home
    # Walk up to the nearest .git: reaching home first costs no git call.
    while not same_path(directory, home):
        if os.path.lexists(os.path.join(directory, ".git")):
            break
        parent = os.path.dirname(directory)
        if parent == directory:
            return home
        directory = parent
    else:
        return home
    top, common = git_common_dir(directory)
    if not top:
        return home
    _, home_common = git_common_dir(home)
    if not home_common or not same_path(common, home_common):
        return home
    return os.path.realpath(top)


def gate_roots(gate):
    """{checkout: [rels]} for gated checkouts other than home."""
    roots = gate.get("roots") if isinstance(gate, dict) else None
    out = {}
    if isinstance(roots, dict):
        for key, rels in roots.items():
            if isinstance(key, str) and key and isinstance(rels, list):
                out[key] = [r for r in rels if isinstance(r, str)]
    return out


def root_key(roots, checkout):
    for key in roots:
        if same_path(key, checkout):
            return key
    return None


def write_json_atomic(path, data):
    """Atomic JSON write; the temp name extends the target's so its ignore rules apply."""
    directory, name = os.path.split(path)
    fd, tmp = tempfile.mkstemp(prefix=name + ".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows refuses to replace a file another process has open.
                if os.name != "nt" or attempt == 4:
                    raise
                time.sleep(0.05)
    except BaseException:
        remove_quietly(tmp)
        raise


@contextmanager
def gate_lock(root):
    """flock on .claude/ itself, since a lock file would look like a gate. Never nest.
    A no-op on Windows, which has no fcntl."""
    directory = os.path.join(root, GATE_DIR)
    os.makedirs(directory, exist_ok=True)
    fd = None
    try:
        import fcntl

        fd = os.open(directory, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except ImportError:
        pass
    except OSError as exc:
        print(
            "review_gate.py: cannot lock %s (%s); writing unlocked" % (directory, exc),
            file=sys.stderr,
        )
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)


def write_gate(root, session_id, rels, fingerprints=None, checkout=None):
    """Add rels (relative to `checkout`, default home) to the session gate and
    to live snapshots of that checkout."""
    checkout = checkout or root
    with gate_lock(root):
        update_gate(root, session_id, rels, checkout)
        sync_snapshots(checkout, session_id, rels, fingerprints or {})


def update_gate(root, session_id, rels, checkout):
    path = gate_path(root, session_id)
    marked_at = time.time()
    now = int(marked_at)
    files = set()
    roots = {}
    first = now

    existing = read_gate(path)
    if existing:
        files.update(f for f in existing.get("files") or [] if isinstance(f, str))
        roots.update(gate_roots(existing))
        try:
            first = min(first, int(existing.get("firstTimestamp") or existing.get("timestamp")))
        except (TypeError, ValueError):
            pass

    # Absorb and retire the unsuffixed legacy gate once a session ID is known.
    unsuffixed = gate_path(root, "")
    if session_id and os.path.exists(unsuffixed):
        legacy = read_gate(unsuffixed) or {}
        files.update(f for f in legacy.get("files") or [] if isinstance(f, str))
        for key, extra in gate_roots(legacy).items():
            key = root_key(roots, key) or key
            roots[key] = sorted(set(roots.get(key, [])) | set(extra))
        try:
            first = min(first, int(legacy.get("firstTimestamp") or legacy.get("timestamp")))
        except (TypeError, ValueError):
            pass
        try:
            os.remove(unsuffixed)
        except OSError:
            pass

    if same_path(checkout, root):
        files.update(rels)
    else:
        key = root_key(roots, checkout) or os.path.realpath(checkout)
        roots[key] = sorted(set(roots.get(key, [])) | set(rels))
    data = {
        "timestamp": now,
        "firstTimestamp": first,
        "markedAt": marked_at,
        "sessionID": session_id,
        "files": sorted(files),
    }
    if roots:
        data["roots"] = roots
    write_json_atomic(path, data)


# ---------------------------------------------------------------- bash ----


def dirty_paths(root):
    """Dirty repo-relative paths, or None if git fails; never takes index.lock."""
    out = run_git_raw(
        root,
        [
            "--no-optional-locks",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
        ],
    )
    if out is None:
        return None
    paths = []
    for entry in out.split(b"\0"):
        if len(entry) > 3:
            paths.append(os.fsdecode(entry[3:]).rstrip("/"))
    return paths


def fingerprint(root, rel, previous, budget, reuse_before_ns):
    """[kind, ...] for one dirty path, None for a directory; never raises."""
    path = os.path.join(root, rel)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return ["missing"]
    except OSError:
        return ["unreadable"]
    try:
        if stat.S_ISDIR(st.st_mode):
            return None
        if stat.S_ISLNK(st.st_mode):
            return ["link", os.readlink(path)]
        if not stat.S_ISREG(st.st_mode):
            return ["special"]
        executable = bool(st.st_mode & 0o111)
        if (
            isinstance(previous, list)
            and len(previous) == 5
            and previous[:3] == ["file", st.st_size, st.st_mtime_ns]
            and previous[4] == executable
            and st.st_mtime_ns < reuse_before_ns
        ):
            return previous
        digest = None
        if st.st_size <= budget[0]:
            budget[0] -= st.st_size
            sha = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    sha.update(chunk)
            digest = sha.hexdigest()
        return ["file", st.st_size, st.st_mtime_ns, digest, executable]
    except OSError:
        return ["unreadable", st.st_size, st.st_mtime_ns]


def dirty_fingerprints(root, previous=None, taken=0.0, cfg=None):
    """Reviewable dirty set under `cfg` (home's path policy). Old hashes are
    reused only for files unchanged since RACY_MTIME_SECONDS before `taken`."""
    paths = dirty_paths(root)
    if paths is None:
        return None
    if cfg is None:
        cfg = load_config(root)
    previous = previous or {}
    reuse_before_ns = int((taken - RACY_MTIME_SECONDS) * 1e9)
    budget = [HASH_BUDGET_BYTES]
    result = {}
    for rel in paths:
        if not is_reviewable(rel, cfg):
            continue
        fp = fingerprint(root, rel, previous.get(rel), budget, reuse_before_ns)
        if fp is not None:
            result[rel] = fp
    return result


def same_fingerprint(before, after):
    if before == after:
        return True
    # Hashed files compare by content and mode; an mtime change alone is not an edit.
    return (
        isinstance(before, list)
        and len(before) == len(after) == 5
        and before[0] == after[0] == "file"
        and before[3] is not None
        and before[3:] == after[3:]
    )


def changed_paths(before, after):
    return {rel for rel, fp in after.items() if not same_fingerprint(before.get(rel), fp)}


def state_dir():
    base = os.environ.get(STATE_DIR_ENV)
    getuid = None
    if not base:
        name = "claude-review-gate"
        # No getuid on Windows, where %TEMP% is already per-user.
        getuid = getattr(os, "getuid", None)
        if getuid is not None:
            name += "-%d" % getuid()
        base = os.path.join(tempfile.gettempdir(), name)
    try:
        if os.path.islink(base):
            raise OSError("symlink")
        os.makedirs(base, mode=0o700, exist_ok=True)
        if getuid is not None:
            if os.lstat(base).st_uid != getuid():
                raise OSError("owned by another user")
            os.chmod(base, 0o700)
    except OSError as exc:
        print(
            "review_gate.py: %s is not a private directory (%s); "
            "Bash snapshots are disabled" % (base, exc),
            file=sys.stderr,
        )
        return None
    return base


def id_hash(value):
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def snapshot_path(directory, session_id, tool_use_id):
    return os.path.join(directory, "%s-%s.json" % (id_hash(session_id), id_hash(tool_use_id)))


def read_snapshot(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("fingerprints"), dict)
        or not all(isinstance(data.get(k), str) and data[k] for k in ("root", "session_id"))
    ):
        return None
    return data


def prune_snapshots(directory, now, own_session=None, keep=()):
    # Snapshot names only: an overridden state directory may hold other files.
    for path in glob.glob(os.path.join(glob.escape(directory), SNAPSHOT_GLOB)):
        if path in keep:
            continue
        data = read_snapshot(path) if path.endswith(".json") else None
        created = as_epoch(data.get("created")) if data else None
        if created is None:
            try:
                created = os.path.getmtime(path)
            except OSError:
                continue
        ttl = SNAPSHOT_TTL_SECONDS
        if data and data["session_id"] == own_session and not data.get("background"):
            ttl = FOREGROUND_SNAPSHOT_TTL_SECONDS
        # Symmetric, so a future-dated file (clock stepped back) still expires.
        if abs(now - created) >= ttl:
            remove_quietly(path)


def session_snapshots(directory, session_id, checkout=None):
    pattern = os.path.join(glob.escape(directory), id_hash(session_id) + "-*.json")
    snapshots = {}
    for path in glob.glob(pattern):
        data = read_snapshot(path)
        if data and data["session_id"] == session_id:
            if checkout is None or same_path(data["root"], checkout):
                snapshots[path] = data
    return snapshots


def sync_snapshots(checkout, session_id, rels, known):
    """Put marked paths into live snapshots of `checkout` so older baselines
    never re-report them. Best effort (a miss only re-marks). Caller holds
    gate_lock."""
    if not session_id:
        return
    try:
        directory = state_dir()
        if not directory:
            return
        snapshots = session_snapshots(directory, session_id, checkout)
        if not snapshots:
            return
        budget = [HASH_BUDGET_BYTES]
        current = {
            rel: known[rel] if rel in known else fingerprint(checkout, rel, None, budget, 0)
            for rel in rels
        }
        for path, data in snapshots.items():
            for rel, fp in current.items():
                if fp is None:
                    data["fingerprints"].pop(rel, None)
                else:
                    data["fingerprints"][rel] = fp
            write_json_atomic(path, data)
    except OSError:
        pass


def payload_ids(payload):
    session_id = payload.get("session_id")
    tool_use_id = payload.get("tool_use_id")
    if not all(isinstance(v, str) and v for v in (session_id, tool_use_id)):
        return None, None
    return session_id, tool_use_id


def runs_in_background(payload):
    tool_input = payload.get("tool_input")
    return isinstance(tool_input, dict) and tool_input.get("run_in_background") is True


def cmd_snapshot(payload, root):
    session_id, tool_use_id = payload_ids(payload)
    if not session_id:
        return 0
    directory = state_dir()
    if not directory:
        return 0
    now = time.time()
    prune_snapshots(directory, now)
    work = os.path.realpath(work_root(payload, root))
    cfg = load_config(root)
    targets = [(work, tool_use_id)]
    if not same_path(work, root):
        # From a worktree, a command can still write the project checkout by path.
        targets.append((os.path.realpath(root), tool_use_id + HOME_SNAPSHOT))
    for checkout, key in targets:
        fingerprints = dirty_fingerprints(checkout, cfg=cfg)
        if fingerprints is None:
            print("review_gate.py: git status failed in %s; not checked there" % checkout, file=sys.stderr)
            continue
        write_json_atomic(
            snapshot_path(directory, session_id, key),
            {
                "root": checkout,
                "session_id": session_id,
                "background": runs_in_background(payload),
                "created": now,
                "taken": now,
                "fingerprints": fingerprints,
            },
        )
    return 0


def as_epoch(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def snapshot_taken(data):
    return as_epoch(data.get("taken")) or 0.0


def cmd_mark_bash(payload, root):
    session_id, tool_use_id = payload_ids(payload)
    if not session_id:
        return 0
    directory = state_dir()
    if not directory:
        return 0
    for key in (tool_use_id, tool_use_id + HOME_SNAPSHOT):
        consume_snapshot(snapshot_path(directory, session_id, key), payload, root, session_id)
    return 0


def consume_snapshot(path, payload, root, session_id):
    snapshot = read_snapshot(path)
    if snapshot is None:
        return
    # The Pre hook's checkout: this payload's cwd follows any cd in the command.
    checkout = snapshot["root"]
    before = snapshot["fingerprints"]
    taken = time.time()
    after = dirty_fingerprints(checkout, before, snapshot_taken(snapshot), load_config(root))
    if after is None:
        # Left in place for the Stop reconcile.
        return
    # Mark before consuming, so a failed mark leaves the snapshot for Stop.
    rels = changed_paths(before, after)
    if rels:
        write_gate(root, session_id, rels, after, checkout=checkout)
    with gate_lock(root):
        if snapshot.get("background") or runs_in_background(payload):
            # A background command may still write; Stop diffs it again from here.
            rebase_snapshot(path, before, after, taken, background=True)
        else:
            remove_quietly(path)


def rebase_snapshot(path, read, after, taken, background=False):
    """Rebase onto `after`, keeping entries synced since `read`; never recreate
    a consumed snapshot. Caller holds gate_lock."""
    current = read_snapshot(path)
    if current is None:
        return
    merged = dict(after)
    for rel, fp in current["fingerprints"].items():
        if read.get(rel) != fp:
            merged[rel] = fp
    current["fingerprints"] = merged
    current["taken"] = taken
    if background:
        current["background"] = True
    write_json_atomic(path, current)


def reconcile_snapshots(root, session_id):
    """Mark and rebase snapshots no Post hook consumed, then drop expired ones."""
    if not session_id:
        return []
    directory = state_dir()
    if not directory:
        return []
    prune_snapshots(directory, time.time())
    groups = {}
    for path, data in session_snapshots(directory, session_id).items():
        key = root_key(groups, data["root"]) or data["root"]
        groups.setdefault(key, {})[path] = data
    if not groups:
        return []
    cfg = load_config(root)
    marked = set()
    undiffed = set()
    for checkout, snapshots in groups.items():
        previous = {}
        for data in snapshots.values():
            previous.update(data["fingerprints"])
        taken = time.time()
        oldest = min(snapshot_taken(data) for data in snapshots.values())
        after = dirty_fingerprints(checkout, previous, oldest, cfg)
        if after is None:
            # Kept past the foreground TTL below: it has had no final diff yet.
            undiffed.update(snapshots)
            continue
        rels = set()
        for data in snapshots.values():
            rels |= changed_paths(data["fingerprints"], after)
        if rels:
            write_gate(root, session_id, rels, after, checkout=checkout)
            marked |= rels
        with gate_lock(root):
            for path, data in snapshots.items():
                rebase_snapshot(path, data["fingerprints"], after, taken)
    prune_snapshots(directory, time.time(), session_id, keep=undiffed)
    return sorted(marked)


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


def checkout_state(checkout):
    """"live", "gone" (removed, or its gitdir pruned) or "unknown" (git failed,
    e.g. a safe.directory refusal, which must not fold a live worktree)."""
    dot_git = os.path.join(checkout, ".git")
    if not os.path.isdir(checkout) or not os.path.lexists(dot_git):
        return "gone"
    if os.path.isfile(dot_git):
        try:
            with open(dot_git, "rb") as fh:
                line = os.fsdecode(fh.readline()).strip()
        except OSError:
            return "unknown"
        if line.startswith("gitdir:"):
            if not os.path.isdir(os.path.join(checkout, line[len("gitdir:"):].strip())):
                return "gone"
    out = run_git_raw(checkout, ["rev-parse", "--show-toplevel"])
    if out is None:
        return "unknown"
    top = os.fsdecode(out).strip()
    return "live" if top and same_path(top, checkout) else "unknown"


def fold_gone_roots(root, path, gate):
    """Move the rels of removed worktrees into home's list, where home's pending
    check and commit history judge them (a branch merged into home's current
    branch still blocks). Keeps markedAt, so no in-flight reviewer goes stale.
    True if it rewrote."""
    gone_keys = [key for key in gate_roots(gate) if checkout_state(key) == "gone"]
    if not gone_keys:
        return False
    with gate_lock(root):
        current = read_gate(path) or {}
        roots = gate_roots(current)
        gone = [key for key in roots if any(same_path(key, g) for g in gone_keys)]
        if not gone:
            return False
        files = set(f for f in current.get("files") or [] if isinstance(f, str))
        for key in gone:
            files.update(roots.pop(key))
        current["files"] = sorted(files)
        if roots:
            current["roots"] = roots
        else:
            current.pop("roots", None)
        write_json_atomic(path, current)
    return True


def checkout_busy(checkout, rels, first_ts):
    """Pending work in another checkout; any git failure counts as pending."""
    scope = ["--"] + rels
    for args in (
        ["diff", "--name-only"],
        ["diff", "--name-only", "--cached"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        out = run_git_raw(checkout, args + scope)
        if out is None or out.strip():
            return True
    out = run_git_raw(checkout, ["log", "-1", "--format=%ct", "--"] + rels)
    if out is None:
        return True
    try:
        stamp = int(out.decode().strip() or 0)
    except ValueError:
        return True
    return bool(stamp) and bool(first_ts) and stamp >= first_ts - COMMIT_SLACK_SECONDS


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


def scope_lines(files, checkout=None):
    quoted = " ".join(shlex.quote(f) for f in files)
    git = "git -C {c}".format(c=shlex.quote(checkout)) if checkout else "git"
    return (
        "  {g} diff -- {q}\n"
        "  {g} diff --cached -- {q}\n"
        "  {g} status --short -- {q}\n"
    ).format(g=git, q=quoted)


def build_reason(files, cfg, stale_reviewer=False, roots=None, home=None, in_worktree=False):
    prefix = cfg["marker_prefix"]
    reviewer = cfg["reviewer"]
    if roots or (files and in_worktree):
        # From inside a worktree an unprefixed diff reads the wrong checkout.
        scope = "Scope the review to the files this session edited:\n"
        if files:
            scope += scope_lines(files, home)
        for checkout in sorted(roots or {}):
            scope += scope_lines(roots[checkout], checkout)
        if files:
            scope += (
                "If this session is inside a worktree, run ExitWorktree (keep the "
                "worktree) before reviewing the files under {h}: Claude Code blocks "
                "git -C into the project checkout from a worktree session and its "
                "subagents.\n"
            ).format(h=home)
    elif files:
        scope = "Scope the review to the files this session edited:\n" + scope_lines(files)
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
    # Before reconcile spends the git budget, so a slow Stop still scopes correctly.
    in_worktree = not same_path(work_root(payload, root), root)
    try:
        reconcile_snapshots(root, session_id)
    except OSError as exc:
        print("review_gate.py: snapshot reconcile failed: %s" % exc, file=sys.stderr)
    last_mark = last_mark_epoch(root, session_id)
    path = find_gate(root, session_id)
    if not path:
        return 0

    gate = read_gate(path) or {}
    try:
        if fold_gone_roots(root, path, gate):
            gate = read_gate(path) or {}
    except OSError as exc:
        print("review_gate.py: worktree fold failed: %s" % exc, file=sys.stderr)
    files = [f for f in gate.get("files") or [] if isinstance(f, str)]
    roots = gate_roots(gate)
    try:
        first_ts = int(gate.get("firstTimestamp") or gate.get("timestamp"))
    except (TypeError, ValueError):
        first_ts = 0

    cfg = load_config(root)
    if files or roots:
        busy = (
            bool(files)
            and (bool(git_pending(root, files)) or committed_since(root, files, first_ts))
        ) or any(checkout_busy(c, r, first_ts) for c, r in roots.items())
    else:
        # Legacy gate without a file list: judge the whole repo, minus
        # exempt paths and the review loop's own artifacts.
        busy = any(
            not is_exempt(p, cfg["exempt"]) and not is_runtime_artifact(p)
            for p in git_pending(root, None)
        )
    if not busy:
        # Gated edits vanished (reverted / never materialized): stand down,
        # unless a hook marked again meanwhile.
        with gate_lock(root):
            if last_mark_epoch(root, session_id) <= last_mark:
                remove_quietly(path)
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
        reason = build_reason(
            files, cfg, stale_reviewer=stale, roots=roots, home=root, in_worktree=in_worktree
        )
        out = {"decision": "block", "reason": reason}
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
            with gate_lock(root):
                # A hook that marked while the verdict was read keeps its gate.
                if last_mark_epoch(root, session_id) <= min_epoch:
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

    GIT_DEADLINE[0] = time.monotonic() + HOOK_GIT_BUDGET
    cwd = os.getcwd()
    root = git_toplevel(cwd) or os.path.realpath(cwd)

    if command == "mark":
        return cmd_mark(payload, root)
    if command == "snapshot":
        return cmd_snapshot(payload, root)
    if command == "mark-bash":
        return cmd_mark_bash(payload, root)
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
        # Non-zero sends each wrapper to its conservative branch.
        print("review_gate.py: %s" % exc, file=sys.stderr)
        sys.exit(1)
