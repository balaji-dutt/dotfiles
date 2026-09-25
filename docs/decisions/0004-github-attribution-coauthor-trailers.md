<!-- markdownlint-disable MD013 MD040 MD041 -->

# ADR 0004: GitHub attribution co-author trailers for automated commits

## Status

Accepted — 2026-09-25. The operative policy, identity matrix, and rollout are
maintained in
[`docs/automation/git-identity-policy.md`](../automation/git-identity-policy.md).

## Context

This repository is canonical on GitLab and push-mirrored to GitHub. OpenCode
commits use `OpenCode <noreply@opencode.ai>`, and Renovate commits use a GitLab
service-account `noreply` address. GitHub links neither to an account, so
those commits show no avatar and no contributor.

[ADR 0003](0003-git-agent-attestation.md) made `AI-Participant` trailers the
provenance record for AI participation. It also plans to retire the
repository's static, one-shot AI `Co-authored-by` hook once both harnesses
reach wrapper parity. Adding a new automated `Co-authored-by` trailer runs
against that plan unless the difference is recorded.

The owner introduced `oc-commit` and `cc-commit` so that the harness that
produced and committed a change is its author. That rules out replacing
harness authorship with human authorship to improve GitHub results.

## Options considered

- **Keep current identities and accept that GitHub shows no account.**
  Accurate and free, but the mirrored history shows no attribution for most
  commits.
- **Change raw author and committer to a GitHub-recognized bot address.**
  Resolves on GitHub, but claims that an external GitHub App made commits it
  never made. For Renovate it also forces a `gitIgnoredAuthors` migration and
  leaves CI amendments with a mixed committer.
- **Create an owner-controlled automation account.** Resolves accurately, but
  adds an account to maintain.
- **Keep raw identities and add a `Co-authored-by` trailer naming a
  GitHub-recognized bot.** Raw metadata stays accurate, and GitHub gets an
  attribution field it recognizes.

## Decision

Keep the existing author and committer identities for OpenCode and Renovate.
Add one repository-controlled GitHub attribution trailer to each:

```text
Co-authored-by: opencode-agent[bot] <opencode-agent[bot]@users.noreply.github.com>
Co-authored-by: renovate[bot] <29139614+renovate[bot]@users.noreply.github.com>
```

OpenCode's commit tooling writes the OpenCode trailer, and Renovate's
`commitBody` writes the Renovate trailer.

These trailers are for GitHub attribution only. They are not provenance
records and not the static AI coauthor hook that ADR 0003 retires.
`AI-Participant` remains authoritative for AI participation.

The owner accepted that the `renovate[bot]` trailer credits the GitHub App
that Mend hosts, which takes no part in this self-hosted run. For OpenCode,
the address matches what OpenCode's own GitHub integration configures, and an
upstream contributor recommended it for agent co-author attribution.

## Consequences

- ADR 0003's retirement of AI-specific coauthor automation does not cover
  these trailers.
- Raw author and committer, the GitLab pusher, and service-account MR
  attribution stay as they are, and so does Renovate's branch-modified
  detection.
- GitHub's top-level commit author stays unresolved, and the co-author
  surfaces separately. Merge commits that GitLab creates on automerge, and all
  existing history, carry no trailer.
- A future Renovate package rule that sets `commitBody` must carry the
  trailer.
- An owner-controlled automation account remains the fallback for OpenCode.

## Revisit triggers

Revisit this decision if:

- GitHub stops resolving either address, or `dots-a0jr.4` shows it never
  resolved;
- OpenCode or Renovate publishes a different recommended attribution
  identity;
- this repository adopts an owner-controlled automation account;
- GitLab push rules or signature requirements start constraining trailers.

## Sources

- [`docs/automation/git-identity-policy.md`](../automation/git-identity-policy.md)
- [ADR 0003](0003-git-agent-attestation.md)
- [GitHub: creating a commit with multiple authors](https://docs.github.com/en/pull-requests/committing-changes-to-your-project/creating-and-editing-commits/creating-a-commit-with-multiple-authors)
- [OpenCode #43358](https://github.com/anomalyco/opencode/issues/43358), including the
  [recommendation](https://github.com/anomalyco/opencode/issues/43358#issuecomment-5346508601)
  and [reported result](https://github.com/anomalyco/opencode/issues/43358#issuecomment-5346664581)
- [OpenCode #29845](https://github.com/anomalyco/opencode/issues/29845)
- [`github.handler.ts` at `16c56fe5`](https://github.com/anomalyco/opencode/blob/16c56fe5ecc3305028d1f0a9cff5806e51c9d480/packages/opencode/src/cli/cmd/github.handler.ts#L143)
- [Renovate `commitBody` at 43.288.0](https://github.com/renovatebot/renovate/blob/43.288.0/lib/workers/repository/update/branch/index.ts#L728-L731)
