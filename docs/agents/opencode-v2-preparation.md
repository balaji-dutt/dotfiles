# Inactive native OpenCode v2 preparation

## Decision and current boundary

`dots-0lqe` tracks **DV2-010**, the approved inventory, consumer contract and
preparation ledger. These are design metadata, not loadable OpenCode
configuration. No native payload, policy, provider route or agent is activated.
**DV2-020 requires separate approval**, even though its inactive preparation
does not depend on resolving every operational migration blocker.

The three DV2-010 files are:

- [`inventory.json`](../../configs/opencode-v2/source/inventory.json): exact
  source identities, historical hashes, attributed refresh and future selection.
- [`consumer-contract.jsonc`](../../configs/opencode-v2/source/consumer-contract.jsonc):
  ownership, composition, preservation requirements and future checker acceptance.
- This ledger: decisions, migration boundaries and verification records.

The approved plan's SHA256 is
`595af4820ac1dec8d99cf7c71bf01165aea95824922703549990621c1d451837`.
Its full text is preserved in the Bead's design field. The verified OpenCode
tracking anchor is in issue notes; no local tracking file is required.

## Pinned evidence and attributed refresh

The owner delivery is agentic-tooling commit
`81d4b66457e8d2b5628904c043dacd9d33a5c66e`, not the owner's moving HEAD.
The four owner references at that commit are
`docs/workflows/native-opencode-v2-agents.md`,
`docs/reference/opencode-v2-preparation.md`,
`docs/workflows/native-opencode-v2-source-matrix.md` and
`docs/roadmap/native-opencode-v2-agents-tasklist.md`, including External handoffs.

| Identity | Frozen value and meaning |
| --- | --- |
| Owner native reference | v2.2.3 / `082ac6281fd395c2d0c17fd3c38214db2f89621f`; preparation reference, not the cutover release |
| Owner canonical observation | `03e8935e716368ae6de4b44d279d878032ee44bc`; separate from delivery commit |
| Historical dotfiles observation | `418776882a73011590304379b8b57051182eaf20` |
| Refreshed consumer baseline | `333b882a9f7415589cf7df4aa8cf269783cd29ad` |
| Private plugin delivery | `263c5a1137584b2840be6463c0a1d2012710de28`, using v2.2.0 / `1097e2668b1e08ad63bdf0b1c4860b9bc01e420c`; not adopted or qualified against v2.2.3 |
| Producing build | Unknown; pinned source/package/lock hashes and committed emissions are evidence, not a locally verified producing binary |

All 47 historical dotfiles hashes matched their original committed bytes.
Against the refreshed consumer baseline, **44 are unchanged and three differ**:

| Source | Attributed delta | Preservation consequence |
| --- | --- | --- |
| Host `opencode.jsonc` | Six opencode-go context limits become 200000 with output 65536 in `d0e5c0d1` | Preserve limits alongside model/provider intent |
| Independent container `opencode.jsonc` | cc-safety-net source pin 2.6.19 to 2.7.6 | Neither installed-version evidence nor an enabled fixture PASS |
| Mirrored homelab npm list | OpenSpec 1.16, Beads 1.3.2, pnpm 12.17.0 and markdownlint 0.24.0; OpenCode remains 1.19.5 | Record mirror drift without claiming homelab Ansible/state qualification |

The inventory retains all 47 historical hashes, the three old/new hash pairs,
their commit attribution and additional current source hashes. Its immutable
owner-inventory reference preserves the original three-repository observation
and seven attributions. No historical observation is silently relabeled current.
Hashes identify committed bytes. The PowerShell helper's attribute-required CRLF
checkout has its own exact digest and LF comparison recorded separately; this
does not authorize general normalization or source drift acceptance.
No credential, dotenv value, authentication store or merged runtime configuration
is part of this inventory.

## Relevant subset, not a full archive

The owner package has 48 files: 15 agent bases, 31 retained skill files across
12 bundles, a canonical-bases map and its preparation manifest. All 15 owner
operational rows are blocked, the aggregate is withheld and `overallReady` is
false. The map is an ID-to-agent map for an `agents` value, not a root config.

The user selected only the relevant **14 future payload files**:

| Kind | Selection | Files |
| --- | --- | ---: |
| Generated bases | `agent-engineer`, `special-builder` | 2 |
| Complete skill bundle | `prompt-engineer` | 4 |
| Complete skill bundle | `prompt-optimizer` | 3 |
| Complete skill bundle | `skill-creator` | 3 |
| Complete skill bundle | `skill-reviewer` | 2 |

