<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Test Runner

`assets/run-tests.py` is the repository's test orchestrator. The POSIX and
PowerShell entrypoints delegate to it, so suite names, isolation, output, and
exit status stay consistent across hosts.

## Commands

Run the default `fast` suite from the repository root:

```sh
./assets/run-tests.sh
```

```powershell
pwsh -NoProfile -File ./assets/run-tests.ps1
```

Pass one suite name to either entrypoint:

```text
fast
integration
render
provenance
platform
all
```

`all` runs every registered step once in registry order. Use `--list` to
validate and print the selected steps without executing them:

```sh
./assets/run-tests.sh --list integration
```

```powershell
pwsh -NoProfile -File ./assets/run-tests.ps1 --list integration
```

Local runs report an unavailable tool as `SKIP`. A CI lane that promises every
declared tool for its current operating system adds `--require-capabilities`; an
applicable missing tool then becomes `FAIL`. A lane with narrower promises uses
repeatable `--require-capability NAME`, so other optional tools remain visible
skips. Unknown capability names are configuration errors. A step for another
operating system remains a platform skip.

The GitLab `linux-fast` job requires all declared Linux capabilities and requests
a deterministic suite-registration report:

```sh
./assets/run-tests.sh fast \
  --require-capabilities \
  --report-file ci-artifacts/linux-fast.json
```

The schema-versioned JSON contains the platform, selected suite, stable step
IDs, registered `covers` paths, status/reason/exit code for each step, totals,
and the overall exit code. It is written atomically after execution, including
test and capability failures. This describes registered path execution; it is
not source line or branch coverage.

The runner exits `0` when every executed step passes, including permitted local
skips; `1` for a test failure, timed-out step, or required missing capability;
and `2` for CLI or registry errors. Each selected step gets a `PASS`, `SKIP`,
or `FAIL` line and the run ends with deterministic pass/skip/fail totals. Skips
produced inside a test framework remain visible in that framework's output and
are not reclassified by the runner.

### Case-report pilot

`--case-report` is an opt-in pilot and requires `--report-file`. Without it the
report stays at `schema_version` 1, unchanged. With it, each step whose argv is
`{python} -m unittest` plus optional `-v` and selectors runs through
`assets/unittest-case-report.py` instead. That adapter calls `unittest.main`
with a recording result class, so verbosity and exit codes are unittest's own,
including `5` when no tests ran. It writes a per-step JSON sidecar in the
runner's sandbox. The step's exit code still decides `PASS`/`FAIL`; a missing or
unreadable sidecar is recorded but never changes a step's status.

The report becomes `schema_version` 2. It keeps every version 1 field and adds:

- `state`: `running`, `complete`, `interrupted` or `crashed`. The runner
  rewrites the report atomically after every step, so a run in progress shows
  `running` with its finished steps and a null `exit_code`. If the run is
  interrupted (`SIGINT`, `SIGTERM`, `SIGHUP`, Ctrl+C) or the runner itself
  raises, the last write marks the step in progress `interrupted`, the steps
  after it `not_run`, and records the exit code the runner is leaving with
  (128 plus the signal number, `130` for Ctrl+C, `1` for a runner crash). A
  runner killed outright (`SIGKILL`, power loss) cannot write that final
  report, so a file still at `running` means the run ended without a summary.
- `context`: Git `revision` and `dirty` (tracked files only), `platform`,
  Python `implementation` and `version`, the `suite`, and the
  `selected_steps` in run order.
- per step, `outcome`, one of `passed`, `failed`, `skipped_platform`,
  `skipped_capability`, `missing_capability`, `timeout`, `launch_error`,
  `crash`, `interrupted` or `not_run`. On Windows, `crash` means an exit code
  at or above `0xC0000000` (an NTSTATUS error such as an access violation).
  `interrupted` steps have status `fail`, and `not_run` steps have status
  `skip`.
- per step, `duration_seconds` and `termination` (`kind`, `exit_code`,
  `signal`, `cleanup_error`); `kind` is `exited`, `timeout`, `launch_error` or
  `signal`. Steps that never started (platform or capability skips and
  `missing_capability` failures) have no termination, duration or case report.
