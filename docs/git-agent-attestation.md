# Git Agent Attestation

This document defines version 1 of this repository's Git trailer convention
for recording AI participation. It also defines the transient JSON handoff used
by harness producers and commit wrappers.

The convention records **self-asserted input provenance**. It helps reviewers
identify which tools, agents, models, and primary definitions materially shaped
a change. It is not evidence that those inputs were used faithfully or that the
result is correct.

## Participant model

An AI participant is an agent or model whose output materially informed the
committed content or its acceptance. Incidental tool calls are not participants.

The participant fields are:

- `tool`: the harness that supplied the participant. The preferred slugs are
  `opencode` and `claude-code`.
- `agent`: the configured agent identity, when known.
- `role`: the participant's contribution role. This is an open lowercase
  vocabulary; examples include `planner`, `editor`, `reviewer`, and `verifier`.
- `model`: the exact provider/model identifier, when runtime metadata exposes
  it.

These fields are independent of Git Author and Committer identity. They are
also independent of ordinary `Co-authored-by` conventions.

## Trailer format

Each participant is one contiguous, ordered group in the final Git trailer
block:

```text
AI-Participant: tool=<id>[; agent=<id>][; role=<role>][; model=<id>]
Source-Definition: <portable-relative-path>
Source-Digest: sha256:<64-lowercase-hex>
```

The source lines are optional, but they always appear as a pair immediately
after their `AI-Participant` line.

### Participant fields

V1 writers MUST follow these rules:

1. `tool` is required and appears first.
2. Known optional fields appear in `agent`, `role`, `model` order. Unknown or
   invalid fields are omitted, never emitted with empty values.
3. Fields are separated by a semicolon and one ASCII space (`; `).
4. Identifier values match `[A-Za-z0-9][A-Za-z0-9._:/@+-]*`. They are
   case-sensitive and contain no whitespace, control characters, semicolons,
   or equals signs.
5. A `role` additionally matches `[a-z0-9][a-z0-9-]*`.

Readers SHOULD tolerate unknown future participant subfields after the known
fields. V1 writers MUST emit only `tool`, `agent`, `role`, and `model`.

The exact degraded records for the supported harnesses are:

```text
AI-Participant: tool=opencode
AI-Participant: tool=claude-code
```

### Source pair

`Source-Definition` identifies the participant's primary agent definition.
`Source-Digest` identifies the exact bytes of that definition with lowercase
SHA-256.

A source path:

- uses `/` separators;
- has nonempty relative path segments;
- has no absolute prefix, backslashes, host details, empty segments, or `.` or
  `..` segments.

The two source lines are inseparable and immediately follow their participant.
An orphan line, an incomplete pair, or a pair separated from its participant is
semantically malformed. Consumers MUST ignore the malformed source claim.

### Multiple participants

Repeat the complete group for each participant. Groups appear in the order of
first material contribution. Writers remove only exact duplicate complete
records and preserve unrelated Git trailers and the relative order of valid
participant groups.

Existing malformed attestation lines do not constitute a valid record.

Git's trailer parser recognizes trailer syntax and preserves line order. It
does not enforce participant field grammar, group adjacency, or source-pair
completeness. Consumers that rely on those semantics must validate them
separately.

## Resolving source definitions

### Generated agents

For a generated agent definition:

1. Normalize the managed rendered path to `/` separators.
2. Exact-match that path to a `files[]` entry with `kind: "agent"` in
   `.agentic-tooling/generated-manifest.json`. Do not infer a match from the
   agent name.
3. Emit the entry's slash-normalized `source` as `Source-Definition` and its
   validated `sourceDigest` as `Source-Digest`.
4. Never substitute the entry's rendered-output `digest`.

The manifest `source` is relative to its owning source distribution. It can be
a portable identity without resolving to a file in this repository. The
`sourceDigest` pins the canonical generator input, not the rendered agent or
the complete runtime prompt stack.

### Hand-authored agents

For a hand-authored definition, emit its slash-normalized path relative to the
owning dotfiles source root. Hash the exact bytes of that referenced file.
Equivalent commands include:

```sh
printf 'sha256:%s\n' "$(sha256sum -- "$definition" | cut -d ' ' -f1)"
printf 'sha256:%s\n' "$(shasum -a 256 -- "$definition" | cut -d ' ' -f1)"
```

```powershell
"sha256:$((Get-FileHash -Algorithm SHA256 -LiteralPath $filePath).Hash.ToLowerInvariant())"
```

