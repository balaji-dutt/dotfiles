# Testing infrastructure assessment

**Status:** Assessed — 2026-09-29. Point-in-time findings, not an accepted decision.
**Worktree baseline:** `ee3aa3c8d004129ce9c670f94e773c5c330ef2cd` on
`feat/assess-testing-plan`. Counts below refer to this checkout unless attributed
to the independent review of local `main` at `7d531e77`.

## Verdict and scope

The tests exercise real behavior, but maintaining the testing system is becoming
expensive. The primary debt is not the use of `unittest`: it is repeated policy
bookkeeping, scattered fixture infrastructure, and a runner that cannot reliably
say which cases executed. Keep the behavioral tests and unittest compatibility;
prune declarations that do not earn their upkeep, consolidate genuinely shared
helpers, and repair execution evidence. Add development dependencies only when
they displace more maintenance than they introduce. Native-platform coverage is
also important, but expanding CI is a separate investment decision.

This assessment inspects source, history, CI configuration, existing tests, and
six bounded local selectors; it does not certify the entire suite. The sample
ran on macOS 26.7 with Python 3.14.7, Git 2.54.0 (Apple Git-157), Bash 5.3.20,
Zsh 5.9, chezmoi 2.72.2, and Node 24.21.0; `pwsh` was unavailable. No full fast
suite, live smoke, container build, installation, or remote CI run was attempted.
The external review's timings and counts are identified separately below. Neither
source registration nor a green selected run measures total behavioral coverage.

### What already works

- Temporary Git histories, rendered shell startup, whole-script PowerShell
  execution, and synthetic chezmoi rendering test real interfaces. The shell
  ordering check compares the full helper sequence; Windows boundary fixtures
  deny external commands, check a canary, and assert token non-disclosure
  (`tests/test_shell_startup.py`, `tests/test_windows_lifecycle_boundaries.py`).
- The canonical runner gives subprocesses a synthetic home and config, an
  environment allowlist, empty Git config, and guarded Beads/Dolt executables
  (`assets/run-tests.py:24–45,261–310`). Focused negative tests verify error
  diagnostics and retained state, rather than merely checking a nonzero exit.
- Automation discovery, risk declarations, partial/planned states, and dated
  exceptions make ownership visible. Provenance records canonical generators,
  mirrors, accepted divergences, and integrity snapshots that test discovery
  cannot infer (`configs/automation-provenance.json`). These are useful policy
  and integrity evidence, **not** measurements of executed assertions.

The baseline registry has 75 steps and 13 capability definitions, with overlapping
memberships: fast 28, integration 44, platform 43, render 22, provenance 8.
The inventory has 35 entries (28 owned: 20 declared covered and eight partial)
and 112 behavior requirements. Counts are declarations/commands, not case counts
or coverage percentages. The runner, inventory checker, and provenance checker
total 2,102 physical lines; their three named test modules total another 2,138.
Size signals maintenance exposure, not that repository-specific safety policy is
unnecessary. The independent review examined `7d531e77` and reported 70 Python
test modules plus inventory changes in 79 of 294 non-merge commits since
2026-08-22. That review's corpus/churn counts were not reproduced against this
worktree and are investigation signals, not coverage or defect rates.

## Debt area 1: policy upkeep and evidence authority

`configs/automation-candidates.txt` is a reviewed path/reason census;
`configs/automation-test-inventory.json` describes ownership, risk, intended
behavior, and registered test files; `configs/test-suites.json` chooses commands;
the runner reports step exit codes. None can by itself establish that an assertion
exists, ran, or passed on every advertised platform. The checker
(`assets/check-automation-test-inventory.py:341–770`) does enforce useful
relationships: exact-once candidate classification, tracked test paths, registry
membership, status consistency, and success/failure/safety requirements for
critical entries. The costs arise where a second declaration has no distinct
consumer, or a consistency check is mistaken for behavioral evidence.

