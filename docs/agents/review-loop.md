<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Dotfiles Review Loop

This document explains when the dotfiles review gate should trigger, when it
should not, and how post-review docs refresh should behave.

## Review gate path policy

- Exempt from review-gate marking:
  - `docs/**` except `docs/agents/**`
  - `assets/README.md`
- Still reviewed (normal review required):
  - `docs/agents/**`
  - `README.md`
  - `AGENTS.md`
  - `dot_claude/AGENTS.md`
- Any non-doc change remains reviewed as usual.

## Expected review loop behavior

1. Edit reviewed files or normal files.
2. Run `@dotfiles-reviewer` until the final line is exactly:
   `DOTFILES_REVIEWER_RESULT=PASS`
3. Only after PASS, assess documentation impact.
4. If docs are stale, invoke `refresh-docs` and make minimal updates.
5. If `refresh-docs` changes reviewed docs (`README.md`, `docs/agents/**`,
   `AGENTS.md`, `dot_claude/AGENTS.md`), run one more review pass.

## Docs refresh conventions

- Docs refresh is conditional and idempotent, not automatic on every PASS.
- Prefer small targeted edits over broad rewrites.
- Keep `README.md` concise; detailed operational content belongs under `docs/`.
- Keep `.opencode/journal.md` as working memory; promote only durable guidance
  into `docs/agents/`.

## Manual command wrapper

- Use `/refresh-docs` as a manual wrapper around the `refresh-docs` skill.
- Supported modes: `auto`, `human-only`, `agent-only`, `deep`.
- Optional target docs can be passed after the mode.
- If mode is omitted or unrecognized, the command defaults to `auto`.
- If reviewed docs are changed, run one more `@dotfiles-reviewer` pass.

## Related files

- OpenCode instructions:
  `.opencode/instructions/post-review-docs.md`
- OpenCode marker plugin:
  `.opencode/plugins/mark-needs-review-on-file-edited.js`
- OpenCode enforcer plugin:
  `.opencode/plugins/enforce-dotfiles-review.js`
- OpenCode config wiring:
  `.opencode/opencode.jsonc`
- Claude stop hook:
  `.claude/hooks/enforce-review-on-stop.sh`
- Skill:
  `.opencode/skills/refresh-docs/SKILL.md`
- Command:
  `.opencode/commands/refresh-docs.md`
