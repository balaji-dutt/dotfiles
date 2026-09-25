<!-- markdownlint-disable MD013 MD040 MD041 -->

# ADR 0003: Git agent attestation trailers

## Status

Accepted — 2026-09-10. The normative v1 format is maintained in
[`docs/git-agent-attestation.md`](../git-agent-attestation.md).

## Context

Git records author and committer identity, not which AI systems materially
shaped a change. This repository's current one-shot AI `Co-authored-by`
configuration names a static model. It cannot represent dynamic model
selection, multiple agents and roles, or source-definition provenance.

The record needs to remain visible in ordinary Git history, survive common
hosting and transport paths, and degrade without blocking a commit when runtime
metadata is unavailable. Its claims must not imply proof of runtime execution.

## Options considered

- **Retain the static AI coauthor workflow.** Simple, but cannot describe the
  actual model and multi-agent participation of a session.
- **Keep provenance only in external telemetry or Git notes.** Allows richer
  records, but separates review evidence from the commit and reduces
  portability.
- **Use visible, structured Git trailers.** Fits existing Git tooling and keeps
  a compact record beside the change, while remaining explicitly self-asserted.
- **Require cryptographically signed runtime attestations immediately.** Could
  support stronger claims, but requires execution evidence, identity, and trust
  infrastructure that this repository does not have.

## Decision

Adopt the v1 `AI-Participant`, `Source-Definition`, and `Source-Digest` trailer
groups defined in the normative specification. Harnesses provide participant
data through a versioned JSON handoff, and commit wrappers validate and append
the records. Harness-specific producers remain thin.

The wrapper boundary is the common point across OpenCode, Claude Code, POSIX,
and PowerShell. The mechanism is fail-open and emits a tool-only record when
richer valid metadata is unavailable.

All records are self-asserted input provenance. They are not cryptographic
proof that a runtime executed, followed a definition, or produced correct
output.

## Consequences

- The attestation and AI-specific coauthor systems coexist during rollout.
- After OpenCode and Claude Code reach verified POSIX and PowerShell parity,
  later scoped work can retire the repository's AI-specific coauthor
  automation.
- GitHub attribution trailers for automated commits are a separate decision
  and are not retired with it; see [ADR 0004](0004-github-attribution-coauthor-trailers.md).
- Standard `Co-authored-by` remains available for human coauthors and external
  project conventions.
- The visible format is compact and parseable, but semantic consumers must
  enforce grouping and field rules beyond Git's trailer parser.
- Richer external records or signed attestations can be layered on later
  without claiming that v1 already supplies those guarantees.

## Revisit triggers

Revisit this decision if Git trailer ordering proves unreliable in supported
workflows, if a harness supplies verifiable execution attestations, or if the
handoff cannot preserve parity across supported commit wrappers.

## Sources

- [`docs/git-agent-attestation.md`](../git-agent-attestation.md)
- [`docs/git-ai-coauthor.md`](../git-ai-coauthor.md)
- [Git commit message trailers](https://git-scm.com/docs/git-interpret-trailers)