If lookup, path validation, file access, hashing, or digest validation fails,
the writer omits both source trailers and retains the participant. Writers
never guess a source or block a commit because source provenance is unavailable.

## JSON handoff

The immutable structural contract is
[`configs/schemas/ai-attestation-handoff.v1.schema.json`](../configs/schemas/ai-attestation-handoff.v1.schema.json).
Both transports use the same JSON shape:

```json
{
  "schemaVersion": 1,
  "participants": [
    {
      "tool": "opencode",
      "agent": "special-builder",
      "role": "editor",
      "model": "openai/gpt-6-sol",
      "sourceDefinition": "agents/special-builder.yaml",
      "sourceDigest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
}
```

Unknown subfields in parsed Git trailers are reader-forward-compatible.
Unknown properties in a v1 JSON handoff are invalid; changing the JSON shape
requires a new schema version. The v1 schema accepts at most eight participants,
128 characters per identifier, and 512 characters per source definition.

### Transports and precedence

- Direct transport: the invocation-scoped `AI_ATTESTATION_JSON` environment
  variable.
- State transport: a worktree-private file resolved with
  `git rev-parse --git-path ai-attestation-opencode.json` or
  `git rev-parse --git-path ai-attestation-claude-code.json`.

The environment payload wins over the matching state file. Consumers never
merge records from both transports.

Producers atomically write state immediately before invoking the commit
wrapper. A state path supports one pending invocation per tool and worktree;
concurrent invocations use the direct environment transport or serialize
access.

### Consumer lifecycle

Commit wrappers follow this fail-open lifecycle:

1. Prefer the environment payload when present; read the matching state payload
   only when the environment payload is absent. Raw UTF-8 input is limited to
   16 KiB.
2. Discard unreadable JSON, unknown schema versions, invalid top-level shapes,
   and oversized input.
3. Require a valid `tool` for each participant. Omit invalid optional fields
   and omit incomplete or invalid source fields together. Preserve array order
   and deduplicate exact records.
4. If no valid participant survives, emit the wrapper's own tool-only degraded
   record.
5. Remove or discard the matching state file before invoking Git, including
   when the environment payload wins or either payload is invalid. Remove
   `AI_ATTESTATION_JSON` from Git's child environment.

A failed or retried commit requires a fresh producer handoff. Stale metadata is
never reused. Git-path resolution or cleanup failure does not block a commit;
the wrapper continues with validated environment data or its tool-only fallback.

Handoff data MUST NOT contain prompts, source contents, secrets, or unrelated
session context. Exact OpenCode plugin APIs, Claude hook APIs, wrapper language
choices, and trailer injection code are outside this specification.

## Producer plugins

The managed OpenCode entry point is
`~/.config/opencode/plugins/opencode-agent-attestation.js`, with support modules
under `attestation/`. The Claude Code plugin is
`~/.claude/skills/agent-attestation/`; its rendered `.claude-plugin/plugin.json`
enables skills-directory plugin discovery as `agent-attestation@skills-dir`.
It owns its lifecycle hooks and has no `SKILL.md` or loose settings hooks.
On Windows, these paths are relative to the native user profile, not a WSL home.

Preview the relevant chezmoi targets before applying them. Apply only with
approval, then restart OpenCode and restart Claude Code or use `/reload-plugins`.
Both integrations require Node.js and Git. Source resolution additionally uses
chezmoi. POSIX commit wrappers need `jq` to consume rich handoffs; without it,
they commit with tool-only provenance. The POSIX attestation test step declares
`jq` as a required capability, and `linux-fast` installs it before running tests.
Native Windows commit commands require PowerShell 7 and the `.ps1`
wrappers; the refusing `.cmd` stubs are not supported. No Bash, WSL, `jq`, or
Unix hashing utility is required by the native Windows plugins themselves.

OpenCode supplies `AI_ATTESTATION_JSON` through its invocation-local `shell.env`
hook. Claude uses `PreToolUse` to rewrite a recognized wrapper invocation,
preserving other tool inputs without granting permission. Run the direct
wrapper as the entire shell command from the repository working directory.
Supported forms include `command cc-commit -m 'message'`,
`oc-commit -m 'message'`, and `oc-commit -F 'message file'` in Bash, or
`& 'C:\path with spaces\cc-commit.ps1' -m 'message'` in PowerShell. Literal
single-quoted `-m` arguments can span lines; bare `-F <real-file>` invocations
also work when the file path is literal. PowerShell transport restores the
prior environment and preserves wrapper exit status. Claude's Bash tool also
supports a literal `pwsh` or `pwsh.exe` bridge:

