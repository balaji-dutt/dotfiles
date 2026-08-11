<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Claude permission rules and tool names

Tool names in `permissions.allow` are version-dependent. Claude Code retires
tools, renames them, and puts replacements behind feature gates, but it does
**not** validate rule names against a tool registry — an entry for a tool that
no longer exists parses cleanly and matches nothing. Stale entries are
therefore silent, which is why they need a doc rather than a linter.

This page records the vocabulary as verified against a specific Claude Code
build, and the recipe to re-verify it after an upgrade.

## Where the rules live

`dot_claude/settings-base.json` is the single source for `env`, `permissions`,
`statusLine`, and plugin registration:

- On the host, `dot_claude/modify_private_settings.json` is a
  `chezmoi:modify-template` that reads the base, merges in aoe-owned hook
  groups and the macOS OpenUsage hooks, and writes `~/.claude/settings.json`.
- The homelab-IaC devcontainer never runs chezmoi. `install_claude_managed_asset_links`
  in `dot_devcontainer/devcontainer-common.sh` symlinks
  `settings-base.json` straight to `~/.claude/settings.json`. **The base must
  stay parseable plain JSON** — no template syntax, and no comments, which is
  why rationale for individual entries lives here instead of inline.
- `configs/devcontainer-sync.jsonc` mirrors the base into
  `private_Documents/development/container-dotfiles/dotfiles/dot_claude/`.
  Run `bash ./assets/sync-devcontainer-assets.sh` after editing it.

Repo-scoped rules live in `.claude/settings.json` and cover chezmoi commands
only. The `mcp__cbm__*` allow/deny entries are documented in
`docs/automation/claude-mcp.md`.

## Rule syntax that bites

The parser treats the argument in `Tool(argument)` differently per tool:

| Rule form | Applies to | Notes |
| :--- | :--- | :--- |
| `Tool(path-glob)` | `Read`, `Write`, `Edit`, `Glob`, `NotebookRead`, `NotebookEdit`, `Cd` | the only tools whose argument is parsed as a file pattern. This list outlives the tools themselves — `NotebookRead` is on it but no longer exists |
| `Bash(prefix:*)` | `Bash` only | `:*` must be the final characters |
| `WebFetch(domain:host)` | `WebFetch` only | a bare URL or `https://` prefix is rejected |
| `Tool` | any | bare rule, matches every invocation |
| `Tool(anything)` | anything else | never acts as a *path* rule, but a tool with its own rule-content matcher can still match on something else — `Grep` matches the argument against the search pattern |

Three consequences worth knowing. They rest on two *different* mechanisms, so
the evidence for each is called out separately.

- **Path rules are consolidated under `Read` and `Edit`.** Writing `Glob(**)`
  or `Write(**)` produces Claude Code's own warning: *"`<rule>` is not matched
  by file permission checks — only `<Read|Edit>(path)` rules are. Use
  `<Read|Edit>(...)` instead"*. That message is the evidence, and it covers
  `Glob` -> `Read` and `Write`/`MultiEdit`/`NotebookEdit` -> `Edit`. Note that
  `Write` appears in the file-pattern table above and **still** gets the
  warning: the argument parses, but the match is delegated to `Edit` rules.

- **`Grep` is covered by `Read(path)` rules too, but for a different reason.**
  The validator says nothing about `Grep`; the tool definition does. `Grep`'s
  `checkPermissions` delegates to the shared file-permission helper, which
  resolves the tool's `getPath()` (the `path` argument, or the working
  directory) and walks it through the `read` rule bucket — deny, then ask, then
  allow. So a `Read(...)` allow rule covering the searched path allows the
  `Grep` call.

  `Grep` is nonetheless **not** a file-pattern tool. Its rule-content matcher
  compares the rule argument against the **search pattern**, not against a
  path. `Grep(**)` was therefore being tested against the regex being searched
  for, which is why the old entry was inert as a path rule. `LS(**)` was inert
  for the simpler reason that `LS` no longer exists.

- **A bare `Tool` rule matches unconditionally**, before any of the above: the
  matcher returns true as soon as the tool names agree and the rule carries no
  argument. `settings-base.json` lists a bare `Grep` and a bare `Glob` on that
  basis. They are partly redundant with `Read(**)`, and deliberately so — they
  hold regardless of the working directory, and they keep both names visible in
  the allow list so an upgrade that changes the coverage rules shows up here
  rather than as a silent behaviour change.

Some names are aliases. As of 2.1.227: `Task` -> `Agent`,
`KillShell`/`KillBash` -> `TaskStop`, `BashOutput`/`AgentOutput` ->
`TaskOutput`, `ListPeers` -> `ListAgents`, `ListMcpResources` ->
`ListMcpResourcesTool`.

