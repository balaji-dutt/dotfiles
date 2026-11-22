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

## DV2-020: inactive subset and consumer validation

This section extends the frozen DV2-010 observation above. The approved DV2-020
plan and subsequent readback-verified results are dated notes on `dots-0lqe`;
they do not rewrite the earlier observation, original design or acceptance text.
The preservation baseline for this phase is
`9100cf156e76e4dec337de3d8e0d5d9df7fc7821`, not a silent refresh of the
inventory's historical `333b882a` consumer observation.

The phase contains fourteen inactive payload files: two specialist bases and
twelve files forming four complete skill bundles. Three additional immutable
evidence files contain the pinned owner manifest and the two canonical YAML
sources. Evidence is not installed source authority, a complete upstream archive,
or a claim of runnable agent-engineer dependency closure. The withheld
`plugin-developer` and `prompt-evaluator` bundles remain upstream dependencies.

The consumer descriptor at `configs/opencode-v2/source/preparation.jsonc` records
the exact import and ordered, unapplied overlay inputs. The separate subset
manifest at `configs/opencode-v2/prepared/metadata/subset-manifest.json` records
canonical source digests, emitted output digests, immutable evidence and blocked
operational rows. The actual producing build remains unknown. No aggregate,
registration, activation, native model alias, permission translation or provider
sampling decision is supplied by this phase.

### Offline checker and proof boundaries

```sh
python3 assets/check-opencode-v2-preparation.py --root .
```

The checker and core gate require POSIX checkout semantics. Non-POSIX checker
execution is refused before root, Git or artifact access. Native Windows checker
execution is unsupported; the separate render class retains its four-platform
synthetic coverage without claiming native Windows filesystem verification.

The checker derives its authority from pinned public Git objects and the frozen
inventory/contract before accepting mutable prepared metadata. It checks exact
payload/evidence closure, independent source/output hashes, ordered inputs and
inactive status. A changed output cannot self-certify by changing its mutable
subset-manifest digest. No owner checkout, credentials, network, Beads query,
user-state interpolation or installed configuration scan is required.

Historical preservation compares completed preparation commit
`a417636d7376dd426a811935bc2f7203472bd6ef` with pre-phase baseline
`9100cf156e76e4dec337de3d8e0d5d9df7fc7821`. Protected path sets, modes and bytes must
match, subject only to the approved additive registry and ledger changes. Both
historical views are selected from public metadata before blob contents are
requested. Missing history fails without fetching or substituting HEAD.

This historical audit does not freeze the current active configuration to old
bytes. Ongoing checks validate current prepared artifacts and the bounded loading
declarations below. The descriptor's overlay hashes and preservation-tree digest
remain evidence of the `9100cf15` inputs; they are not rebased onto current main
or presented as qualification of current helper behavior.

Root and component links, dangling links, cycles, traversal and Windows aliases
are rejected before prepared content is read. Instrumented open/hash tests cover
forbidden target reads; separate fixture tree, index and outside-sentinel checks
cover non-modification. These checks are not an atomic filesystem sandbox against
concurrent hostile mutation.

The explicit physical-preservation audit model retains legacy link payloads
without opening or hashing their targets. Its fixtures reject link-type changes,
retargeting and mode drift. The recorded CRLF model requires exact committed and
working digests, only the permitted line-ending transformation, and actual
fixture Git attributes declaring `text` and `eol=crlf`. These physical audit
fixtures are separate from the default current-loading check.

The tests model consumer-owned composition and ownership contracts using
synthetic inputs and historical source hashes. They do not execute or qualify
current profile/lifecycle helpers or the native permission engine. Reference
rebasing, profile order, workspace-agent-only merging, exact
fallback routes, native `claude-agent` preservation, default-only `Plan-Trial`,
Windows failure routes and explicit launcher approval remain separate from
operational readiness.

The render class uses synthetic Linux, macOS, Windows and WSL2 data and real
chezmoi `ignored` and `managed` queries in owned temporary source, destination,
config, state and cache directories. Chezmoi `!` exclusions override matching
ignore rules regardless of order. Placement of the repository's `configs/**`
rule after the Windows block is a structural invariant, not last-match semantic
proof. Negative fixtures expose prepared paths with overlapping unignore rules
in either order. Synthetic Windows rendering is not native Windows runtime
verification. No apply, init, script execution or external download is involved.
Ignore-exposure negatives fail this companion render gate, not the CLI's loading
declaration check. CLI PASS alone establishes neither chezmoi exclusion nor
runtime qualification.

### Bounded current-loading declarations

`C` below denotes
`private_Documents/development/container-dotfiles/dotfiles`. Global source roots
are `private_dot_config/opencode` and `C/private_dot_config/opencode`.