```sh
cd "C:/repo" && pwsh -NoProfile -Command "& 'C:\repo\cc-commit.ps1' -m 'message'"
```

Only the Bash-to-PowerShell bridge accepts a prefix of one literal `cd`
(optionally with `--`) followed by `&&`; direct Bash wrapper calls do not.
The bridge accepts `-NoProfile`, `-NonInteractive`, and `-NoLogo` before
`-Command`, whose single literal argument must invoke the wrapper directly.
Quoted executable paths are supported. The payload is scoped to the `pwsh`
process; the directory change and original command text are preserved. This
route does not require enabling Claude's native PowerShell tool. Encoded
commands, execution-policy flags, additional commands, and dynamic PowerShell
arguments such as splats are not recognized.

Compounds such as `cd repo && cc-commit`, post-wrapper command chains,
heredocs (including `-F -` fed by a heredoc), dynamic executable expressions,
substitutions, redirects, and ambiguous commands do not receive the rich
producer handoff and can yield tool-only trailers. Explicit caller payloads
are not overwritten. Set the shell tool's working directory to the repository
for direct wrapper calls; run `git status` and `git log` in separate calls.

### Evidence and limits

Records describe cumulative observed participation in one harness session and
its explicitly linked descendants, not proof of contribution to each committed
diff. Runtime responses supply models; selected defaults and launch-only or
synthetic messages do not. OpenCode excludes its housekeeping agents. A model
switch can produce another participant record. Unknown agent names and roles
are omitted. Complete identical records are deduplicated, and only the first
eight distinct records are emitted; overflow produces a partial diagnostic.

OpenCode source pairs currently require an unambiguous explicit `{file:...}`
prompt reference, matching loaded and effective prompt text, and an exact
chezmoi source mapping. OpenCode 1.18.31 and 1.18.32 expand file references by
decoding UTF-8 and applying JavaScript `.trim()`; the resolver compares that
expansion exactly against both runtime values, without normalizing either.
Generated matches use the manifest's canonical source pair; hand-authored
definitions hash the unmodified source bytes, including boundary whitespace
and line endings. LF and CRLF checkouts can therefore have different digests.
Built-in, inline, historically reconciled, and unresolved Markdown-agent
definitions may lack
source pairs. Claude hooks do not establish the loaded definition path and
bytes, so its plugin currently omits source pairs rather than guessing from an
agent name. These omissions do not discard verified agent/model metadata.

OpenCode sends diagnostics to its structured logs under service
`agent-attestation`, once per code per plugin instance, without writing to the
terminal. `source-evidence-unavailable` uses debug level; collection failures
and limits use warning level. Source configuration codes distinguish oversized
files (`source-config-limit`), read or parse failures
(`source-config-unreadable`), and unexpected resolver failures
(`source-config-unavailable`). Logging failures do not block commits and have
no terminal fallback. Claude hook diagnostics use the hook runner's stderr.

Local state lives under `$XDG_STATE_HOME/agent-attestation` (falling back to
`~/.local/state/agent-attestation`) on POSIX and
`%LOCALAPPDATA%\agent-attestation` (falling back to the native profile's
`AppData\Local`) on Windows. It contains participant metadata and, for Claude,
explicit transcript paths and cursors, not transcript contents or prompts.
Windows directories inherit the user's ACL; POSIX modes do not establish a
Windows privacy boundary. Keep this state on private local storage supporting
hard links and atomic rename. Unsupported storage fails open with a diagnostic.

Collection has soft ceilings of 256 ledger sessions per harness and 2,048
records per ledger; Claude also limits session metadata to 256 entries. History
reconciliation limits descendants to 32, reads bounded pages or transcript
chunks, and stops scheduling work after its time budget. Caps and read failures
can yield partial or tool-only trailers. State is retained for resumed sessions;
there is no automatic eviction of potentially active sessions. To reclaim it,
stop all affected harness sessions before removing their attestation state.
That reset loses provenance history for later resumes. WSL and native Windows
state are independent and must not be translated or combined.

### Verification

