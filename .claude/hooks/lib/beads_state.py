#!/usr/bin/env python3
"""Beads in-progress state-file validation for the plan-approval hook.

Subcommand:

  check    Decide whether .beads/in-progress-claude.json still refers to a
           live Beads issue. Prints a one-line human reason on stdout and
           signals the verdict through the exit code.

Exit codes (three, deliberately -- "the issue is closed" and "the Beads
tooling is broken" are different events and must not share a message):

  0   LIVE        the issue exists and is not closed; the caller may stay quiet
  10  STALE       a real answer came back and it disqualifies the state file
  20  UNVERIFIED  nothing is known about the issue; Beads tooling failed

A STALE verdict means bd answered. An UNVERIFIED verdict means it did not,
and the caller is expected to say so out loud rather than report it as
staleness. Both are non-zero because staying silent is the bug this guards
(dots-7iav): a state file left behind by an interrupted close suppressed the
plan-approval prompt indefinitely.

bd is exec'd directly rather than through a shell, which bypasses the
interactive `bd` wrappers by construction (see AGENTS.md, "Beads CLI").
The call is bounded by CLAUDE_BEADS_GATE_TIMEOUT seconds (default 5) so a
cold-starting or unreachable Dolt server cannot stall plan approval. Nothing
is cached: caching a LIVE verdict would rebuild the very suppression window
this exists to close.
"""

import json
import os
import shutil
import subprocess
import sys

DEFAULT_STATE_FILE = os.path.join(".beads", "in-progress-claude.json")
DEFAULT_TIMEOUT = 5.0
TIMEOUT_ENV = "CLAUDE_BEADS_GATE_TIMEOUT"

EXIT_LIVE = 0
EXIT_STALE = 10
EXIT_UNVERIFIED = 20

# Statuses that mean "work is still on this issue". Anything outside this set
# that bd actually reports is treated as unknown rather than as live, so a new
# status word fails toward prompting instead of toward silence.
LIVE_STATUSES = frozenset({"open", "in_progress", "blocked"})
CLOSED_STATUS = "closed"

# bd reports a missing issue as rc=1 with {"error": "no issues found ..."} on
# stdout. An unreachable Dolt server can also exit 1, so the error *body* is
# what separates a real not-found answer from a transport failure.
#
# These are whole phrases on purpose. A bare "not found" would also match a
# transport body such as {"error": "database dots not found"} or
# {"error": "dial tcp ...: host not found"}, and misfiling either as STALE
# would tell the user their issue is gone — and invite them to delete a
# perfectly good state file — when the server is merely down. Anything
# ambiguous has to land in UNVERIFIED.
NOT_FOUND_MARKERS = (
    "no issues found",
    "no issue found",
    "issue not found",
    "no such issue",
)


def read_timeout():
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    # "inf" and "nan" both parse as floats. inf > 0 is true, and passing it to
    # subprocess.run means an unbounded wait — the exact stall the timeout
    # exists to prevent — so only finite positive values are honoured.
    if not 0 < value < float("inf"):
        return DEFAULT_TIMEOUT
    return value