| Public source input | Presence and scope |
|---|---|
| `.opencode/opencode.jsonc` and both global roots' `opencode.jsonc` | Mandatory; absence fails. |
| Root `opencode.json`/`opencode.jsonc`, corresponding project/global JSON alternatives | Checked when present; both formats are inspected, without selecting a winner. |
| Global `profiles/*/opencode.json` and `opencode.jsonc` | One profile-directory level; absent optional inputs are inert. |
| Known root and discovery aliases | Public Git links or `symlink_*` records only; link payloads, not target contents. |
| `configs/devcontainer-sync.jsonc` | Only `shared.mirrors` declarations; unrelated source contents are not inspected. |

Present configuration inputs must be public and Git-visible before content is
read. Relevant source templates, unsupported value types and unresolved local
expressions fail as unverified rather than disappearing from the check.

| Field | Relative base and interpretation |
|---|---|
| Project/root `instructions[]` | Repository root. |
| Global/profile `instructions[]` | Known absolute or file-URL mappings only; relative forms are unsupported and fail as unverified. |
| `skills.paths[]` | Declaring configuration directory; directory registrations cover descendants. |
| Local `plugin[]` strings or tuple element zero | Declaring configuration directory. Remote HTTP/Git/NPM identities remain opaque, including version interpolation; no environment lookup. |
| `agent.*` or `agents.*` string `prompt`/`system` | Every literal `{file:...}` marker uses the declaring configuration directory; ordinary inline text stays opaque. |

These source-side resolution rules are not a complete upstream loader model.
Unrelated model, provider, permission and Claude settings values are opaque.
Unsupported future declarations require separate review, not permission-policy
changes or silent assumptions.

Project `.opencode` maps to `$REPO/.opencode`; host OpenCode and `dot_claude`
sources map to distinct host `$HOME/.config/opencode` and `$HOME/.claude`
namespaces. Container counterparts use a separate container-home namespace.
Known repository absolute paths and home prefixes map back to public source
paths without reading an installed home or expanding user environment values.
Project references to `~/.config/opencode` use the host namespace. The inactive
`configs/opencode-v2` area has no deployment mapping. Unknown external contents
are not opened or qualified; unresolved potential preparation references fail.

Home-prefixed declarations retain their selected namespace even in project
configuration. Relative host/container and Claude declarations are normalized
within their containing global source root; leaving that namespace fails before
target inspection. Equality or a slash-delimited descendant is required, so a
similarly named sibling is not contained. The global root is a containment
boundary, not a resolution base: a profile's `../shared/plugin.js` resolves from
its declaring directory to `profiles/shared/plugin.js`, while
`../../prompts/x.md` resolves inside the global `prompts` directory. Contained
parent segments, project-relative paths and explicit public repository absolute
paths/file URLs remain supported. Relative global/profile instructions remain
unsupported.

Local normalization handles dot segments, case-insensitive potential overlap and
one file-URL decoding pass. Drive/UNC forms, escapes and residual encoded
separator/dot forms are unsupported. Local glob checking deliberately uses a
conservative `fnmatchcase` model in which `*` spans slashes, matching inactive
files and their directory ancestors. Negated or unsupported expressions fail as
unverified; false positives require review, not automatic widening.

Alias scope is limited to public project/host/container OpenCode and Claude
agent(s), skill(s), command(s), plugin(s) and prompt roots, plus AGENTS/CLAUDE
root aliases. Only fixed source-prefix mappings and `symlink_` basename removal
are modeled. Repo-local chains permit at most 16 hops with a visited set;
cycles, dangling/external/escaping chains and hop exhaustion fail without target
content reads. Preparation paths and directory ancestors cannot be registered
through aliases.

Mirror destinations are checked independently before source overlap: a target
equal to, inside or ancestral to the inactive area is rejected even for an
unrelated source. Source selection then uses the existing POSIX case-sensitive
include-any and exclude-any `fnmatch` rules; exclusions are never case-folded.
Selected inactive files cannot project into active discovery. Unsupported roots,
schemas or ambiguous aliases fail as unverified. This bounded declaration check
does not analyze arbitrary plugin code or generators.

### DV2-020 behavioral requirement matrix

Core methods below belong to `OpenCodeV2PreparationTests` in
`tests/test_opencode_v2_preparation.py`; render methods belong to
`OpenCodeV2IsolationRenderTests` in the same module. These requirements describe
implemented coverage, not a claim that the full phase verification has passed.

