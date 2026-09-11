#!/usr/bin/env python3
"""Check the authoritative repository's configured main-push pipeline contract."""

import sys

sys.dont_write_bytecode = True

try:
    from pipeline_guard import main
except (ImportError, SyntaxError):
    print("ERROR: restore the documented pipeline guard runtime files in assets/", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