| Inventory data | Present consumer / limit | Proposed treatment |
| --- | --- | --- |
| Candidate `paths`, classification, owner kind, risk, platforms, side effects, rationale | Git-index discovery and checker enforce classification and selected policy rules (`:365–542,720–770`); human review owns risk and rationale | Preserve owned discovery and human decisions; reduce representational churn, not visibility |
| `test_layers`, status, entry and requirement `test_paths`, requirement kinds | Checker tests registration, tracked paths, status/requirement consistency (`:565–713`). Entry paths equal the union of covered requirement paths (`:681–684`) | Keep critical intent reviewable; any mapping reduction must replace or explicitly relinquish these cross-checks |
| `work_item` | Nonempty-string check (`:558`); policy allows an issue ID **or** a durable component name (`docs/inventory/automation-testing.md:87–89`) | Separate historical implementation provenance from who owns an **open** gap; never require live Beads in offline CI |
| `suite_id` | Required nonempty for owned entries (`:557`), but no comparison with registry step IDs | Prefer retirement through schema/checker/test migration unless a distinct consumer is demonstrated; adding a check solely to preserve the field is not simplification |
| `review_due` on WSL waivers | Date gate at `:543`; the March 28, 2027 deadline starts failing on **March 29** | Keep expiry; agree who reviews it and update both inventory dates and policy text together if renewed |

Point-in-time Beads inspection on September 29 found closed references
`dots-qt9`, `dots-4jy.10.5.2`, `dots-4jy.10.5.4`, `dots-4jy.10.2.4`,
and `dots-4jy.10.3`, and open follow-ups `dots-4jy.10.9` and
`dots-4jy.10.15`. Closed implementation references can
remain useful history but cannot alone assign outstanding work. This sample does
not show that *every* partial gap lacks an owner; assess each gap explicitly.
The `work_item` component-name alternative must remain available. A Beads status
lookup at validation time would make offline and native Windows runs depend on
a reachable Dolt server, weakening a previously offline check.

Candidate discovery recognizes `.py` and includes `tests/` files; the reviewed
list adds a line for a new test even when an existing excluded glob classifies
it (`assets/check-automation-test-inventory.py:71–88`,
`docs/inventory/automation-testing.md:44–54`). The external review reported 88
test-support lines among 421 candidates on its newer snapshot; those numbers
were not re-counted here. Commit `ba446e58` correctly replaced an opaque global
digest of path/reason pairs with mergeable reviewed lines. The digest was never
test coverage. Line-level bookkeeping for clearly excluded support files can
still grow without a new human ownership decision. Compare a narrowly justified
discovery boundary **against** a reviewed category-level representation; neither
may conceal newly owned automation under `tests/`. Avoid inventing a second
pattern-policy language simply to save lines. The inventory test module also
enforces a platform-match rule (`tests/test_automation_test_inventory.py:715–738`)
that is not surfaced by the inventory checker or its documentation; decide whether
that is a genuine policy, then put it at the authoritative boundary.

The original draft suggested dropping per-requirement paths without accounting
for the entry-path union invariant. A shorter behavior register could record
reviewed critical success, failure, and safety intent while deriving routine
case references at collection time; it must either replace that cross-check with
a specific sampling/traceability procedure or explicitly accept less evidence.
Retaining only safety kinds would discard critical failure-mode intent. Full
JSON Schema validation could reduce duplicate *shape* checks, but only when an
identified check is actually removed; it does not replace Git, risk, provenance,
or platform semantics. Runtime consumers may still need dependency-free guards.

### Proposed metadata admission rule (future policy, not implemented)

Before adding a checked-in field, name its automated consumer **or** the specific
human decision it informs, authoritative source, update owner, and why existing
data cannot answer it. Record this reasoning in the change review, not another
metadata registry. Derive execution facts from discovery and results, rather than
copying them into the inventory. Put the durable rule in the existing automation
policy and link it from the script-addition checklist; do not duplicate it in
agent prompts. For retired fields, update the active schema and checker, define
historical-schema compatibility deliberately, and add a focused regression that
rejects the obsolete key. No static rule can prove that a human-facing field is
useful; periodic human review remains necessary. A representative new entry
should require one defensible source of each fact, and retired keys should fail
with actionable diagnostics.

## Debt area 2: fixture ownership and portability

`GitFixture` lives in `tests/test_agent_wt_merge.py:66` but is imported by
other test modules; `render_template` in
`tests/test_chezmoi_lifecycle_render.py:155` is another cross-suite helper.
Executable writers, script-as-module loaders, fake `bd` guards, and PowerShell
adapters recur in several suites. These are candidates for shared
`tests/support/` code, not proof every superficially similar fixture should be
merged. In particular, `tests/support/github_fixtures.py` depends on GitFixture
attributes, so a move needs consumer changes and isolation tests, not a file
rename. Remove helper candidates such as `append_json_line` and
`loopback_listener` only after verifying the intended use: observed references
were limited to `tests/test_test_fixtures.py` in this checkout, not proof of
universal dead code.