DV2-010 copies none of these files. Separately approved DV2-020 would import
exact committed emissions beneath `configs/opencode-v2/prepared/owner/` and
write separate consumer subset metadata. Selection closure is not closure of
the complete owner package or a runnable `agent-engineer`. Upstream `build`
and `plan-reviewer` must not replace the dotfiles-owned identities.

`plugin-developer` (three files, `native-plugin-guidance-unqualified`) and
`prompt-evaluator` (five files, `native-live-evaluator-unqualified`) remain
whole-bundle **upstream dependencies**. No local rewrite, partial import,
v1 fallback or evaluation waiver is authorized. Existing active v1 and Claude
skill copies remain untouched; generic LLM-as-judge evaluation remains usable.

The owner CLI rejects `configs` as an output target. Consumer import must not
disable that guard or invoke legacy generation. A future changed projection
needs separately approved owner regeneration and independent scored evaluation.
Canonical `sourceDigest` and emitted `digest` remain distinct even when equal
for verbatim skill files. Ordered input and overlay hashes are required, not
just a final output hash. See [existing provenance ownership](generated-agents.md).

## Consumer composition and preservation contract

The inventory and contract cover host global, independently maintained container
global, project overrides, ten authored/configured identities, prompts, four
profiles, POSIX and Windows helpers, mirrors, lifecycle, launchers and the AoE
overlay. This is a source inventory, not an exhaustive live runtime catalog.

Normal/default selection leaves `OPENCODE_CONFIG_DIR` unset. Composed selection
uses global-or-defaults, the defaults overlay, ordered normalized profiles,
workspace **agent-only** entries, then model transforms. Objects merge
recursively; lists/scalars replace. Exact relative `{file:...}` references are
rebased against their declaring input. The composer prefers JSONC, while profile
freshness probes prefer JSON. Its writes and legacy JSON removal make execution
against real user configuration unsuitable for offline verification.

The contract retains the exact v1 fallback mappings, Windows runtime-required
failure behavior and `Plan-Trial`'s project-only normal/default limitation.
Native `claude-agent` must never remap back to `anthropic-api` or the bridge.
Launcher `--auto` and AoE routing are separate from configured permission intent;
no persistent AoE state was inspected or assumed.

Preserve ordered rules, unmatched defaults, precedence, inheritance, named
delegates, child depth and model overrides, reviewer read-only intent including
mutating shell flags, credential/env/path scopes and unavailable-tool behavior.
The generated specialists' effective Bash ask must not become newly automatic
project grants. Native full-parent-session child inheritance differs from v1;
neither permits assuming a blanket policy intersection. MCP action permission
is not `mcp.use`, skill permission is not `skill.use`, and plugin loading is not
`integration.use`. Known native field shapes are not accepted operational maps.

Anthropic/Claude-backed OpenCode routes are intended for native `claude-agent`;
actual native IDs and efforts remain unknown. Preserve OpenAI, DeepSeek, Qwen
and other evidenced choices without synthetic aliases or fallbacks. Every
provider's sampling needs effective mapping, explicit per-row loss approval or
blocked status. An ineffective request-body overlay is not preservation. Any
widening, tightening or behavior loss requires an explicit decision.

## Overall migration ledger

| Workstream | Kind | Current boundary / next gate |
| --- | --- | --- |
| DV2-010 inventory and contract | Inactive preparation | Approved; local verification and phase-exit gates recorded below |
| DV2-020 subset, manifests, checker and fixtures | Inactive preparation | Separate approval; no operational aggregate required or implied |
| Two withheld skill bundles | Upstream dependency | Owner qualifies native guidance; not an inactive-preparation blocker |
| Consumer policy, provider, sampling and profile mapping | Future operational change | Per-row evidence/decisions; withhold unsupported aggregates |
| Native plugins and review-loop adoption | Separate active change | Delivery uses another reference; adoption plus manual/external gaps remain |
| Native attestation restoration and emission | Separate active change | Restore canonical Source-Definition/Source-Digest pair and verify trailer emission in collector/source logic and mirrors |
| Homelab Ansible and deployed state | Separate active change | Owning repository and deployment evidence required |
| CC Safety Net enabled fixture | Operational blocker | Preserve startup guard; 2.7.3 bug-fix report and 2.7.6 source pin are not enabled-fixture PASS |
| Cutover, rollout and v1 retirement | Separate active change | Explicit release/activation/deployment/retirement approval after blockers resolve |