def load_state(state_file):
    """Return (issue_id, verdict, reason). issue_id is None unless verdict is None."""
    if not os.path.isfile(state_file):
        return None, EXIT_STALE, "no in-progress state file at %s" % state_file
    try:
        with open(state_file, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return None, EXIT_STALE, "state file %s is unreadable (%s)" % (state_file, exc)
    if not isinstance(data, dict):
        return None, EXIT_STALE, "state file %s is not a JSON object" % state_file
    issue_id = str(data.get("id") or "").strip()
    if not issue_id:
        return None, EXIT_STALE, 'state file %s carries no "id"' % state_file
    return issue_id, None, ""


def status_of(payload):
    """Pull a status string out of whatever shape bd returned, or None."""
    entry = None
    if isinstance(payload, list):
        if not payload:
            return None
        entry = payload[0]
    elif isinstance(payload, dict):
        entry = payload
    if not isinstance(entry, dict):
        return None
    status = str(entry.get("status") or "").strip().lower()
    return status or None


def looks_like_not_found(payload):
    """True only when the error body is unmistakably about a missing issue."""
    if not isinstance(payload, dict):
        return False
    error = str(payload.get("error") or "").strip().lower()
    if not error:
        return False
    return any(marker in error for marker in NOT_FOUND_MARKERS)


def query_bd(issue_id, timeout):
    """Return (verdict, reason, diagnostic). diagnostic is '' unless UNVERIFIED."""
    executable = shutil.which("bd")
    if not executable:
        return (
            EXIT_UNVERIFIED,
            "cannot verify issue %s: bd was not found on PATH" % issue_id,
            "bd not found on PATH; PATH=%s" % os.environ.get("PATH", ""),
        )

    argv = [executable, "show", issue_id, "--json"]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            # The hook's stdin is the Claude payload pipe. A bd build that
            # reads stdin would otherwise block until the full timeout before
            # reporting UNVERIFIED.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return (
            EXIT_UNVERIFIED,
            "cannot verify issue %s: bd show timed out after %gs "
            "(Dolt server unreachable or cold-starting)" % (issue_id, timeout),
            "timed out after %gs running: %s" % (timeout, " ".join(argv)),
        )
    except OSError as exc:
        return (
            EXIT_UNVERIFIED,
            "cannot verify issue %s: bd could not be run (%s)" % (issue_id, exc),
            "OSError running %s: %s" % (" ".join(argv), exc),
        )

    diagnostic = "%s exited %d\nstderr: %s" % (
        " ".join(argv),
        proc.returncode,
        (proc.stderr or "").strip()[-800:] or "(empty)",
    )

    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return (
            EXIT_UNVERIFIED,
            "cannot verify issue %s: bd returned no usable JSON (exit %d)"
            % (issue_id, proc.returncode),
            diagnostic,
        )

    # A parsed not-found body is a real answer even though bd exits non-zero.
    if looks_like_not_found(payload):
        return EXIT_STALE, "issue %s no longer exists in Beads" % issue_id, ""

    if proc.returncode != 0:
        return (
            EXIT_UNVERIFIED,
            "cannot verify issue %s: bd show exited %d"
            % (issue_id, proc.returncode),
            diagnostic,
        )

    if isinstance(payload, list) and not payload:
        return EXIT_STALE, "issue %s no longer exists in Beads" % issue_id, ""

    status = status_of(payload)
    if status is None:
        return (
            EXIT_UNVERIFIED,
            "cannot verify issue %s: bd returned no status field" % issue_id,
            diagnostic,
        )
    if status in LIVE_STATUSES:
        return EXIT_LIVE, "issue %s is %s" % (issue_id, status), ""
    if status == CLOSED_STATUS:
        return EXIT_STALE, "issue %s is closed" % issue_id, ""
    return (
        EXIT_UNVERIFIED,
        "cannot verify issue %s: unrecognized status %r" % (issue_id, status),
        diagnostic,
    )


def check(state_file):
    issue_id, verdict, reason = load_state(state_file)
    if verdict is not None:
        print(reason)
        return verdict

    verdict, reason, diagnostic = query_bd(issue_id, read_timeout())
    print(reason)
    if verdict == EXIT_UNVERIFIED and diagnostic:
        sys.stderr.write(diagnostic.rstrip() + "\n")
    return verdict


def parse_args(argv):
    if not argv or argv[0] != "check":
        sys.stderr.write("usage: beads_state.py check [--state-file PATH]\n")
        return None
    rest = argv[1:]
    state_file = DEFAULT_STATE_FILE
    while rest:
        flag = rest.pop(0)
        if flag == "--state-file":
            if not rest:
                sys.stderr.write("--state-file requires a path\n")
                return None
            state_file = rest.pop(0)
        else:
            sys.stderr.write("unknown argument: %s\n" % flag)
            return None
    return state_file


def main(argv):
    state_file = parse_args(argv)
    if state_file is None:
        return EXIT_UNVERIFIED
    return check(state_file)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
