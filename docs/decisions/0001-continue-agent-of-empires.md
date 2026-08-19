<!-- markdownlint-disable MD013 MD040 MD041 -->

# ADR 0001: Continue investing in Agent of Empires

## Status

Accepted — 2026-08-12. All project metrics in this document were collected on
that date and will drift; see [Revisit triggers](#revisit-triggers).

## Context

[Agent of Empires](https://github.com/agent-of-empires/agent-of-empires)
(`aoe`, Rust, MIT) is the coding-harness multiplexer this repo is built
around. Hacker News discussion of the category is dominated by other tools —
Herdr, Superset, Paseo, and the pre-release Superlogical — while AoE barely
registers in those threads. That visibility gap matters: release cadence
alone is no longer a reliable proxy for durable developer attention now that
AI assistance makes velocity cheap, and high-velocity tools in this space
have faded before. This ADR is a deliberate check on whether continued
investment (including building AoE plugins) is justified.

What this repo currently depends on:

- Config overlay [`private_dot_config/agent-of-empires/modify_config.toml`](../../private_dot_config/agent-of-empires/modify_config.toml):
  agent command overrides (Plannotator wrappers), `[status_hooks]` wired to
  `~/bin/aoe-notify`, worktree path template
  `./worktrees/{branch}-{session-id}`, tmux clipboard and status bar.
- The `auto` profile
  ([`private_dot_config/agent-of-empires/profiles/auto/config.toml`](../../private_dot_config/agent-of-empires/profiles/auto/config.toml))
  for auto-approved OpenCode sessions.
- Notification delivery via
  [`bin/executable_aoe-notify.tmpl`](../../bin/executable_aoe-notify.tmpl)
  and the OpenCode notifier bridge (see
  [`docs/agents/aoe-notifications.md`](../agents/aoe-notifications.md)).
- Provisioning on every platform: Homebrew (macOS), Renovate-pinned release
  in `configs/packages.yaml` (WSL2, 1.13.2 at time of writing), pinned
  source build in devcontainers. Native Windows is unsupported.

## Options considered

Requirements derived from actual usage, roughly in priority order:

1. tmux-backed sessions (survive detach; existing scripts and espanso
   helpers assume tmux).
2. Cross-platform parity: macOS, WSL2, devcontainers.
3. OpenCode first-class, Claude Code alongside.
4. Git worktree lifecycle with configurable path templates.
5. Status hooks that drive the existing native notification scripts.
6. GitLab-compatible (or at least SCM-neutral with extension points);
   GitHub-only enrichment is a dead end here.
7. OSS license; config as code manageable by chezmoi.

| Dimension | AoE | Herdr | Superset | Paseo | Superlogical | DIY tmux |
| --- | --- | --- | --- | --- | --- | --- |
| Architecture | tmux frontend | tmux-style server/client (own PTYs) | Electron desktop IDE | daemon + desktop/mobile/web | server-side, libghostty clients | raw tmux |
| tmux fit | native | replaces tmux | none | none | replaces tmux | native |
| OpenCode + Claude | both first-class | both (tool-agnostic) | both | both | unknown | manual |
| Git worktrees | first-class, path templates | none | core (workspace = worktree) | yes | unknown | manual |
| macOS / WSL2 / devcontainer | all three (this repo provisions all) | macOS/Linux, Windows beta; no devcontainer story | macOS-first, Linux experimental | desktop-app-centric | pre-release | all |
| Status hooks / notifications | `[status_hooks]` → own scripts | built-in + plugin ecosystem | built-in | built-in push | unknown | manual |
| Extension model | plugins: JSON-RPC over stdio, any language, capability grants | plugins: "the CLI is the API", any language | skills + MCP + SDK | skills + MCP | unknown | shell |
| SCM neutrality | neutral core; GitHub via optional plugin | neutral (no repo awareness) | GitHub-centric PR view | neutral | unknown | neutral |
| License | MIT | Apache-2.0 | ELv2 (source-available) | AGPL-3.0 | "OSS releases" promised | n/a |
| Stars (2026-08-12) | 3,048 | 27,910 | 12,870 | 13,436 | no public repo | n/a |
| Core team / backing | ~2 (njbrake, Seluj78); Mozilla.ai support, informal | solo founder; YC F26 | 3–4 founders; YC P26 | solo maintainer; independent | 4 founders; ~$10M seed (reported) | n/a |
| Release cadence | ~biweekly minors since 2026-01 | weekly–biweekly | multiple per week | rapid, pre-1.0 | none | n/a |
| Peak HN thread | 118 pts | 404 pts | 108 pts | 92 pts | 796 pts | n/a |

Per-option assessment:

- **Herdr** — the category leader by stars and HN attention. Genuinely
  tmux-like architecture and the strongest plugin model. But it *replaces*
  tmux rather than fronting it, and it has no git-worktree support at all —
  it owns terminals, not repos. Adopting it means rebuilding the worktree
  workflow externally and migrating every tmux-adjacent helper.
- **Superset** — worktree-per-agent is core and PR review is polished, but
  it is an Electron desktop IDE: no tmux, macOS-first (Linux experimental,
  no WSL2 CLI parity), GitHub-centric, and ELv2 source-available rather
  than OSS. Architecturally the furthest from this repo's model.
- **Paseo** — daemon plus desktop/mobile clients, worktrees supported, but
  not tmux-based, effectively a single-maintainer project (one author has
  ~99% of commits), pre-1.0, AGPL. Its mobile story is its differentiator
  and is not a requirement here.
- **Superlogical** — the loudest HN signal (796 points) attached to the
  least product: waitlist-only, no public code, no stated agent/worktree
  support. Founders and funding make it the most credible future threat,
  not a present option.
- **DIY tmux** — always available as the fallback and a recurring HN
  sentiment ("I always come back to ghostty+tmux"). It forfeits status
  detection, worktree lifecycle, profiles, and the web dashboard; the whole
  point of AoE here is not re-implementing those in shell.
- **AoE** — the only option that satisfies every requirement today:
  tmux-native, worktree lifecycle with path templates, OpenCode
  first-class, provisioned on all three platforms by this repo, status
  hooks already wired to native notification scripts, SCM-neutral core with
  a plugin seam where GitLab support can be added. Maintenance health is
  strong: ~biweekly minors, 201 issues closed in the 30 days before this
  ADR, 92 listed contributors. Its weakness is everything *around* the
  code: mindshare roughly an order of magnitude below the leaders, a
  two-person core, informal Mozilla.ai support versus VC-backed rivals, a
  plugin API that went through 12 `api_version` revisions in ~3 months with
  docs lagging the code, and a third-party plugin ecosystem that is
  essentially nonexistent.

## Decision

Continue investing in Agent of Empires, including building the planned
session-operations plugins.

Fit and maintenance health outweigh the mindshare gap today. Every
alternative requires giving up something this setup actually depends on
(tmux, worktrees, WSL2/devcontainer parity, or an OSS license), whereas
AoE's weaknesses are risks to monitor, not gaps in function.

Risks accepted, explicitly:

- **Mindshare.** ~10x fewer stars than Herdr and near-zero presence in the
  big HN threads. Thin mindshare means a weak contributor pipeline and
  little ecosystem gravity — the exact fade pattern that motivated this
  ADR. This is the primary risk.
- **Bus factor ≈ 2.** njbrake plus Seluj78 write most commits; Mozilla.ai
  support is informal, not a funded commitment.
- **Plugin API churn.** `api_version` 1→12 in ~3 months; the plugin docs
  contradict the changelogs. Anything built must pin `aoe_version` ranges
  and expect chasing.

Mitigations that shape *how* we invest:

- Keep plugin investments small, independently useful, and read-only first
  (the plugin epic's spec already mandates this) so each is cheap to
  abandon if AoE fades.
- Keep notification *delivery* in the existing harness-agnostic scripts;
  AoE only triggers them. Exit cost stays low.
- Keep the Renovate pin discipline for WSL2 and devcontainer builds so an
  upstream breaking change never lands unreviewed.
- Validate plugin APIs against the installed version and release
  changelogs, not the published docs.

## Consequences

- The AoE session-operations plugin epic proceeds in Beads (`dots-xbn`,
  `[epic] AoE session-operations plugins`).
- Plugin manifests pin tight `aoe_version` ranges; API assumptions are
  re-verified per AoE minor release.
- `aoe-notify` and the OpenCode notifier bridge remain the notification
  delivery layer regardless of any AoE plugin work.
- This decision is revisited when any trigger below fires, or otherwise
  opportunistically when the category shifts.

## Revisit triggers

Reopen this ADR if any of the following occurs:

- No AoE minor release for more than ~3 months, or open-issue triage
  visibly stalls.
- Both core maintainers go inactive with no successor, Mozilla.ai support
  ends, or the repo is archived.
- Herdr ships first-class git-worktree lifecycle management (its one
  disqualifying gap here).
- Superlogical ships an open, tmux-compatible runtime with worktree and
  OpenCode support.
- An AoE breaking change orphans the plugin API with no migration path for
  the plugins built under the epic above.

## Sources

Collected 2026-08-12: [AoE repo](https://github.com/agent-of-empires/agent-of-empires),
[AoE docs](https://www.agent-of-empires.com/docs/),
[plugin API](https://www.agent-of-empires.com/docs/plugin-api/),
[Herdr](https://github.com/herdrdev/herdr),
[Superset](https://github.com/superset-sh/superset),
[Paseo](https://github.com/getpaseo/paseo),
[Superlogical](https://www.superlogical.com/),
HN metrics via the [Algolia HN API](https://hn.algolia.com/api).
Unverified figures (funding amounts, self-reported traction) are marked
"reported" above.