Normalization runs on **both sides** of the comparison — the invoked tool name
and the rule's tool name are each passed through the alias map before they are
compared. So a rule naming only the canonical target still matches an
invocation that arrives under the alias, and vice versa. Prefer the canonical
name; there is no need to list both. (This is why `Task` was dropped in favour
of `Agent` rather than kept alongside it.)

## Verified vocabulary (Claude Code 2.1.227)

| Name | Status | Basis |
| :--- | :--- | :--- |
| `Grep` | live | tool definition present ("Content search built on ripgrep") |
| `Glob` | live | tool definition present ("Fast file pattern matching") |
| `Agent` | live | canonical name; `Task` is an alias for it |
| `TaskCreate`, `TaskGet`, `TaskList`, `TaskUpdate`, `TaskStop`, `TaskOutput` | live | current todo/background-task family |
| `TodoWrite` | live, superseded | a feature gate decides whether a session gets `TodoWrite` or `TaskCreate`, so both are allowed |
| `LS` | retired | no tool definition; survives only in one legacy allowlist array |
| `NotebookRead` | retired | no tool definition; `Read` parses `.ipynb` directly. Still accepted as permission-rule syntax |
| `TodoRead` | retired | absent from the binary entirely |

`LS(**)`, `NotebookRead(**)` and `TodoRead` were removed from
`settings-base.json` on this basis. `TodoWrite` was kept and the `Task*` family
added beside it.

### Bash search helpers

`Bash(grep:*)`, `Bash(rg:*)`, `Bash(find:*)`, `Bash(ls:*)` and `Bash(tree:*)`
are allowed. A session that gets the native `Grep`/`Glob` tools should use
those, but not every session does — tool availability varies by model and
feature gate — and the shell fallback should not prompt.

`Bash(fd:*)` is deliberately absent: `fd` is not installed by
`configs/mise.toml` or any other provisioning path in this repo. Add the rule
if that changes.

`Bash(rg:*)` is an exec vector, not just a search rule — `rg --pre <cmd>` runs
an arbitrary command per file. The allowlist already accepts that class through
`Bash(find:*)` (`-exec`), so `rg` is consistent with the existing posture
rather than a new exposure, but it is not a read-only guarantee.

## How to re-verify after an upgrade

A session's visible tool list is **not** evidence. It varies by model and by
feature gate, so a tool missing from one session may still exist. Check the
shipped binary instead.

```sh
# readlink -f is absent on older macOS; fall back to the launcher path, which
# the canary check below will flag as the wrong file.
bin="$(readlink -f "$(command -v claude)" 2>/dev/null || command -v claude)"
claude --version

# Cheap first pass: zero hits means the name is gone.
for t in Grep Glob LS NotebookRead TodoRead TodoWrite Task Agent TaskCreate; do
  printf '%-14s %s\n' "$t" "$(LC_ALL=C grep -a -c "\"$t\"" "$bin" || true)"
done
```

`Grep` and `Glob` are in that list as canaries: if **every** name reports `0`,
`$bin` resolved to a launcher or wrapper rather than the real bundle — or
`claude` was not on `PATH` at all — and the whole column is meaningless. The `|| true` keeps a zero count rendering as `0`
instead of failing the command, which matters if the loop is lifted into a
script that assigns the result under `set -e`.

A handful of hits can still mean the name only survives in a legacy list, so
confirm the two authoritative structures directly:

```sh
python3 - "$bin" <<'PY'
import sys
data = open(sys.argv[1], 'rb').read()
for needle in (b'filePatternTools:[', b'KillBash:'):
    i = data.find(needle)
    print(needle.decode(), '->',
          data[max(0, i - 200):i + 400].decode('utf-8', 'replace')
          if i >= 0 else 'NOT FOUND')
PY
```

`filePatternTools` is the list of tools whose rule argument is a path glob.
`KillBash:` lands inside the alias map — anchor on one of its keys rather than
on the map's variable name, which is minified and changes between builds.

A live tool also has its own definition object. Grep for its description text,
or for the `checkPermissions` / `ruleContentField` pair, which is what tells
you *how* a rule argument for that tool is interpreted.

Update the table above with the version you tested when the answers change.

## Related

- `docs/automation/claude-mcp.md` — the `mcp__cbm__*` and DeepWiki entries
- `docs/agents/generated-agents.md` — the `tools:` allowlists on the two
  generated subagents, which use the same vocabulary
- `docs/devcontainers.md` — the container mirror this file's rules ship into