| Requirement ID | Kind | Description | Existing test methods |
|---|---|---|---|
| `success-independent-subset-authority` | success | Verify the fourteen-file subset, complete bundles, canonical source proofs, ordered inputs and unknown producer build against frozen independent authority. | Core: `test_default_cli_accepts_the_real_inactive_subset_without_owner_checkout`, `test_selected_closure_has_independent_expected_counts_and_source_proof`, `test_provenance_order_unknown_build_and_readiness_are_strict` |
| `success-offline-composition-contract` | success | Exercise synthetic profile ordering, workspace-agent scope, reference rebasing, provider transforms, Windows failure routes and managed ownership without executing live helpers. | Core: `test_profile_composition_order_workspace_scope_rebase_and_replacements`, `test_default_alias_provider_transforms_native_claude_and_windows_failure_contract`, `test_raw_host_and_fallback_mirror_lifecycle_ownership_and_isolation` |
| `failure-output-and-manifest-tamper` | failure | Reject edited, missing, extra or incomplete artifacts even when a mutable subset digest is changed to match the edited output. | Core: `test_output_and_mutable_manifest_cannot_self_certify_tampering`, `test_missing_extra_stale_and_incomplete_bundle_are_rejected`, `test_canonical_evidence_and_frozen_authority_cannot_be_replaced` |
| `failure-readiness-and-provenance-forgery` | failure | Reject forged readiness, activation, aggregate, producer-build, typed-value and ordered-input claims. | Core: `test_provenance_order_unknown_build_and_readiness_are_strict`, `test_descriptor_cannot_enable_registration_or_hide_an_applied_overlay`, `test_strict_jsonc_duplicates_nonfinite_and_root_types` |
| `failure-protected-source-drift` | failure | Reject protected public source or path-set drift, existing registration changes and a missing pinned Git baseline without fetching or falling back to HEAD. | Core: `test_protected_bytes_path_sets_modes_and_registry_history`, `test_protected_executable_mode_drift_is_rejected`, `test_recorded_crlf_variance_requires_exact_bytes_and_real_attributes`, `test_legacy_link_payload_is_preserved_without_reading_its_targets`, `test_missing_trusted_git_baseline_fails_without_fetch_or_fallback` |
| `safety-no-forbidden-link-read-or-write` | safety | Reject roots, ancestors, dangling links, cycles, traversal and Windows aliases before forbidden file-open or hash boundaries; separately preserve fixture trees, index and outside sentinels. | Core: `test_symlink_targets_are_not_read_or_hashed_and_checker_does_not_write`, `test_legacy_link_payload_is_preserved_without_reading_its_targets`, `test_root_ancestor_dangling_cyclic_links_and_case_aliases_are_rejected`, `test_windows_path_alias_and_escape_matrix` |
| `safety-real-chezmoi-target-exclusion` | safety | Render four synthetic platforms and query real chezmoi ignored and managed target paths; detect exposing unignore rules in either order without apply, init, scripts or user-state access. | Render: `test_real_rendered_matrix_keeps_evidence_payload_and_container_mirror_unmanaged`, `test_actual_unignore_exposes_payload_in_either_order`, `test_omitting_config_exclusions_exposes_prepared_files_on_posix` |
| `safety-native-route-and-policy-boundaries` | safety | Preserve native claude-agent routes, default-only Plan-Trial and explicit launcher approval boundaries without claiming native runtime or permission-engine qualification. | Core: `test_non_posix_checker_refuses_before_root_or_content_access`, `test_default_alias_provider_transforms_native_claude_and_windows_failure_contract`, `test_workspace_preference_plan_trial_and_explicit_launcher_auto` |

Integration mappings extend those same requirement IDs:

- `success-independent-subset-authority` and `failure-protected-source-drift`:
  `test_completed_history_is_independent_of_benign_current_changes`.
- `safety-no-forbidden-link-read-or-write`:
  `test_current_inputs_presence_url_escapes_and_external_boundaries`,
  `test_alias_payloads_have_bounded_chains_and_never_read_targets`, and both
  `test_public_projection_filters_before_blob_reads_and_preserves_main_delta`
  and `test_public_projection_conflicts_and_unapproved_delta_fail_closed`.
- `safety-native-route-and-policy-boundaries`:
  `test_loader_fields_resolve_independently_and_reject_preparation_exposure` and
  `test_mirror_destinations_are_guarded_before_source_overlap`.
- `safety-no-forbidden-link-read-or-write` and
  `safety-native-route-and-policy-boundaries`:
  `test_mapped_loader_namespaces_reject_escapes_before_target_inspection` and
  `test_mapped_loader_namespaces_preserve_declaring_bases_and_public_mappings`.
- `success-independent-subset-authority`, `failure-protected-source-drift` and
  `safety-no-forbidden-link-read-or-write`:
  `test_public_projection_keeps_one_capture_when_shared_refs_advance` and
  `test_public_projection_rejects_own_index_files_and_feature_drift`.

### Public integration projection

