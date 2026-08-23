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

CI can request a deterministic suite-registration report:

```sh
./assets/run-tests.sh fast \
  --require-capability git \
  --report-file ci-artifacts/linux-fast.json
```

The schema-versioned JSON contains the platform, selected suite, stable step
IDs, registered `covers` paths, status/reason/exit code for each step, totals,
and the overall exit code. It is written atomically after execution, including
test and capability failures. This describes registered path execution; it is
not source line or branch coverage.

The runner exits `0` when every executed step passes, including permitted local
skips; `1` for a test failure or a required missing capability; and `2` for CLI
or registry errors. Each selected step gets a `PASS`, `SKIP`, or `FAIL` line and
the run ends with deterministic pass/skip/fail totals. Skips produced inside a
test framework remain visible in that framework's output and are not reclassified
by the runner.

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
`platforms` and `requires` fields declare when the step can run. Commands are
argv arrays, not shell strings; only `{python}` and `{repo}` placeholders are
supported. Steps are sorted by ID, duplicate IDs are rejected, and `all` runs
the resulting unique registry entries.

Every top-level `tests/test_*.py` module must appear in the union of `covers`.
The runner contract tests enforce that registration and specifically retain the
existing `agent-worktree-merge` suite.

Capabilities name an executable and may include a non-interactive probe. This
also supports future Node and Pester steps without adding framework-specific
runner branches. For example:

```json
{
  "capabilities": {
    "node": {"command": "node"},
    "pester": {
      "command": "pwsh",
      "probe": [
        "pwsh",
        "-NoProfile",
        "-Command",
        "if (Get-Module -ListAvailable Pester) { exit 0 } else { exit 1 }"
      ]
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
        "pwsh",
        "-NoProfile",
        "-Command",
        "Invoke-Pester tests/powershell/Example.Tests.ps1 -CI"
      ],
      "covers": ["tests/powershell/Example.Tests.ps1"],
      "platforms": ["windows"],
      "requires": ["pester"]
    }
  ]
}
```

The excerpt illustrates the schema only; those example paths are not registered
production tests.

## Isolation contract

Each invocation gets a temporary sandbox. Child processes receive synthetic
`HOME`, `USERPROFILE`, XDG, and temporary directories. Git global/system config
points at an empty sandbox file, interactive Git prompts are disabled, and
credential/service variables are not copied wholesale from the parent process.

The runner prepends fail-closed `bd` and `dolt` commands. An accidental call
therefore cannot reach this checkout's real Beads database. A test that needs
those commands must prepend its own fake-bin fixture, which takes precedence
over the guards. Tests must still avoid live networks, GUI automation,
installers, secrets, and destructive host operations by default.

## Shared fixtures

`tests/support/fixtures.py` contains standard-library primitives for:

- temporary homes and child environments;
- executable and fake-PATH commands with JSON-lines call logs;
- isolated Git repositories with a local test identity;
- deterministic JSON and JSON-lines I/O; and
- TCP listeners bound to `127.0.0.1` on ephemeral ports.

Use these primitives instead of a real home, global Git identity, production
command, fixed port, or Beads state. Domain-specific setup should remain in the
test module rather than growing the shared file into a second test framework.

## Adding a step

1. Add the test using standard-library or already established framework tools.
2. Add one sorted registry step with stable suites, argv, `covers`, and only the
   capabilities it actually requires.
3. Run `./assets/run-tests.sh --list all` to validate registration.
4. Run the affected suite locally, then require only the capabilities each CI
   lane explicitly claims. Use `--require-capabilities` only for a lane that
   promises every applicable step requirement.
5. If production automation or nested test support changed, update the
   automation inventory, apply the risk policy, and review its candidate digest.

The current GitLab lanes, artifacts, rules, and platform gaps are documented in
`docs/tooling/continuous-integration.md`. Provenance authorities and accepted
divergences are documented in `docs/inventory/automation-provenance.md`.
Risk tiers and new-script evidence requirements are documented in
`docs/tooling/automation-coverage-policy.md`.