- per step, `case_report` with `status`: `complete`, `incomplete` (the adapter
  stopped before unittest finished), `missing` (no sidecar, for example after
  a crash), `invalid`, or `unsupported-argv` for steps that are not plain
  unittest selectors. A readable sidecar adds `cases`, outcome `totals`,
  `zero_tests` (no test ran) and `all_skipped` (every test skipped).

Each case has a stable `id` (`module.Class.method`), an `outcome` (`passed`,
`failed`, `error`, `skipped`, `expected_failure`, `unexpected_success`,
`fixture_error`, or `incomplete`), a skip `reason`, and `duration_seconds`.
Subtests are listed under their parent and do not count as cases. A parent
whose subtests fail is `failed`, or `error` if any subtest raised; a parent
whose subtests only passed or skipped is `passed`, or `skipped` if every
subtest skipped. A `setUpClass` or `setUpModule` error is its own
`fixture_error` case named the way unittest names it, and a class-level
`SkipTest` is a `skipped` case. Cases cover Python assertions only: a test that
drives Node or Pester appears as one case, and the assertions inside that
subprocess are not broken out.

The pilot's maintenance footprint is the adapter, the `--case-report` branches
in `assets/run-tests.py` (`case_report_command`, `read_case_report`,
`run_context` and the version 2 fields in `write_report`), and
`tests/test_case_report.py`. Making it the default is a separate decision.

## Suite boundary

- `fast`: deterministic inventory and drift policy, guard, runner, fixture, and
  hook tests.
- `integration`: isolated Git, worktree, subprocess, sync, and runtime tests.
- `render`: shell, profile, hook, and template rendering tests.
- `provenance`: inventory, config-contract, generated, mirror, and runtime drift
  tests.
- `platform`: native and shell-specific behavior for the current host.

The registry is `configs/test-suites.json`. Every step has a stable `id`, one or
more `suites`, an argv array, and one or more `covers` paths. Optional
`platforms` and `requires` fields declare when the step can run. An optional
positive integer `timeout_seconds` sets the step's time limit (default:
600 seconds). Commands are argv arrays, not shell strings; only `{python}` and
`{repo}` placeholders are supported. Steps are sorted by ID, duplicate IDs are
rejected, and `all` runs the resulting unique registry entries.

Every top-level `tests/test_*.py` module must appear in the union of `covers`.
The runner contract tests enforce that registration and specifically retain the
existing `agent-worktree-merge` suite.

`assets/check-test-selection.py` checks selection at class level, and the fast
`test-selection` step runs it. It reads test sources with `ast` and never
imports them, so no module-level runtime probe runs. Every step must be
`{python} -m unittest` followed by optional `-v` and `tests.test_<module>` or
`tests.test_<module>.<Class>` selectors, and its `covers` must equal the modules
its selectors name. A concrete class is a `unittest.TestCase` subclass with at
least one `test*` attribute, defined on the class or inherited from its bases,
including mixins from `tests/support/`. The check fails when:

- no step selects a concrete class;
- two steps select the same class on overlapping `platforms`;
- a selector names nothing, or only classes without tests;
- a module defines, assigns or imports `load_tests`, defines a class or
  function inside a module-level block, has a base the check cannot resolve,
  or imports a concrete class that unittest would collect a second time;
- a module has no concrete class.

Repetition across disjoint platforms is allowed. The check does not evaluate
`skipUnless` conditions, so a selected class can still skip at run time. Reviewed
exceptions live in `REVIEWED_EXCEPTIONS` in the script, each with a reason and
work item. An exception that no longer excuses anything is an error. The only
current exception is the manual-only macOS shell-startup smoke module. Run the
check locally with:

```sh
python3 assets/check-test-selection.py
```

Capabilities name one executable with `command`, or ordered alternatives with
`commands`, and may include a non-interactive probe. The first executable found
on the runner's inherited `PATH`, with the Node normalization described below,
runs the probe by absolute path with a 30-second timeout; a failed probe leaves the capability unavailable. The
runner's guard shims do not satisfy capability checks. Capability commands and
probes support the same `{python}` and `{repo}` placeholders as step argv
arrays. For example:

