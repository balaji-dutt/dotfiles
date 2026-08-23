<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Automation Provenance

`configs/automation-provenance.json` records only provenance facts that cannot
be derived from existing repository authorities. The standard-library checker
at `assets/check-automation-provenance.py` derives the rest from tracked files
and fails when a source, generated output, mirror, or vendored copy drifts.

## Authorities and checks

- `.agentic-tooling/generated-manifest.json` owns generated output membership,
  source digests, and rendered digests. Two Claude agent renders have exact
  local digest and marker exceptions for their maintained frontmatter; changed
  or unnecessary exceptions fail.
- `configs/devcontainer-sync.jsonc` owns shared container mirror mappings. The
  checker applies its include/exclude rules to Git-tracked paths and compares
  file bytes, executable modes, symlink targets, and cleanup-managed stale
  targets.
- `configs/promptfoo-runtime/package.json` owns Promptfoo dependency versions.
  The npm lockfile and devcontainer package list must carry those derived pins;
  the provenance policy does not duplicate the versions.
- The Espanso policy records the canonical source, shared include template, and
  platform wrapper chain. Each wrapper must delegate to the expected authority.
- The two statusline copies must remain byte- and version-identical, and the
  tracked sync command must remain executable. Live upstream comparison stays
  in `assets/sync-statusline.sh --check` and its targeted GitLab sync job.
- The OpenCode unslop backend is the local canonical tree. Claude and container
  copies must match its membership and bytes, required CLI entrypoints must
  exist, and an exact tree digest records the reviewed upstream-derived
  snapshot. `docs/unslop-fork-status.md` remains the historical source record.
- `archive/` and `.opencode/node_modules/` must not enter behavioral suite
  commands or coverage paths. Archive automation remains classified as
  archived, and tracked node_modules content is rejected.

The mirror and unslop checks intentionally derive membership from the Git index,
not an unrestricted filesystem walk. Ignored caches and untracked files are
outside committed provenance; tracked stale targets are not. Git attributes pin
the provenance-checked AI trees to LF so byte checks remain stable on Windows.

## Run the checks

From Linux, macOS, or WSL2:

```sh
python3 assets/check-automation-provenance.py
./assets/run-tests.sh provenance
```

From native Windows:

```powershell
py -3 assets/check-automation-provenance.py
pwsh -NoProfile -File ./assets/run-tests.ps1 provenance
```

Default checks are offline. The statusline contract test supplies a fake local
upstream to exercise the existing `--check` command without contacting GitHub.

## Update workflow

1. Change the canonical source or existing authority first.
2. Run the repository's existing generator or sync command when one exists.
3. Run the provenance checker and inspect every reported relationship.
4. Add policy data only when the fact cannot be derived. Accepted generated
   divergence requires an exact digest, identifying markers, and a rationale.
5. If an intentional unslop snapshot changes, review the complete canonical
   tree and update its tree digest together with the historical provenance note
   when the upstream basis also changed.
6. Run the provenance suite and the automation inventory checker.

Do not update a digest merely to silence an unexplained difference. Behavioral
tests belong to canonical owned sources; mirrors and generated copies receive
narrow provenance checks rather than duplicate test suites.
