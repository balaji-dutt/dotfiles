<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# unslop fork status and dotfiles sync

Status snapshot of the local `unslop` fork, the provenance of the unslop OpenCode
content carried into this dotfiles repo, and how unslop behaves in Claude Code.

This is an analysis/reference document: it records the state as observed on
2026-06-20, not a set of actions to take. The fork lives at
`~/Documents/development/unslop` (referred to below as the unslop repo). Findings were
gathered with read-only `git`/`diff` commands; the reproduction commands are listed in
the sync section.

unslop itself is a cross-platform plugin that strips "AI slop" from LLM output
(sycophancy, stock vocabulary, hedging stacks, robotic transitions) while preserving
code, URLs, file paths, headings, tables, and technical terms.

## 1. unslop fork status (worktrees and branches)

The fork is checked out across **five git worktrees** (`git worktree list`). The
important caveat: several worktrees carry **uncommitted** changes that never became
commits, which is why the work was easy to lose track of. The committed history alone
understates what exists on disk.

Default branch is `main` (`5af59d9`, tracks `origin/main`).

| Worktree / branch | HEAD | Committed delta vs `main` | Uncommitted work in the worktree |
|---|---|---|---|
| `main` | `5af59d9` | baseline | only an untracked `worktrees/` directory |
| `feat/add-opencode-support` | `e37604e` | +1 commit: adds `opencode/` (`README.md` + 10 command files), updates `README.md` + `docs/RESEARCH_AND_TECH.md`, adds a test (14 files, +164/-4). The OpenCode compatibility branch. | clean |
| `feat/add-unslop-file-voice` | `c9375e1` | +1 commit on top of `feat/add-opencode-support` (ahead of `main` by 2): adds `skills/unslop-file-voice/`, `opencode/commands/unslop-file-voice.md`, `commands/unslop-file-voice.toml`, updates `opencode/README.md` and a test (5 files, +135). The file-voice work, now committed; local only, not pushed. | clean |
| `feat/add-chatgpt-skill-package` | `f726971` | +1 commit: adds `chatgpt/unslop/` package, a `--export-voice-profile markdown` CLI flag, a `style_memory.py` module, and tests (17 files, +586/-11). Independent of the OpenCode work. | clean |
| `feat/add-opencode-chatgpt-web--eaa87c02` | `5af59d9` (= `main`) | none | UNCOMMITTED: a re-integration on top of latest `main`. Adds `opencode/` (10 commands, no file-voice) + `chatgpt/` package, and modifies `.gitignore`, `README.md`, `docs/RESEARCH_AND_TECH.md`, `cli.py`/`style_memory.py` (three mirrored copies each), and tests. The newest combined attempt; never committed. |

Stale remote branches, behind or already merged, with no live work:

- `origin/codex-core-enhancement-2026-04-28` (`470264f`) — a CI regression-baseline
  fix, already merged into `main`.
- `origin/docs/readme-prelaunch-2026-04-28` (`9fb1684`) — v0.6.2 release prep
  (SVG to PNG assets, version bumps across marketplace manifests), superseded by `main`.

Net: the unfinished work now spans **three committed branches**
(`feat/add-opencode-support`, `feat/add-unslop-file-voice`,
`feat/add-chatgpt-skill-package`) **plus one worktree with uncommitted-only changes**
(`feat/add-opencode-chatgpt-web--eaa87c02`). The `feat/add-unslop-file-voice` work was
committed as `c9375e1` on 2026-06-20 (local, not pushed).

## 2. Dotfiles, unslop, and dev-container sync status

The unslop OpenCode content under `private_dot_config/opencode/` is assembled from
**three different unslop sources**, not a single branch. (A common recollection is that
it was "copied from the OpenCode compatibility branch" — that is only partly true.)

- **Commands** (`private_dot_config/opencode/commands/`): the 10 standard command files
  are byte-identical to `feat/add-opencode-support:opencode/commands/` (committed).
- **Skills** (`private_dot_config/opencode/skills/`): the six shared skills (`unslop`,
  `unslop-commit`, `unslop-file`, `unslop-help`, `unslop-reasoning`, `unslop-review`),
  including the full Python backend (`humanize.py`, `style_memory.py`, `soul.py`, and so
  on), are byte-identical to `main:skills/` — the repo's single source of truth — not to
  any feature branch. The OpenCode branch added no skills.
