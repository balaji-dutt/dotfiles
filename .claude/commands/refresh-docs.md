---
description: Refresh stale docs after review PASS
---
Use the `refresh-docs` skill and run the repository docs refresh workflow.

Inputs:
- Raw args: `$ARGUMENTS`
- First arg: `$1`

Argument contract:
- Supported modes: `auto`, `human-only`, `agent-only`, `deep`
- If `$1` matches a supported mode, treat it as mode.
- Otherwise, use mode `auto`.
- Any remaining args are optional target docs.
- If no args are provided, use mode `auto` with no explicit targets.

Workflow:
1. Load the `refresh-docs` skill and follow it.
2. Assess docs impact from current changes before editing.
3. Apply only minimal, deterministic edits.
4. Prefer the provided target docs when they are sufficient.
5. Respect repository path policy:
   - reviewed docs: `README.md`, `AGENTS.md`, `dot_claude/AGENTS.md`,
     `docs/agents/**`
   - exempt docs: `docs/**` except `docs/agents/**`, `assets/README.md`
6. Do not use this command to bypass normal review for reviewed/non-doc changes.
7. If reviewed docs are changed during refresh, run `@dotfiles-reviewer` once.

Final output requirement:
- End with exactly one result line:
  `DOCS_RESULT=NO_CHANGES`, `DOCS_RESULT=UPDATED_HUMAN`,
  `DOCS_RESULT=UPDATED_AGENT`, `DOCS_RESULT=UPDATED_BOTH`, or
  `DOCS_RESULT=BLOCKED`.