Isolation differs by entrypoint. `tests/support/fixtures.py:48–82` inherits most
of `os.environ` while removing Beads/Dolt/Git prefixes; the canonical runner
starts from an allowlist. Direct `unittest` invocation can therefore see host
inputs that a runner invocation cannot. This does **not** describe every test:
`tests/test_git_template_hook_sync.py:33–42` explicitly overrides global Git
config. An empty Git config and command shims are not OS/network sandboxes;
absolute executables and direct calls require separate boundaries.

The `514b078a` first-parent diff fixes a Plannotator injected-failure target by
resolving the fixture path, matching production's canonicalized destination
(`assets/sync-plannotator-assets.py:93–155`). On aliases, the old comparison
could miss the injected write; the test already checked the specific error,
unchanged manifest, and destination bytes, so a missed injection would fail
rather than demonstrate a false pass. It could work on already-canonical paths;
historical execution was not replayed. The same merge contains distinct WSL
applicability, optional-command fixture, and production-quoting changes. Native
Windows corrections in `89893c6e` likewise mix fixture and implementation
semantics. These incidents support explicit path/launch/fault contracts, not a
blanket conclusion that the suite was falsely green.

Windows tests that replace spacing-sensitive source fragments or mock command
discovery signatures can break on harmless formatting or miss new call shapes
(`tests/test_windows_lifecycle_boundaries.py`). Prefer configured inputs and
whole-script behavior where possible; use Pester mocks selectively while
preserving process-level checks. Consolidation must preserve canonical paths,
spaces, CRLF/LF, executable/launcher semantics, WSL path conversion, native
PowerShell versus parser fallback, fault reachability, and forbidden side effects.
`tests/support/powershell.py` can build a retained parser image during actual
parsing (300-second build budget); `probe-parser` does **not** provision. Move
preparation out of test execution, with explicit missing-tool diagnostics; a
parser fallback cannot certify Windows behavior.

### Proposed fixture placement rule (future policy, not implemented)

Test modules consume reusable helpers from `tests/support/`; support modules
must not depend on test modules. Keep scenario-specific fixtures local, and do
not mandate a support import in every test. Add a short authoring recipe to the
existing runner guide, linked from the script-addition checklist: canonical Git
and environment setup, template rendering, executable writing, PowerShell
runtime/translation, and a reachable fault assertion. Derive helper names from
the actual implementation rather than maintaining a new manifest. At review,
ask whether new shared infrastructure reuses or narrowly extends a support
helper; if kept local, document the semantic or isolation reason.

After migrating shared helpers, a **source-only** import-boundary regression in
the existing infrastructure tests should reject support-to-test imports and
new helper imports between test modules. Check ordinary absolute/relative
imports without importing or executing suites. Review intentional behavioral
inheritance separately, rather than growing a blanket allowlist. This enforces
placement, not semantic deduplication: keep behavioral tests of helper isolation
and avoid repository-wide clone detection or a custom fixture framework. A new
representative test should follow the recipe without copying generic setup;
prohibited edges should fail while justified local fixtures remain possible.

## Debt area 3: runner evidence, selection, and cost

`assets/run-tests.py:362–478` marks each step by subprocess exit status. Its JSON
records step ID, suites, covered files, status/reason, and exit code, but not
per-case identities, skip counts, durations, or revision. Framework skips remain
in console text; a zero-executed/all-skipped selection can pass. Capability
requirements do not cover every test's runtime prerequisite. The registry maps
**files** to steps (`tests/test_test_runner.py:493–511`), so adding a TestCase to
an already registered module can leave it unselected by a class-specific step
without failing inventory validation. Compare intended discovered case IDs with
selected IDs by lane, with reviewed exceptions for applicability and intentional
exclusions. Discovery must handle import-time effects safely; inherited tests
and subtests need explicit interpretation. Do not create a second hand-entered
class inventory.

