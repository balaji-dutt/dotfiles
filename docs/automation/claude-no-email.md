<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Claude Code `no-email` mod

When you sign in with claude.ai, Claude Code attaches a `userEmail` context
block (taken from `oauthAccount.emailAddress` in `~/.claude.json`) to every
conversation. No setting turns it off, and models sometimes copy the address
into request headers or payloads they write. The `no-email` mod registers a
`prompt.context` hook that lets the rest of the chain build the context, then
drops the block named `userEmail`.

## Source and ownership

The mod is a fork of
[AlexErrant/claude-code-no-email](https://github.com/AlexErrant/claude-code-no-email)
at `06ddeb65ba9d9d204ea88a534c363751d54f0787` (MIT, license copied alongside).
This repo owns it and does not sync from upstream. The hook is a few lines
long, and upstream ships no marketplace manifest that the plugin refresh hook
could consume. To pick up a later upstream change, compare
`.claude/skills/no-email/` upstream against `dot_claude/skills/no-email/` by hand.

| Path | Purpose |
| :--- | :--- |
| `dot_claude/skills/no-email/dot_claude-plugin/plugin.json` | Manifest; enables skills-dir discovery as `no-email@skills-dir` |
| `dot_claude/skills/no-email/hooks/hooks.json` | Lists the `register.ts` hook module |
| `dot_claude/skills/no-email/hooks/register.ts` | The `prompt.context` filter |
| `dot_claude/skills/no-email/tests/register.test.ts` | Upstream mod tests, run by `claude plugin test` |
| `tests/test_no_email_plugin.py` | Repo contract test; runs `claude plugin validate` and `claude plugin test` on a temp copy when `claude` is on `PATH` |

The target is `~/.claude/skills/no-email/` on every platform, including native
Windows. The `claude-user-config` mirror in `configs/devcontainer-sync.jsonc`
copies it to the container dotfiles. Skills-dir plugins load automatically, so
`settings-base.json` has no `enabledPlugins` entry for it.

## Limits

- Organization policy on Team or Enterprise logins, or managed settings, may
  skip hooks from user mods. Run `claude --debug` and look for a
  `no-email: prompt.context bypassed by ...` line.
- If Claude Code renames the `userEmail` block, the filter silently stops
  matching. The live check below catches that.

## Verify

After `chezmoi apply ~/.claude/skills/no-email` and a Claude Code restart (or
`/reload-plugins`):

```sh
claude plugin list | grep no-email
claude --safe-mode -p 'reply with OK'
claude -p 'reply with OK'
```

After each `-p` run, check the newest transcript JSONL under
`~/.claude/projects/<project>/`. The `--safe-mode` control run skips user mods,
so its transcript should contain `email address is`; the plain run's should not.
