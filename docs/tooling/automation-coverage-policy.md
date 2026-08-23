<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Automation Coverage Policy

Automation coverage is based on risk and observable behavior, not one repository
percentage. A high line count in low-risk helpers cannot prove that a destructive
sync command fails safely, and a shell/template branch is often better exercised
through a subprocess or render contract than a language coverage tool.

`configs/automation-test-inventory.json` is the machine-readable policy. Its
checker runs in both the `fast` and `provenance` suites. The candidate census and
exact-once classification catch additions, removals, and renames; coverage fields
record what is implemented and what remains assigned to a durable work item.

## Owned automation

| Risk | Minimum evidence |
| :--- | :--- |
| `critical` | Integration layer plus an explicit success, failure, and safety matrix. Each covered branch names registered tests. |
| `high` | Contract or integration layer for meaningful behavior and failure handling. |
| `medium` | At least one deterministic contract, unit, render, platform-smoke, or integration layer. |
| `low` | A focused static, render, contract, unit, or integration check when behavior exists; data or harness false positives belong in a justified exclusion. |

Automation whose sole declared platform is native Windows, WSL2, or a
devcontainer also requires the `platform-smoke` layer. A missing local runner
remains explicit: keep the branch planned and its platform suite visible rather
than reporting an unexecuted path as covered.

### Critical behavior matrix

Each critical entry has sorted `coverage.behavior_requirements`. Every item has a
stable ID, kind (`success`, `failure`, or `safety`), concrete description, status,
and test evidence.

- `covered` requirements name tracked tests registered in a behavioral canonical
  suite (`fast`, `integration`, `render`, or `platform`).
- `planned` requirements have no test paths and remain assigned through the
  entry's durable work item and rationale.
- The entry test list is the union of covered requirement evidence.
- All covered requirements make the entry `covered`; all planned make it
  `planned`; a mixture makes it `partial`.

The matrix is a family-level minimum, not permission to ignore a newly added
command. Candidate-digest review and the scoped reviewer checklist require each
addition or behavior change to revisit the relevant entry. Existing truthful
planned and partial gaps do not block unrelated changes or require downstream
testing epics to land first.

## Non-owned automation

- Generated, mirrored, vendored-upstream, and archived paths use covered
  provenance evidence against their canonical authority. They do not claim an
  independent behavior matrix.
- Exclusions are repository-owned discovery false positives or test support.
  They require a concrete rationale, low risk, no side effects, no test layers,
  and `not-applicable` coverage.
- Static, audit, and provenance checks describe their own contracts. They are
  not substitutes for owned behavioral evidence.

Authorities and accepted generated/vendor exceptions are documented in
`docs/inventory/automation-provenance.md`.

## New-script workflow

For an added, removed, renamed, or behaviorally changed script, hook, plugin, or
command-bearing template:

1. Run `python3 assets/check-automation-test-inventory.py --list-candidates` and
   review changed paths and discovery reasons.
2. Update exactly one inventory entry with ownership, risk, side effects,
   platforms, and required layers. Do not weaken discovery to hide a candidate.
3. Register deterministic tests in `configs/test-suites.json`, or record an
   explicit planned/partial gap under a durable work item and rationale.
4. For critical automation, update all affected success, failure, and safety
   requirements. Claim only evidence that the named tests exercise.
5. Update `candidate_digest` only to the reviewed value printed by the checker.
6. Run `fast`, the relevant integration/render/platform suite, and the required
   repository audit.

The dotfiles reviewer applies the same rules only to automation visible in its
scoped diff. The automatic checker is the authoritative full-repository gate.

## Canonical commands

```sh
./assets/run-tests.sh fast
./assets/run-tests.sh integration
./assets/run-tests.sh platform
python3 assets/check-automation-test-inventory.py
```

```powershell
pwsh -NoProfile -File ./assets/run-tests.ps1 fast
pwsh -NoProfile -File ./assets/run-tests.ps1 integration
pwsh -NoProfile -File ./assets/run-tests.ps1 platform
py -3 assets/check-automation-test-inventory.py
```

Use `all` before a broad or high-risk merge. Missing optional platform tools are
reported as skips unless a CI lane explicitly requires that capability.

## Optional language metrics

Targeted metrics may be useful for a substantial implementation: Python branch
coverage for named modules, Node coverage for JavaScript logic, or Pester
coverage on a Windows lane. Introduce one only when it clarifies a domain risk
and can be maintained on the platform that owns it. It supplements the behavior
matrix; it does not create a universal threshold or a new default dependency.