Run `python3 -m unittest tests.test_agent_attestation` on POSIX. On native
Windows, run `pwsh -NoProfile -File tests/support/run_agent_attestation_windows.ps1
-SourceRoot <native-checkout>` as one command. The Windows runner stages a
temporary native-local fixture, runs Node tests and actual `.ps1` wrapper
commits, then cleans up. Use a native checkout rather than executing an unsigned
script over a WSL UNC path; do not change execution policy for this test.

Fixture validation has passed with Node 24.21, OpenCode's 1.18.31 API contract,
and native PowerShell 7.6.6/Git 2.55. Claude manifest validation passed on WSL
2.1.278 and Windows 2.1.268. Native fixtures also exercise Git Bash through
`pwsh` to the real `.ps1` wrapper. These checks do not establish live plugin
behavior. Live primary-plus-delegate commits passed for both harnesses on WSL
and native Windows, including Claude's default Bash-to-PowerShell route.
Model-switch and fresh-session isolation checks passed for both harnesses on
both platforms. OpenCode live source-pair checks passed on both platforms for
the hand-authored Plan prompt, matching each checkout's exact source-byte digest.
Claude source-pair emission is an explicitly deferred limitation of `dots-qt9`:
its documented hook metadata does not establish the loaded definition. Verified
Claude agent/model records remain available; both source fields are omitted
until a verifiable loaded-definition mechanism exists. This deferral is not a
successful source-pair check.

After approved activation, use an isolated repository to have a primary agent and a returned
delegate contribute, commit through the wrapper, and inspect trailers with the
command below. Repeat
after a model switch and in a separate session to check isolation; verify a
managed custom agent's source pair against the manifest or exact source bytes.
Do not count missing source evidence as a successful source-pair check.

## Examples

All digests below are illustrative values, not approved hashes or registry
entries.

### Generated agent

```text
AI-Participant: tool=opencode; agent=special-builder; role=editor; model=openai/gpt-6-sol
Source-Definition: agents/special-builder.yaml
Source-Digest: sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

### Hand-authored definition

```text
AI-Participant: tool=claude-code; agent=release-reviewer; role=reviewer; model=anthropic/claude-opus-5-5
Source-Definition: dot_claude/agents/release-reviewer.md
Source-Digest: sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789
```

### Degraded record

```text
AI-Participant: tool=opencode
```

### Multiple participants

```text
AI-Participant: tool=opencode; agent=plan-GPT-xhigh; role=planner; model=openai/gpt-6-sol
Source-Definition: agents/plan-GPT-xhigh.yaml
Source-Digest: sha256:1111111111111111111111111111111111111111111111111111111111111111
AI-Participant: tool=opencode; agent=special-builder; role=editor; model=openai/gpt-6-sol
Source-Definition: agents/special-builder.yaml
Source-Digest: sha256:2222222222222222222222222222222222222222222222222222222222222222
AI-Participant: tool=claude-code; role=verifier; model=anthropic/claude-sonnet-5
```

Inspect the final trailer block with:

```sh
git log -1 --format=%B | git interpret-trailers --parse
```

This command checks Git trailer syntax and order only. It does not establish
semantic validity or the truth of any claim.

## Trust boundary and non-goals

These trailers and hashes are self-asserted input provenance. They do not prove:

- that the named runtime executed;
- that a model followed its prompt;
- that a definition was the effective rendered runtime input;
- that the full prompt, tool, or context stack is represented;
- that a source was reviewed or approved;
- that the resulting change is correct.

SHA-256 supports equality checking only when a verifier independently obtains
the claimed source bytes. Git commit signing covers the commit bytes, including
trailers, but does not make their statements true. Rebasing, amending,
cherry-picking, or manually editing a message can alter the records.

Known-hash registries, signatures over source definitions, execution traces,
compliance claims, usage dashboards, and philosophical authorship are out of
scope.

## Rollout relationship to AI coauthoring

This convention is the intended successor to this repository's AI-specific,
one-shot `Co-authored-by` automation. A static configured coauthor identity
cannot represent dynamic per-session model selection, multiple agents, roles,
or source-definition provenance.

The coauthor automation remains operational alongside the attestation wrappers;
its retirement is separately scoped work despite verified POSIX and PowerShell
parity. Removing that automation does not deprecate `Co-authored-by` for human
coauthors or external project workflows. See
[ADR 0003](decisions/0003-git-agent-attestation.md) for the architectural
decision. The GitHub attribution trailers for OpenCode and Renovate commits
are not part of that automation; see
[ADR 0004](decisions/0004-github-attribution-coauthor-trailers.md).
