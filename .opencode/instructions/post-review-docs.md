# Post-review docs refresh

After `DOTFILES_REVIEWER_RESULT=PASS`, assess whether the latest changes made
documentation stale.

If docs are stale, load the `refresh-docs` skill with the `skill` tool and
follow it.

Use docs refresh only when impact exists. Keep edits minimal and idempotent.

Reviewed docs:

- `README.md`
- `AGENTS.md`
- `dot_claude/AGENTS.md`
- `docs/agents/**`

Exempt docs:

- `docs/**` except `docs/agents/**`
- `assets/README.md`

If docs refresh changes reviewed docs, rerun `@dotfiles-reviewer` once.
If it changes only exempt docs, do not create an extra docs-only review cycle.
