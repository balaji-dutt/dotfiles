<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Automation Test Inventory

`configs/automation-test-inventory.json` is the canonical ownership and test
mapping for automation tracked by this repository. The manifest classifies
every discovered candidate as owned, mirrored, generated, vendored/upstream,
archived, or excluded.

The inventory is a planning boundary, not a claim that every owned path already
has full coverage. Each owned entry names its suite, durable work item or
component, current coverage status, required test layers, supported platforms,
risk, and side effects.

## Candidate discovery

`assets/check-automation-test-inventory.py` reads the Git index with
`git ls-files --stage`. It does not walk untracked or ignored directories.
Candidate signals include:

- executable Git mode and chezmoi `executable_` names;
- script, PowerShell, batch, JavaScript, TypeScript, and Python extensions,
  including extensions before `.tmpl`;
- chezmoi lifecycle scripts and known shell startup files;
- shebangs near the start of non-prose files, including templated scripts;
- `.envrc` and GitLab CI configuration; and
- command-bearing devcontainer JSON and Ansible YAML in their bounded paths.

The checker deliberately does not treat every YAML, JSON, or template as
automation. Add a narrow discovery signal when a new executable surface cannot
be represented by the existing rules.

Manifest path selectors are root-relative. `*` stays within one path segment;
`**` can cross directories. Selectors expand only over discovered candidates.
Every candidate must match exactly one entry, and every selector must match at
least one candidate.

The top-level `candidate_digest` snapshots the sorted path and discovery-reason
pairs. This means a new file still causes drift when an existing glob would
otherwise classify it automatically. Review the candidate list before updating
the digest.

## Classification and ownership

Each path has one primary classification:

- `owned`: behavioral coverage belongs to this repository;
- `mirrored`: another tracked path is canonical and this copy is synchronized;
- `generated`: a generator or canonical source owns the output;
- `vendored-upstream`: upstream owns the implementation;
- `archived`: retained history that is not active automation;
- `excluded`: a discovery false positive or test harness rather than a
  production automation target.

When provenance categories overlap, use this primary precedence: container
mirror target, generated output, vendored/upstream copy, archive, then deliberate
exclusion. This inventory records the ownership decision; the separate
provenance contract compares the source relationships.

The devcontainer runtime is currently repository-owned. `dots-7mx` tracks a
future same-path submodule split, so the inventory must not claim that move has
already happened.

## Manifest fields

Every entry records:

- `id`, `classification`, and one or more sorted `paths` selectors;
- the canonical `owner` kind, name, and source when applicable;
- `languages`, `platforms`, `risk`, and observable `side_effects`;
- required `test_layers`; and
- `coverage` with a suite ID, status, durable work item or component, and tracked
  test paths.

Owned entries require a suite ID and durable ownership reference. Long-lived
components such as `test-foundation` are preferred when ownership should outlive
the Beads story that introduced the automation. `covered` and `partial` entries
require at least one tracked test path. Non-owned entries require a provenance or
exclusion rationale. The checker also validates stable enums, ordering, duplicate
IDs and paths, stale selectors, and references to missing tests.

`planned`, `partial`, and `covered` describe the current implementation state.
They do not set the final risk-based rejection policy; that policy belongs to
`dots-4jy.10.1.5`.

## Run the drift check

From the repository root on Linux, macOS, or WSL2:

```sh
python3 assets/check-automation-test-inventory.py
python3 -m unittest tests.test_automation_test_inventory
```

On native Windows:

```powershell
py -3 assets/check-automation-test-inventory.py
py -3 -m unittest tests.test_automation_test_inventory
```

To inspect the discovered census and its replacement digest without reading the
manifest:

```sh
python3 assets/check-automation-test-inventory.py --list-candidates
```

## Update workflow

When adding, removing, renaming, generating, or mirroring automation:

1. Run `--list-candidates` and review every changed path and reason.
2. Add or update one manifest entry with the correct primary classification.
3. For owned automation, assign its suite and durable work item or component and
   record any existing tests. Do not mark planned coverage as covered.
4. Update `candidate_digest` to the reviewed value printed by the checker.
5. Run the checker and its fixture-driven unit tests.

Do not weaken discovery merely to make an unexpected candidate disappear. Do
not add duplicate behavioral suites for mirrors or generated files. Canonical
commands and shared fixtures are documented in `docs/tooling/test-runner.md`.
Portable CI and its main-push guard are documented in
`docs/tooling/continuous-integration.md`. Generated, mirrored, and vendored
relationships are documented in `docs/inventory/automation-provenance.md`.