```json
{
  "capabilities": {
    "node": {"command": "node"},
    "pwsh": {
      "command": "{python}",
      "probe": [
        "{python}", "{repo}/tests/support/powershell.py", "probe-runtime", "--native",
        "--repo-root", "{repo}"
      ]
    },
    "pester": {
      "command": "{python}",
      "probe": ["{python}", "{repo}/tests/support/powershell.py", "probe-pester"]
    }
  },
  "steps": [
    {
      "id": "example-node-contract",
      "suites": ["fast"],
      "argv": ["node", "--test", "tests/node/example.test.mjs"],
      "covers": ["tests/node/example.test.mjs"],
      "requires": ["node"]
    },
    {
      "id": "example-pester-contract",
      "suites": ["platform"],
      "argv": [
        "{python}",
        "{repo}/tests/support/powershell.py",
        "run-pester",
        "{repo}/tests/powershell/Example.Tests.ps1"
      ],
      "covers": ["tests/powershell/Example.Tests.ps1"],
      "platforms": ["windows"],
      "requires": ["pester"]
    }
  ]
}
```

The excerpt illustrates the schema only; the example test path is not a
registered production test.

Tests look up PowerShell only through `tests/support/powershell.py`; a source
check in `tests/test_test_runner.py` rejects `which("pwsh")` and argv that start
with a bare `pwsh` anywhere else. `resolve_powershell_runtime` tries `pwsh` on
`PATH`, then on WSL2 `pwsh.exe` on `PATH` and
`C:\Program Files\PowerShell\7\pwsh.exe`. It probes each candidate with
`exit 0` and remembers successful probes for the rest of the process. Its
`interop` argument decides whether a WSL2 interop `pwsh.exe` qualifies:

- `allow` (default) suits callers that pass every path through
  `powershell_path`. That helper raises if `wslpath` cannot translate a path,
  rather than handing a POSIX path to `pwsh.exe`.
- `forbid` suits callers that pass POSIX paths or run `/bin/sh` fakes. A native
  Windows `pwsh.exe` still qualifies.
- `require` accepts only a WSL2 interop `pwsh.exe`.

`probe-runtime --native` and `probe-runtime --interop` expose the `forbid` and
`require` modes to capability probes. The `pwsh` capability uses `--native`, so
a WSL2 host with only `pwsh.exe` skips `pwsh` steps while native `pester`
capability probes may use interop. The `pwsh-interop` capability uses `--interop`. Only the
WSL2 `windows-shell-interop-smoke` step requires it, and that step is evidence
of Windows PowerShell reached through interop, not of a native Windows checkout.
A failed probe reports
`capability probe failed for pwsh (exit 1)`; run
`python3 tests/support/powershell.py probe-runtime --native` to print each
candidate's diagnostic.

Static parsing stays separate from behavioral execution. When no runtime
resolves, parser-only checks may use the retained
`local/powershell-audit:lts` Docker or Podman image built from
`assets/Dockerfile.powershell-audit`. Parser success provides AST evidence only;
neither retained image satisfies the native `pwsh` or `pester` capabilities.
The native Pester probe accepts installed Pester
3.4 or newer, and the shared launcher selects the compatible Pester 3 or 5
invocation without installing or upgrading host modules.
Every `Assert-MockCalled` under `tests/powershell/` passes `-Scope`, because
Pester 3 otherwise counts calls across the whole `Describe`; a source check
enforces this.

### Optional PowerShell fixture containers

Build retained images explicitly from the checkout root (replace `docker` with
`podman` when appropriate). Builds need network access; tests never build or pull:

```sh
docker build -f assets/Dockerfile.powershell-audit -t local/powershell-audit:lts .
docker build -f assets/Dockerfile.powershell-tests -t local/powershell-tests:lts .
```

The audit image supports the audit, Beads sync, and OpenCode hold fixtures.
The optional test image adds Git, Python 3, and AllUsers Pester **5.7.1** for
the Beads dispatch, installer, host-plugin refresh, OpenCode profile, and FFPWA
Pester fixtures. Both images carry `image_retention=retain`.