One real overlap is `beads-helpers-posix` selecting the whole module and
`beads-helpers-powershell` selecting its PowerShell class again on a capable
POSIX host. By contrast, the attestation steps' platforms are disjoint.
`ReviewGateLinkedHomeTests` intentionally inherits repository tests to exercise
a linked Git worktree (`tests/test_claude_review_gate.py:1327`); measure fixture
seeding cost before sharing mutable repositories or dropping that topology.
The reviewer's **separate**, unreproduced macOS fast run took about 146 seconds
(28 executed steps, one skipped, no failures); reported slow steps included
Claude review gate (~34s), GitLab pipeline guard (~25s), Claude token check
(~17s), inventory (~12s), and remind-Beads hook (~9s). This is not the six-selector
sample below, a hosted CI measurement, or evidence that cases may safely be cut.

The runner uses one sandbox per invocation, buffers output, has no per-step
`subprocess.run` timeout, and writes its atomic report only after all steps
finish (`assets/run-tests.py:373–478`). An unhandled launch error or hung child
can leave no final structured report; shared state permits order dependence,
though none was observed in the sample. Add bounded steps, owned process-tree
cleanup on each OS, partial/incomplete-run reporting, and per-step homes unless
sharing is intentional. Test the failure paths before considering parallelism.

Platform routing also has concrete mismatches: WSL-gated
`WindowsShellSmokeTests` in `tests/test_windows_shell.py:87` is selected through
Windows-only registry steps, making that WSL smoke unreachable **via the
registry**. This differs from the intentionally manual macOS smoke requiring a
dedicated unprivileged account and `DOTFILES_MACOS_SMOKE=1`
(`docs/tooling/automation-coverage-policy.md:118–142`), and from documented
manual Windows attestation staging (`docs/git-agent-attestation.md:350–357`).
Fix the WSL route only with its intended safety prerequisites; do not pass live
smoke opt-ins through the runner by default. Native-only skips elsewhere are
correct, but required lanes must say what actually executed.

Two test modules, `tests/test_codebase_memory_mcp_pin.py` (fast) and
`tests/test_promptfoo_runtime.py` (provenance), import `tomllib` without a guard.
Launcher checks for a Python 3 executable are not a test-runtime minimum check;
declare and preflight Python >=3.11 for these suites rather than inferring a
repository-wide production requirement or editing production resolvers.

The independent review used local `main` at `7d531e77`; this worktree at
`ee3aa3c8` is **21 commits behind, zero ahead**, not diverged. That later commit
adds `repo-ops`, a test module, and fast/integration registration on macOS/WSL2.
It is absent here; even on main its platform selection excludes the automatic
Linux fast job. Source/registry presence alone proves no successful run. No
merge or rebase is necessary to revise this historical-baseline document.

## Simplification candidates and recurrence safeguards

These are hypotheses for subsequent implementation, not approved deletions.

| Candidate change | Maintenance removed | Safeguard retained / trade-off | Recurrence prevention | Acceptance evidence |
| --- | --- | --- | --- | --- |
| Retire `suite_id` after consumer audit | Repeated unused identifier | Migrate schema, checker, tests; preserve test-path/registry checks | Field admission rule and retired-key rejection | Old key fails; registry evidence still checked |
| Reduce per-file updates for excluded test support | Candidate-list churn | Preserve owned discovery and reviewed exceptions; compare boundary with category representation | Explicit owner and review of any discovery change | New owned automation still flagged; ordinary support edits need fewer touches |
| Shorten routine requirement-to-test mappings | Duplicated prose/paths | Preserve critical success/failure/safety review; replace or knowingly relinquish union/path checks | Named evidence consumer and update owner | Sample critical obligations trace to executed assertions; schema/checker parity |
| Move cross-suite helpers into `tests/support/` | Cross-test imports and equivalent local primitives | Preserve isolation, process and platform semantics | Authoring recipe, review question, narrow import-edge test | New suite reuses helper; invalid edge fails; direct/runner behavior tested |
| Remove verified unused support and duplicate selections | Extra paths or repeated execution | Do not erase linked-worktree or other intentional topology | Periodic selected-ID review and explicit removal check | Same intended cases once per lane; topology regressions still caught |
| Simplify suite names only if real consumers benefit | Unused selection modes, if demonstrated | Keep useful local selectors; overlapping memberships are legitimate | Review actual CLI/CI users before edits | Fewer maintained branches without losing requested selection |

