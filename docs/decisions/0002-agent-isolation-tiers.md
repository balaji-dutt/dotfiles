<!-- markdownlint-disable MD013 MD040 MD041 -->

# ADR 0002: Agent isolation tiers and autonomy posture

## Status

Accepted — 2026-08-12. Config-state observations in this document reflect the
repo on that date and will drift; see [Revisit triggers](#revisit-triggers).

## Context

`opencode --auto` removes the last interactive approval gate from agent
sessions, and permission fatigue had already hollowed that gate out — blind
approvals were the norm before `--auto` made them automatic. The `auto`
AoE profile
([`private_dot_config/agent-of-empires/profiles/auto/config.toml`](../../private_dot_config/agent-of-empires/profiles/auto/config.toml))
was created as an explicit admission of that. Meanwhile the
[lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)
(private data + untrusted content + an exfiltration path) is fully present in
day-to-day agent use here.

What the safety posture actually is today:

- **Permission-layer only.** Claude Code allow/deny/ask lists in
  [`dot_claude/settings-base.json`](../../dot_claude/settings-base.json), the
  OpenCode `permission` block in
  [`private_dot_config/opencode/opencode.jsonc`](../../private_dot_config/opencode/opencode.jsonc),
  the cc-safety-net and envsitter-guard plugins, and the repo hooks. No OS
  sandboxing, no egress control anywhere.
- **Secrets are ambient.** Every secret resolves at `chezmoi apply` time into
  plaintext (`~/.config/opencode/opencode.env`,
  `~/.claude-code-router/config.json`), and the shell rc files source
  `opencode.env` into every interactive shell. API keys therefore sit in the
  environment every agent inherits: an agent running `env` sees them in any
  repo, sandboxed or not. A devcontainer incident where an agent read `env`
  and exposed API tokens was a symptom of this, not a container problem.
  The Claude-side deny patterns additionally match `.env*` basenames only,
  so rendered files like `opencode.env` and
  `~/.claude-code-router/config.json` fall outside them while `Read(**)` is
  blanket-allowed.
- **The devcontainer is a dev environment, not a boundary.** The homelab-IaC
  devcontainer mounts the host SSH-agent socket and host dotfiles, has no
  egress restriction, and `postCreate.sh` persists `OP_SERVICE_ACCOUNT_TOKEN`
  into the container shell rc.
- **cc-safety-net policy is unmanaged.** The plugin is enabled on both
  harnesses, but its policy file is not chezmoi-managed, so the effective
  preset on each host is an accident.
- **`--auto` respects denies.** It auto-approves everything not explicitly
  denied; deny rules survive it (the reason it was preferred over AoE YOLO
  mode — see [`docs/plannotator.md`](../plannotator.md)). Under `--auto`,
  deny lists are the only line of defense, and bash deny patterns are string
  matches an adversarial prompt can route around.

## Options considered

Three axes were decided together; per-axis options and why the losers lost:

**Autonomy posture (host-config repos):**

- *Ban `--auto` for dotfiles work.* The "correct" answer on paper, defeated
  in practice: permission fatigue produces blind approvals, so interactive
  mode was not delivering the scrutiny the ban assumes.
- *Keep asks as the security layer, tuned harder.* Same failure mode; any
  gate that fires often gets rubber-stamped, and `--auto` turns every ask
  into a yes.
- *Deny-based boundary + prompt budget (chosen).* Treat asks as UX, not
  security. Anything catastrophic must be an explicit deny or structurally
  impossible; asks are reserved for a handful of rare, high-signal moments.
- *MicroVM/docker-sandbox replacement.* Rejected here as a rebuild of the
  tooling — see the container axis.

**Secrets depth:**

- *Env hygiene first (chosen).* Keep apply-time rendering; stop sourcing
  keys into every shell; gate keys per-process; deny-read the rendered
  artifacts. No vault migration.
- *Scoped service accounts everywhere.* Real isolation win, but forces the
  1Password entry migration and token lifecycle management now, for
  marginal benefit over hygiene until container tiers demand it. Kept
  limited to the existing container path
  ([`assets/render-container-configs.sh`](../../assets/render-container-configs.sh)).
- *Full runtime fetch (`op run` wrappers, nothing rendered at rest).*
  Rejected: the harness itself needs its API key and child processes
  inherit the harness environment, so the ceiling is low relative to the
  effort; latency and re-auth friction on every invocation.

**Container strategy (non-dotfiles repos):**

- *Harden existing devcontainers (chosen).* Preserves the substantial
  tooling investment (sync model, launcher, overlays — see
  [`docs/devcontainers.md`](../devcontainers.md)); egress allowlist and
  secret scoping address the actual incidents.
- *MicroVM / docker-sandbox as the new standard.* Strongest boundary, but
  obsoletes the devcontainer tooling wholesale; premature while the trusted
  tier's problems are fixable in place.
- *Both tiers now.* Two isolation mechanisms to maintain without a current
  workload that needs the throwaway tier.

## Decision

Adopt a trust-tier × autonomy model with two operating principles:

1. **Asks are UX, not security.** Fatigue defeats them interactively;
   `--auto` defeats them automatically. The security boundary is hard
   denies, structural impossibility (human-run `chezmoi apply`, no ambient
   secrets), and OS-level confinement where the harness supports it.
2. **Few, loud gates.** Fold the safe, high-frequency majority of prompts
   into allow so the remaining asks are rare enough to actually be read.
   The `opencode-permission-capture` tooling exists for exactly this audit
   but currently produces 0-byte capture logs; fixing it is a prerequisite
   workstream, not an assumption.

| Tier | Scope | Autonomy | Secrets | Filesystem | Network |
| --- | --- | --- | --- | --- | --- |
| T0 — host config | this repo; anything mutating `$HOME` | interactive preferred; `--auto` tolerated as degraded mode once the deny set is complete | no ambient keys; deny-read all rendered secret artifacts | worktrees + evaluate Claude Code native sandbox for write-confinement | no egress control (accepted); minimize untrusted-content tools (WebFetch) |
| T1 — trusted dev repos | devcontainer-based projects | `--auto` is the normal mode | scoped service-account token injected per-process, never persisted in shell rc | container boundary | egress allowlist; SSH-agent mount opt-in per repo, scoped |
| T2 — untrusted / experimental | deferred | `--auto` | none reachable | throwaway microVM / docker sandbox | deny-all or allowlist |

Tier notes:

- **T0 is inherently not injection-safe.** Auditing reads `$HOME` targets
  (including rendered secret files) and the repo's purpose is host
  mutation, so no sandbox makes autonomous dotfiles work safe against a
  hostile prompt. The controls are structural: agents edit source and run
  dry-run audits (`cz-audit`); non-dry-run `chezmoi apply` becomes an
  explicit deny (today it falls through to ask, which `--auto` turns into
  a yes); a human runs `apply` outside the agent. Claude Code's native
  sandbox can confine writes to the worktree (the `rm -rf` answer);
  OpenCode has no equivalent — a known asymmetry this ADR records rather
  than hides.
- **T1's SSH-agent mount stays, scoped.** Dropping it wholesale breaks real
  workflows (ansible check-mode against homelab hosts). Instead: opt-in per
  repo via [`configs/devcontainer-sync.jsonc`](../../configs/devcontainer-sync.jsonc)
  (homelab-IaC keeps it, nothing else gets it by default), 1Password
  SSH-agent per-key authorization with time-boxed approvals on the host
  side, and egress confinement bounding where the keys can be used.
- **T2 is a revisit trigger, not a workstream.** No current workload needs
  it; defining it now would be speculative tooling.

Secrets posture (all tiers): env hygiene first. Stop sourcing
`opencode.env` globally; extend the existing per-profile gating pattern in
[`private_dot_config/opencode/opencode-profile.sh`](../../private_dot_config/opencode/opencode-profile.sh)
(which already scopes the Anthropic key) to all keys; add deny-read rules
for every rendered secret artifact in both harness configs.

## Consequences

- Implementation proceeds as a Beads epic ("Agent isolation tier model —
  ADR 0002") with child issues per workstream: env hygiene, deny-read
  coverage, the `chezmoi apply` deny, chezmoi-managed cc-safety-net policy,
  devcontainer egress allowlist, SSH-agent opt-in + per-key authorization,
  per-process `OP_SERVICE_ACCOUNT_TOKEN` injection, Claude Code sandbox
  evaluation (and OpenCode parity investigation), the
  `opencode-permission-capture` fix, and the prompt-budget review that
  depends on it.
- Until the deny set and env hygiene land, `--auto` in this repo remains
  what it is today: unprotected beyond the current deny lists. The ADR
  changes the target, not the present.
- OpenCode host sessions rely on pattern denies + cc-safety-net semantics
  only; sessions needing filesystem confinement should prefer Claude Code
  until OpenCode parity exists.
- Secret rotation remains manual; nothing here changes the apply-time
  rendering model.

## Revisit triggers

Reopen this ADR if any of the following occurs:

- A workload appears that needs the T2 tier (untrusted code, third-party
  repos, or high-autonomy experiments) — define the microVM/no-secrets
  tier then.
- OpenCode ships native OS sandboxing or env scrubbing for child
  processes, closing the harness asymmetry.
- Service-account usage expands beyond the container path, forcing the
  1Password vault migration this ADR deferred.
- Harness permission vocabularies change materially (see the re-verify
  procedure in [`docs/automation/claude-permissions.md`](../automation/claude-permissions.md))
  — the deny-based boundary is only as good as the rules still matching.
- Secret-rotation automation lands, changing the cost/benefit of rendered
  plaintext at rest.

## Sources

Collected 2026-08-12: repo survey of
[`dot_claude/settings-base.json`](../../dot_claude/settings-base.json),
[`private_dot_config/opencode/opencode.jsonc`](../../private_dot_config/opencode/opencode.jsonc),
[`docs/devcontainers.md`](../devcontainers.md),
[`docs/plannotator.md`](../plannotator.md);
[The lethal trifecta for AI agents](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)
(Simon Willison, 2025).