- **`unslop-file-voice`** (command and skill): byte-identical to the
  `feat/add-unslop-file-voice` branch, now committed at `c9375e1`. It is **not** from the
  chatgpt branch.
  - The chatgpt branch (`feat/add-chatgpt-skill-package`) is independent: it adds the
    `chatgpt/` package and a `--export-voice-profile` flag. The voice backend that the
    file-voice skill calls (`--voice-sample`, `--voice-memory`, `--save-voice-profile`)
    already lives in `main`'s `cli.py`; the chatgpt branch only adds
    `--export-voice-profile` on top. The `unslop-file-voice` skill and command are the
    file-voice branch's wrappers over the existing backend.
  - The dotfiles carry only the `.md` command form, not the `.toml` variant that also
    exists on that branch.
  - Consequence: `unslop-file-voice` now exists as a commit on the local
    `feat/add-unslop-file-voice` branch, plus this dotfiles repo and its dev-container
    mirror. The branch is local only and not pushed to any remote.

**Dev-container mirror.** The mirror at
`private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/`
is byte-identical to the host copy across all seven unslop skill directories and all
eleven unslop command files. It is kept in sync by the manifest at
`configs/devcontainer-sync.jsonc` (`cleanup_managed: true`); the host copy is the source
of truth and the container copy must not be hand-edited (see `docs/devcontainers.md`).

**Reproducing the comparison (read-only).** With `UNS=~/Documents/development/unslop` and
`DOT=<repo>/private_dot_config/opencode`:

```sh
# Commands vs the committed OpenCode branch
diff <(cat "$DOT/commands/unslop.md") \
     <(git -C "$UNS" show feat/add-opencode-support:opencode/commands/unslop.md)

# Skills vs main (single source of truth)
diff <(cat "$DOT/skills/unslop/SKILL.md") \
     <(git -C "$UNS" show main:skills/unslop/SKILL.md)

# unslop-file-voice vs the committed branch
diff -r "$UNS/worktrees/feat/add-unslop-file-voice/skills/unslop-file-voice" \
        "$DOT/skills/unslop-file-voice"

# Host vs dev-container mirror
MIR=<repo>/private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode
diff -r "$DOT/skills" "$MIR/skills"
```

## 3. unslop in Claude Code: default behaviour, config surface, and the OpenCode gap

This section is the basis for a separate follow-up plan (enabling unslop in Claude Code
so it mirrors the OpenCode setup). It records behaviour and the config surface; it is not
a recommended configuration.

- **Integration model = always-on hooks.** The Claude Code plugin
  (`.claude-plugin/plugin.json` and `marketplace.json`) registers two JavaScript hooks:
  `SessionStart` (`hooks/unslop-activate.js`) and `UserPromptSubmit`
  (`hooks/unslop-mode-tracker.js`). They coordinate through a flag file
  `~/.claude/.unslop-active` and a `[unslop:BALANCED]` statusline badge.
- **Default after a marketplace install = active on every message.** On the first
  session, `SessionStart` auto-activates `balanced` mode and injects the unslop ruleset
  into every response. No `/unslop` invocation is required, so all output is processed
  from the first message onward.
- **Config surface:**
  - `UNSLOP_DEFAULT_MODE` environment variable (highest priority).
  - `~/.config/unslop/config.json` with
    `{ "defaultMode": "off" | "subtle" | "balanced" | "full" | "voice-match" | "anti-detector" }`.
  - Per-session control: `/unslop <mode>`, or natural language such as "stop unslop" /
    "normal mode".
  - Opt-in sub-skills via slash commands (`/unslop-commit`, `/unslop-review`,
    `/unslop-file`, `/unslop-reasoning`, `/unslop-help`).
  - Drift reinforcement (at turns 8, 16, 24, then every 16) is automatic and is only
    disabled by editing the hook code.
  - There is no per-message allowlist. Granularity is mode-level plus a per-session
    toggle, not "only certain kinds of messages."
- **The OpenCode model is different, and is the compatibility target.** OpenCode has no
  always-on hook; it ships only the commands and skills invoked on demand. So matching
  the OpenCode setup in Claude Code maps to: set the Claude Code default to `off` (no
  auto-injection) and expose the same commands and skills (including the local
  `unslop-file-voice`) for opt-in use — that is, replicate the opt-in command/skill model
  rather than the always-on hook.

The actual Claude Code enablement (which commands/skills to install where, the
`defaultMode: off` config, and whether to manage it through chezmoi and the dev-container
mirror) is intentionally deferred to a separate follow-up plan.