Sequence: (1) prune redundant policy surface and clarify gap ownership, paired
with the admission/retirement rule; (2) consolidate fixtures and isolation,
paired with recipe, review, and import-boundary checks; (3) improve case results,
selection completeness, and failure containment; (4) profile avoidable fast
cost while retaining meaningful topology; (5) adopt a dependency where a pilot
shows lower ongoing maintenance. Early measurement or an obviously beneficial
maintained tool need not wait for all earlier stages. Do not set an arbitrary
repository-wide coverage target. Track places edited for a new test, custom
code removed/added, expected versus unexpected skips, incomplete reports, and
median/p95 lane duration over comparable runs.

## Reporting and tooling decision

All reporting pilots must cover IDs, outcomes/skips, durations, setup/teardown
errors, subtests, expected failures and unexpected successes, required zero-test
and all-skipped selections, and explicit incomplete runs. Keep subprocess crash,
timeout, and cleanup in the outer runner. Python case reporting does not expose
each individual Node/Pester assertion run within a Python subprocess test.

| Option | Work displaced | Costs / decision test |
| --- | --- | --- |
| Small stdlib `unittest.TestResult` JSON adapter | Case and skip data without changing selectors or installing a test framework | Own correct lifecycle/subtest serialization, duration and partial-run behavior. Pilot narrowly; JSON does **not** provide JUnit XML for free |
| Maintained `unittest-xml-reporting` | JUnit for existing unittest tests with minimal migration | Verify maintained Python/subtest compatibility and whether JUnit plus a small summarizer meets the contract; prefer it if bespoke reporting grows substantial |
| pytest over existing `TestCase` | Collection, JUnit, skip diagnostics and a path to future fixtures | TestCase methods cannot directly receive fixture arguments/parametrization; `load_tests` unsupported. Check setup/teardown, subtests, imports, skips, exit codes and isolation before switching; no wholesale rewrite |
| `jsonschema` in development/CI | Published structural schema validation | Additive until named handwritten shape checks are removed; format checks require explicit configuration. Keep offline semantic/runtime checks |
| coverage.py for selected Python subprocesses | Diagnostics about unexecuted Python branches | Optional, not a behavior score; configure subprocess capture/combine with the cleaned environment. No shell/PowerShell coverage |

A dependency-free bootstrap does not require dependency-free developer tests.
Pilot pinned dependencies in an isolated environment with an offline wheel/cache
plan and explicit required-CI setup failures; do not silently install tools
inside test cases. Plain venv/pip suffices to start; add a task/environment
manager only if it replaces a measured burden. Keep the original selectors for
rollback while comparing, but do not indefinitely maintain two equivalent
reporters. Property/mutation tooling and wholesale Pester/Bats migration have
no established debt-reduction case yet. Pester already exists: improve specific
weak boolean diagnostics or mocks without discarding whole-script tests.

## Decisions requested from the independent reviewer

1. Which inventory declarations genuinely change a human decision? What
   traceability would be lost by shortening per-requirement test-path mappings?
2. Should excluded test support use a narrower discovery boundary or a reviewed
   category representation? Show an owned-script counterexample before choosing.
3. Which helpers share behavior, rather than just names? Which direct-invocation
   safety assumptions should become explicit helper contracts?
4. Does the stdlib reporting adapter remain smaller than a maintained reporter
   after the full result contract and incomplete-run behavior are tested?
5. Which intended native cases should gate a change, and who owns runner safety,
   WSL isolation, and dated exceptions? Which provider can support that budget?

Keep this as a public point-in-time assessment; `docs/decisions/` is appropriate for an
ADR **after** an architecture is accepted, not while these choices are open.
No Beads epic was opened for this assessment.

## Evidence appendix: local runs and historical incidents

Six safety-reviewed selectors used a fresh synthetic runner environment per
group, external logs, and a 180-second owned POSIX process-group budget. All
exited zero: **131 reported unittest cases, one expected missing-PowerShell
skip, 130 not skipped**, no failures/errors. Times include wrapper overhead and
are not benchmarks or the reviewer's fast-suite run.