DV2-020's proposed checker is `assets/check-opencode-v2-preparation.py --root .`,
with `tests/test_opencode_v2_preparation.py` and normal automation inventory,
candidate and suite registration. It must exercise subset closure/provenance,
stale/missing/extra bytes, symlink/escape rejection, recursive/ancestor discovery
and explicit registrations, final rendered Windows ignore ordering, raw-host
and fallback-mirror lifecycle exclusion, and ordered profile/model transforms.
It must prove active v1/Claude sources and legacy provenance remain unchanged.
Tests are synthetic and offline, not a native runtime/provider compatibility
campaign. Trust upstream declared support within its stated bounds.

## Verification and phase exit

Each edited path requires a local audit with peer probing disabled **before**
invocation, followed by `chezmoi doctor` after successful audits:

```sh
CZ_AUDIT_PEER_ENV=0 ./assets/cz-audit.sh check configs/opencode-v2/source/inventory.json
CZ_AUDIT_PEER_ENV=0 ./assets/cz-audit.sh check configs/opencode-v2/source/consumer-contract.jsonc
CZ_AUDIT_PEER_ENV=0 ./assets/cz-audit.sh check docs/agents/opencode-v2-preparation.md
chezmoi doctor
```

Nonzero audit results or `ERROR:` lines block progress. Only the documented
doctor exceptions apply. Also validate metadata, hashes, links, intended diff
and active-source invariance, run the unchanged legacy checker with
`python3 assets/check-automation-provenance.py --repo-root .`, obtain the
effective dotfiles review PASS and assess documentation impact.

Ordinary audits probe peers before printing their INFO instructions. Suppression
does **not** waive or satisfy required cross-environment verification. The owner
separately authorized DV2-010 helper probing, metadata audits and fast tests on
reachable configured peers, including helper-owned temporary snapshots/refs,
worktrees and cleanup, with `--no-agent-repair`. This does not authorize relay or
authentication repair, deployment, branch pushes or DV2-020 verification.
For this inactive metadata/documentation-only DV2-010 scope, passing required
local checks and review permits phase acceptance and a commit proposal despite
the disclosed zero-reachable-peer outcome. This is not a general exemption for
`configs/**` or a consequence of leaving test files unchanged. Executable code,
runtime-consumed configuration, generated payload, test registration or deployment
changes require reassessment and approval. Applicable reachable-peer audits/tests
remain required and actual failures still block. The Markdown ledger is exempt
from peer runs. DV2-020 retains its separate approval and verification obligations.
Both configured peers were unreachable; no peer audits or tests ran, and no retry,
repair or snapshot/ref transfer was attempted. The outcomes are final for this
task; later verification or repair requires fresh explicit authorization. Follow
[peer-environment rules](../tooling/peer-environments.md),
not an obsolete peer exemption or ad-hoc SSH path.

The report statements below use the documented status mapping; the probe printed
peer statuses, not report lines.

| Record | Current result |
| --- | --- |
| Read-only inventory | Historical 47 verified; 44 unchanged and three attributed deltas |
| Metadata, hashes, links and active-source checks | Local PASS on 2026-11-09: strict JSON/JSONC without duplicate keys, 47 historical and 22 additional source records, three deltas, 14 selected rows, complete bundle counts and four relative links; tracked active sources unchanged |
| Local audits and doctor | Three peer-suppressed audits exit 0 without `ERROR:`; doctor exit 0 with no failed checks on 2026-11-09; doctor used the configured main source directory, audits used this worktree |
| Legacy provenance | Unchanged checker PASS on 2026-11-09, including accepted divergences and tracked mirrors |
| Dotfiles review / docs impact | Required before phase exit; dated readback-verified result belongs in the same Bead's notes |
| Peer probe: `wsl2-debian` (`wsl2`) | `unreachable`; `not verified on wsl2: peer unreachable` |
| Peer probe: `wsl2-ubuntu` (`wsl2`) | `unreachable`; `not verified on wsl2: peer unreachable` |
| Cross-environment audits / fast tests | Not run: zero reachable peers; probe results are final for this task |
| DV2-010 phase exit | Required local checks and review govern this metadata/docs-only phase; zero reachable peers remain disclosed and unverified, not blocking; final acceptance belongs in dated readback-verified Bead notes |
| DV2-020 | Not approved; zero payload files imported |
| Commit | None; propose only after gates, then obtain separate approval |

The approved DV2-010 phase-exit addendum permits one exact acceptance-clause
amendment on `dots-0lqe`. Preserve all other acceptance text, original design,
prior notes, tracking anchor, title, status, assignee and other metadata. Subsequent
verified dated results remain append-only and require readback. Do not close,
export or sync the issue. Any permission denial or unknown mutation/readback
outcome stops work; do not switch tools, agents, paths or transports to bypass it.