| Capability | Probe and meaning |
| :--- | :--- |
| `pwsh-container` | `probe-container`: execute the retained audit image, independently of native PowerShell |
| `pwsh-fixture` | `probe-fixture`: prefer native PowerShell, otherwise execute the retained test image |
| `pester-fixture` | `probe-pester-fixture`: native PowerShell with Pester >=3.4, otherwise the retained test image |

Run probes with `python3 tests/support/powershell.py <probe>`. Missing images or
unusable engines produce skips with build guidance; execution failures after
backend selection fail the test. `fast` requires none of these capabilities.
Fixture routes exclude WSL Windows interop and print their selected backend.

The shared launcher mounts only one staged fixture at `/fixture`, translates
declared fixture paths, forwards an explicit environment allowlist, disables
networking, and runs as the invoking UID/GID (rootless Podman uses keep-id).
Timed-out runs remove only their owned container; uncertain cleanup fails and
retains the fixture at the diagnostic path. Pester must discover and pass at
least one test, with no failed tests, blocks, or containers.

Container results are Linux PowerShell behavior, **not native Windows proof**.
Job Objects, reparse points, Windows process enumeration, and the native-only
Windows shell/lifecycle suites still need Windows. Audit-image Beads init
cases requiring Git still skip. Use `--case-report` to inspect these case-level
skips rather than treating a passing registration step as full coverage.

## Isolation contract

Each invocation gets a temporary sandbox. Child processes receive synthetic
`HOME`, `USERPROFILE`, temporary directories, and all four XDG roots
(`XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`). Git global/system config
points at an empty sandbox file, interactive Git prompts are disabled, and
credential/service variables are not copied wholesale from the parent process.
The explicit `DEVCONTAINER_SMOKE` opt-in, artifact directory, and timeout are the
only live-smoke settings passed through. The command timeout defaults to 180
seconds and accepts values from 30 through 900. No similarly prefixed credential
or secret variables are allowed.

On POSIX, when the selected `node` resolves to a mise executable, the runner
queries `mise which node` before replacing the parent environment. Discovery
uses the repository root as its working directory, closes stdin, and has a
10-second timeout. The installed executable is exposed through a Node-only
symlink in the sandbox, behind the `bd`/`dolt` guards and ahead of inherited
`PATH`; capability lookup uses this normalized path without the guards.
Children receive no parent mise variables or real mise config/state roots.
Other Node selections and native Windows runtime selection are unchanged;
`npm` and `npx` shims are not normalized. Failed discovery prints a bounded
warning and retains the inherited shim, so Node execution failures remain
visible rather than becoming capability skips or selecting another runtime.

On POSIX, each step runs in its own process group. A timed-out step receives
`SIGTERM`, then `SIGKILL` if needed; output draining and parent cleanup are
bounded. The runner marks the step `FAIL` with a timeout reason and a null exit
code in the final report, then continues to the next step. The registered live
devcontainer step has a 1200-second step limit, distinct from the live-smoke
command timeout above. Descendants that leave the step's process group are not
covered by group signalling; undrained output is reported as a cleanup failure.

On Windows, each step starts in a new process group and gets the same
`timeout_seconds` limit. On timeout or interruption the runner runs
`%SYSTEMROOT%\System32\taskkill.exe /T /F /PID <step pid>` by absolute path,
which ends the step and every descendant still linked to it by parent process
ID. It never kills by process name. A descendant whose parent already exited is
no longer linked, so `taskkill` cannot reach it. The runner then reports
`step output did not drain` as a cleanup failure and moves on without closing
the pipes, because closing them would wait for that descendant. Output read
before the stall is still printed. If `taskkill` is missing, fails or does not
finish within 10 seconds, the runner stops only the step process and records
that as a cleanup failure. The `runner-windows-interop` step runs this path for
real under Windows Python on a WSL2 host. It requires the
`windows-python-interop` capability, which probes `py.exe -3`. A native Windows
checkout is not yet verified.