The manual test-support rehearsal captures the committed main SHA once, immediately
before selecting its immutable histories, with an expected feature HEAD. Feature
validation does not require main to match an earlier observation or wait for main
to stop moving. Main-worktree cleanliness remains a landing-helper condition;
uncommitted main changes are not included. Its allowlist includes inactive
inputs/artifacts, the four amendment paths, declared loader/mirror/alias inputs,
`.chezmoiignore`, required public Python/render support, and the public drift
controls `docs/devcontainers.md` and `dot_claude/settings-base.json`.

Public-path exclusion happens before any blob request. Only allowed blobs enter
an owned temporary Git object store, with no alternates, clone, hooks, user Git
configuration or network access. Synthetic filtered base/main/feature commits
are merged there; the real full trees are never passed to `merge-tree`.
Excluded object contents are unavailable to that merge. Projection source queries
are non-writing. Independent fixtures prove whole-source/index/ref nonmodification
in controlled runs and use a separate owned fixture actor to advance shared refs.
The rehearsal rejects changes to its source index, four files or feature HEAD;
main and unrelated branch advancement are reported, not treated as failures.

The amendment delta relative to completed preparation `a417636d` is applied with
three-way index application to the projected combined versions. Whole-file
overlays are not used. Conflicts stop; fixtures verify main-only content survives
and unrelated delta paths are rejected. The rehearsal records the resulting
four amended-file hashes and checks other projected content is preserved.

The candidate checker loads pinned public historical authority from the source
repository and uses its existing trusted-context API against the projection.
The candidate's three real render tests run against the projected ignore file.
Acceptance requires both API and render PASS. This is a public projection, not
a full checkout, candidate default-CLI proof, full merged CI or native runtime
qualification. Ordinary fast tests use synthetic histories, never moving main;
the actual-main rehearsal is an explicit one-off support invocation.

Results identify captured main, observed main afterward, feature, projected tree,
four amended-file hashes and observed shared-ref movement. Capture and observation
are non-atomic: they do not establish exclusive access, absence of transient
changes or an A-to-B-to-A transition. Main movement does not trigger recapture,
restart or another campaign. A successful result applies only to the captured
snapshot, not to later commits.

Before landing, compare that snapshot with the canonical helper's fresh main
observation and disclose any gap. Feature CI and the helper's no-ff merge are not
combined-tree validation of later main. A later authorization must choose a
bounded targeted refresh or informed acceptance of the untested delta; this
amendment grants neither, and never waives an actual failed check.

### DV2-020 verification and delivery gate

The POSIX-only core class is registered in `fast` and `provenance`; the
real-render class is registered in `fast` and `render`. Their success, failure
and safety requirements are mapped above and referenced by the high-risk owned
automation inventory entry, independently of the legacy provenance checker. Its
structured requirement array is empty under the noncritical-entry policy.
Existing legacy manifests, accepted divergences, active v1 and Claude sources,
helpers, policies, mirrors, launchers and attestation remain
unchanged. Byte-identical owner prompt emissions retain their pinned evaluation
evidence; a new projection needs separate owner regeneration and independent
scored evaluation.

Required local checks, per-path peer-suppressed audits, doctor and complete
scoped review precede the phase-specific peer cycle. The approved macOS procedure
uses one fresh probe and a bounded 7200-second cache, `--no-agent-repair` on every
invocation, unchanged cache/config fingerprints and sufficient remaining TTL
before each audit/test batch. An expired or replaced cache stops work rather
than authorizing a second probe. Real failures, unknown outcomes, permission
denials and incomplete cleanup block progress; the DV2-010 metadata-only
unavailable-peer exception does not apply here.

The approved integration-completion package permits the initial namespace
correction and at most two related local follow-on rounds within its four-file
scope. Local failures stay blocking until a corrected candidate passes; they do
not require a separate approval for every in-scope correction. Each changed
candidate needs its affected checks and source review, with at most three
complete local campaigns and three public-rehearsal invocations in total. A
diagnosed rehearsal-caller correction consumes the next round and an invocation;
successful rehearsals are not repeated merely to select a later main. Permission
denials stop all normal work, and scope changes, source-state drift, genuine
integration conflicts, timeouts/unknown cleanup, peer failures and cache-guard
refusals are hard stops rather than local-loop retries.

Actual validation, review, docs assessment and peer outcomes are recorded in
dated, readback-verified notes on the same Bead. A missing successful result is
not PASS. The four integration-amendment files are tracked; no intent-to-add
entries are needed. Ordinary staging, a commit and feature CI publication each
require separate approval. Landing uses the canonical CI-gated helper rather
than the public-projection fixture. This phase does not
authorize activation, DV2-030, plugin adoption, attestation restoration, homelab
deployment, Safety Net qualification, binary changes or cutover.