| `python3 -B -m unittest -v` selector | Reported / skipped | Wall seconds |
| --- | ---: | ---: |
| `tests.test_test_runner` | 35 / 1 | 3.01 |
| `tests.test_automation_test_inventory` | 38 / 0 | 12.37 |
| `tests.test_automation_provenance.AutomationProvenanceTests` | 27 / 0 | 6.22 |
| `tests.test_plannotator_sync` | 6 / 0 | 0.18 |
| `tests.test_shell_startup` | 20 / 0 | 3.95 |
| `tests.test_chezmoi_lifecycle_render.LifecycleRenderMatrixTests` + `LifecyclePosixSyntaxTests` | 5 / 0 | 1.23 |

The supervisor imported `assets/run-tests.py` for `isolated_environment`, used
`TemporaryDirectory` per group, removed live-devcontainer opt-ins, redirected
output outside the checkout, and cleaned each process group before deleting
its sandbox. The provenance selector excluded the statusline class; rendering
excluded the PowerShell parser class. This is not an ordinary bare inherited-env
unittest run. No native Windows or full-suite result follows from it.

The candidate SHA incident is discussed above: `git show ba446e58` shows the
digest-to-list migration; candidate hashes were review bookkeeping, whereas
provenance hashes verify identity. `git diff 514b078a^1 514b078a --
tests/test_plannotator_sync.py tests/test_bootstrap_wsl.py
tests/test_shell_startup.py` shows the path canonicalization and distinct
applicability/fixture changes; the current Plannotator six-test selector passed.
`89893c6e` concerns separate Windows launch and test-fixture contracts.

Inspect `assets/run-tests.py`, `configs/test-suites.json`,
`assets/check-automation-test-inventory.py`,
`configs/automation-test-inventory.json`,
`assets/check-automation-provenance.py`,
`docs/tooling/test-runner.md`, `docs/tooling/automation-coverage-policy.md`,
and `docs/inventory/automation-provenance.md` for authority boundaries. The
existing CI guide's “22 registered fast steps” (`docs/tooling/continuous-integration.md:276`)
is stale against this checkout's 28; do not use that planning text as a run count.

### Appendix: platform and CI investment (separate from debt reduction)

`.gitlab-ci.yml:163–308` automatically runs Linux fast for eligible feature
pushes/MRs and a separate OpenCode policy job, excluding Renovate/default-branch
pushes and duplicate branch/MR runs. Linux-all, Windows-all, and devcontainer
smoke are manual `allow_failure` jobs. Windows-all requires Git/PowerShell but
not every optional tool. No native macOS or WSL2 job appears in this YAML.
Synthetic Windows rendering on macOS and PowerShell parsing on Linux are useful,
but neither proves Windows process, registry, path, or ACL behavior. A native
lane should require intended capabilities, record actual cases/skips, start with
a small stable contract subset, and leave live mutation smoke opt-in.

The CI guide gives a provisional **75-second hosted whole-job target** and
320/400 monthly compute-minute forecast; neither is an observed time or a
verified entitlement here, and the reviewer's ~146-second local run is not
directly comparable. Measure `frequency × billed duration × runner factor`,
including setup, retries, Renovate, and native jobs, before promotion. Compare
the present GitLab Free arrangement, hosted native offerings subject to account
eligibility, and GitHub's public-repository standard-runner terms. A GitHub pilot
could offer Windows/macOS execution, but mirror/ref/exact-SHA authority, artifact
retention, and a single landing gate require explicit design; the existing
provider-aware helper does not make this repo's GitLab CI a dual-provider gate.
WSL2 still needs a disposable interop environment on either provider. Dedicated
runners add patching, cleanup, credential isolation, and capacity costs; do not
run untrusted public branches on a credential-bearing workstation. CI-provider
migration is not a prerequisite to fixing fixture or policy debt.

External capability references (consulted September 28, not installed or
certified against this suite): [pytest/unittest compatibility](https://docs.pytest.org/en/stable/how-to/unittest.html),
[unittest-xml-reporting](https://unittest-xml-reporting.readthedocs.io/en/latest/),
[coverage.py subprocess support](https://coverage.readthedocs.io/en/latest/subprocess.html),
[jsonschema validation](https://python-jsonschema.readthedocs.io/en/stable/validate/),
[GitLab hosted runners](https://docs.gitlab.com/ci/runners/hosted_runners/),
and [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
