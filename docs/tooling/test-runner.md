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
positive integer `timeout_seconds` sets the step's POSIX time limit (default:
600 seconds). Commands are argv arrays, not shell strings; only `{python}` and
`{repo}` placeholders are supported. Steps are sorted by ID, duplicate IDs are
rejected, and `all` runs the resulting unique registry entries.

Every top-level `tests/test_*.py` module must appear in the union of `covers`.
The runner contract tests enforce that registration and specifically retain the
existing `agent-worktree-merge` suite.

Capabilities name one executable with `command`, or ordered alternatives with
`commands`, and may include a non-interactive probe. The first executable found
on the runner's inherited `PATH` runs the probe by absolute path with a
30-second timeout; a failed probe leaves the capability unavailable. The
runner's guard shims do not satisfy capability checks. Capability commands and
probes support the same `{python}` and `{repo}` placeholders as step argv
arrays. For example:

```json
{
  "capabilities": {
    "node": {"command": "node"},
    "pwsh": {"commands": ["pwsh", "pwsh.exe"]},
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

`tests/support/powershell.py` keeps static parsing separate from behavioral
execution. It resolves `pwsh` first and native `pwsh.exe` from WSL2 second. If
neither exists, parser-only checks may use the retained
`local/powershell-audit:lts` Docker or Podman image built from
`assets/Dockerfile.powershell-audit`. That image provides AST evidence only and
does not satisfy the `pwsh` or `pester` capabilities. Behavioral PowerShell
tests require a local/native runtime. The Pester probe accepts installed Pester
3.4 or newer, and the shared launcher selects the compatible Pester 3 or 5
invocation without installing or upgrading host modules.

## Isolation contract

Each invocation gets a temporary sandbox. Child processes receive synthetic
`HOME`, `USERPROFILE`, XDG, and temporary directories. Git global/system config
points at an empty sandbox file, interactive Git prompts are disabled, and
credential/service variables are not copied wholesale from the parent process.
The explicit `DEVCONTAINER_SMOKE` opt-in, artifact directory, and timeout are the
only live-smoke settings passed through. The command timeout defaults to 180
seconds and accepts values from 30 through 900. No similarly prefixed credential
or secret variables are allowed.

On POSIX, each step runs in its own process group. A timed-out step receives
`SIGTERM`, then `SIGKILL` if needed; output draining and parent cleanup are
bounded. The runner marks the step `FAIL` with a timeout reason and a null exit
code in the final report, then continues to the next step. The registered live
devcontainer step has a 1200-second step limit, distinct from the live-smoke
command timeout above. Descendants that leave the step's process group are not
covered by group signalling; undrained output is reported as a cleanup failure.
Native Windows step execution does not yet enforce this limit.

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
synthetic home, empty Git config, and `bd`/`dolt` guards. The sandbox directory
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
