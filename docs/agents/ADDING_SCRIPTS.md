<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Standards

- Use existing code style conventions and patterns.

There are two classes of script in this repo. The rules below apply to the first.

## Chezmoi-applied scripts

Scripts that run as part of `chezmoi apply` to install or configure the machine.

- Scripts must always be created in the `.chezmoiscripts` folder and nowhere else.
- Scripts must always use `chezmoi` templates and have logic that makes the execution logic conditional on which operating system it is running under.

## Repo-only helpers

Tooling invoked by hand from the repository root, never applied to a target and
never templated. These live in `assets/` and are paired per platform (`*.sh` for
macOS/Linux/WSL2, `*.ps1` for PowerShell 7) rather than using chezmoi's
OS conditionals.

A single cross-platform Python helper is an accepted alternative to the `*.sh` /
`*.ps1` pair when both platforms would otherwise run identical logic, or when a
chezmoi-applied script and something outside chezmoi (a devcontainer lifecycle
hook, for example) must share one implementation. `sync-browser-policies.py` and
`claude-mcp-apply.py` follow this shape; the latter is invoked both by the
chezmoi hook `run_onchange_after_claude_mcp_servers.sh.tmpl` and by the
homelab-IaC devcontainer over its read-only host mount. Such helpers still obey
the rules above — repo-only, not templated, not applied to a target.

A Python helper that callers invoke **by path** needs a third shape, because
native Windows has no `python3` on `PATH`, so `#!/usr/bin/env python3` leaves the
file unrunnable even from the POSIX shells that do read shebangs there.
`agent-wt-merge` handles this without splitting into two files: line 1 is
`#!/bin/sh`, followed by a block that `sh` executes and Python reads as the module
docstring. The block execs `assets/resolve-python3`, which probes `python3`,
`py -3`, then `python`, and rejects the Windows app-execution alias that resolves
but does not run. Keeping it one file matters when the path itself carries policy
— one dirty check, one allowlist pattern, one entry in any provenance manifest.
Two requirements come with the shape: `resolve-python3` has to ship beside the
helper in every mode, and the helper needs `text eol=lf` in `.gitattributes`,
since a CRLF `#!/bin/sh` is fatal under a real `/bin/sh`. That applies to every
shell-dispatched file (any shebang, `*.sh.tmpl`, `*.zsh`, shell rc files), not
only this shape. `tests/test_line_endings.py` selects tracked files that start
with `#!`, carry a shell suffix, or use a known rc name, and fails unless each
is pinned to `text eol=lf` and stored as LF in the index. PowerShell has no `sh`,
so it still calls the interpreter directly; see `worktree-merge-helper.md` →
**Native Windows** for that form, the working header, and the invariants that
break it.

Existing members: `cz-audit.sh` / `cz-audit.ps1`, `beads-sync.sh` /
`beads-sync.ps1` and the repo-local `beads-sync` launcher, `agent-wt-merge`
(sh-dispatched Python), `guarded-main-sync` and its shared GitLab pipeline
runtime, `claude-mcp-apply.py`, `sync-browser-policies.py`, and the `sync-*`
scripts. See `assets/README.md` for their usage.

## Test and ownership checklist

Adding, renaming, or removing production automation also requires an update to
`configs/automation-test-inventory.json`:

1. Run `python3 assets/check-automation-test-inventory.py --list-candidates`
   and review the changed candidate paths, then record them with
   `--update-candidates` in `configs/automation-candidates.txt`.
2. Classify each path exactly once with its repository owner, risk, platforms,
   side effects, and required test layers.
3. Register behavioral evidence in `configs/test-suites.json`, or record a
   truthful planned/partial gap with a durable work item and rationale.
4. For critical automation, list success, failure, and safety requirements. A
   covered requirement must cite a tracked test in a behavioral suite.
5. Use `excluded` only for a repository-owned non-production target with an
   explicit rationale. Static, audit, and provenance checks do not replace
   behavioral evidence for owned automation.
6. Run the inventory checker and the affected canonical suite before review.

The complete policy, including platform-only rules and optional targeted
language metrics, is in `docs/tooling/automation-coverage-policy.md`.