The runner reads each step's stdout and stderr as they arrive and keeps only the
last 1 MiB of each, so a step that floods its output cannot exhaust the
runner's memory. When it drops output, the printed stream starts with a line
saying how many bytes it omitted.

Version 1 reports are still written once, after the last step. An interrupted
run without `--case-report` leaves no report, because a partial version 1
file would look like a finished run that selected fewer steps.

The runner prepends fail-closed `bd` and `dolt` commands. An accidental call
therefore cannot reach this checkout's real Beads database. Tests that need
those commands normally prepend their own fake-bin fixture, which takes
precedence over the guards. The POSIX `beads-isolated-worktree` integration
step is the exception: after a successful `bd --version` probe, the runner
passes the resolved real executable as `DOTFILES_TEST_BD` to that step only.
The step uses a temporary repository and still runs with the guarded `PATH`;
if `bd` is missing or its probe fails, the step is skipped (or fails when the
capability is required). Tests must still avoid live networks, GUI automation,
installers, secrets, and destructive host operations by default.
The registered live devcontainer step is the exception only when
`DEVCONTAINER_SMOKE=1`; its capability probe otherwise leaves the step as a
runner-level skip.

## Direct runs

Run a module directly from the repository root as
`python3 -m unittest tests.test_name` (`py -3 -m unittest ...` on native
Windows). Importing the `tests` package applies the runner's own
`isolated_environment` to that process: the same environment allowlist,
synthetic home/XDG roots, Node normalization, empty Git config, and `bd`/`dolt`
guards. Nested fixtures inherit the resolved Node path. The sandbox directory
is removed at exit. Inside the runner, `DOTFILES_TEST_SANDBOX=1` is already set
and the package leaves the environment alone. Running a test file as a script
(`python3 tests/test_name.py`) is not supported.

## Shared fixtures

Test modules import reusable helpers from `tests/support/`; support modules and
test modules never import a test module. `tests.test_test_fixtures` checks that
boundary from source without importing the suites.

| Module | Use it for |
| --- | --- |
| `fixtures.py` | `isolated_environment`, `write_executable` (`dedent=True` for indented script text), `write_fake_command` with JSON-lines call logs, `write_python_command`, `load_script_module` for scripts without a `.py` suffix, `restricted_lifecycle_env`, Git repository setup, and JSON I/O |
| `chezmoi_render.py` | `render_template` and the cached `render_matrix` against synthetic per-platform data |
| `git_worktree.py` | `GitFixture`: a disposable main/feature worktree pair with a bare remote and fake `bd` |
| `gitlab_ci.py` | `CI_PATH` and `top_level_block` for `.gitlab-ci.yml` assertions |
| `powershell.py` | PowerShell runtime resolution, path translation, parsing, and Pester |

A new test should follow this recipe:

1. Start from `isolated_environment()` (or `GitFixture`) instead of the real
   home, global Git identity, or Beads state.
2. Render templates with `render_template`, adjusting synthetic data through
   its `configure` callback.
3. Write fakes with `write_fake_command` or `write_executable`, put them first
   on `PATH`, and assert on their call logs, including that forbidden commands
   were never called.
4. Assert a reachable failure: make a fake fail and check the exit code,
   message, and that nothing was written outside the fixture.

Keep scenario-specific fixtures in the test module. When a helper is needed
by a second module, move it into `tests/support/`; if it stays local, say
why in the review.

## Adding a step

1. Add the test using standard-library or already established framework tools.
2. Add one sorted registry step with stable suites, argv, `covers`, and only the
   capabilities it actually requires.
3. Run `./assets/run-tests.sh --list all` to validate registration.
4. Run the affected suite locally, then require only the capabilities each CI
   lane explicitly claims. Use `--require-capabilities` only for a lane that
   promises every applicable step requirement.
5. If production automation or nested test support changed, update the
   automation inventory, apply the risk policy, and review its candidate list.

The current GitLab lanes, artifacts, rules, and platform gaps are documented in
`docs/tooling/continuous-integration.md`. Provenance authorities and accepted
divergences are documented in `docs/inventory/automation-provenance.md`.
Risk tiers and new-script evidence requirements are documented in
`docs/tooling/automation-coverage-policy.md`.
