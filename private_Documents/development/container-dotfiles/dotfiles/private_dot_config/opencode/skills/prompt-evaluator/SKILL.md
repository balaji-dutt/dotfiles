---
name: prompt-evaluator
description: Adversarially evaluate agent prompts using LLM-as-judge and
  optional promptfoo harness for regression testing
license: MIT
compatibility: opencode
metadata:
  audience: agent-engineers
  workflow: prompt-evaluation
---

# prompt-evaluator

Evaluate agent prompts through adversarial testing, rubric-based scoring, and
regression checks. Works in two modes: LLM-as-judge (always available) and
promptfoo harness (when the tool is installed).

## Use this skill when

- A new or revised agent prompt needs quality validation before deployment.
- You want to identify failure modes, edge cases, or regressions in a prompt.
- You need a structured evaluation report with pass/fail criteria.
- You want to compare two prompt versions head-to-head.

## Do not use this skill when

- You are still refining requirements (use `prompt-optimizer` first).
- You are writing the prompt (use `prompt-engineer` first).
- You need to package the prompt as a skill (use `skill-creator` after
  evaluation passes).

## Evaluation modes

### Mode 1: LLM-as-Judge (always available)

No external tools required. The evaluating agent role-plays as a critical
reviewer using a structured rubric.

**Workflow**:
1. Read the prompt under evaluation.
2. Generate 5-7 adversarial test inputs designed to expose weaknesses:
   - Ambiguous inputs that test interpretation.
   - Out-of-scope requests that test guardrails.
   - Edge cases that test robustness.
   - Malicious inputs that test safety.
   - Multi-step requests that test coherence.
3. For each test input, simulate the expected agent behavior.
4. Score each response against the evaluation rubric (see
   `references/evaluation-rubric.md`).
5. Produce a structured evaluation report.

### Mode 2: Promptfoo Harness (when available)

Uses promptfoo for automated, reproducible evaluation with assertions.

**Prerequisite**: Promptfoo and its provider SDK must be installed in one package
root. The supported runtime includes `promptfoo`, `@opencode-ai/sdk`,
`@anthropic-ai/claude-agent-sdk`, and `@anthropic-ai/sdk`. Validate it with:
- macOS/Linux/WSL2: `bash references/install-promptfoo.sh`
- Windows: `pwsh references/install-promptfoo.ps1`

The compatibility-named scripts validate an existing managed runtime. They do
not install unpinned packages. If validation fails, provision the bundle through
the host or project's lockfile workflow.

**Choose a provider deliberately**:
- `opencode:sdk`: Route through configured OpenCode providers; requires
  `@opencode-ai/sdk`.
- `anthropic:claude-agent-sdk` (alias `anthropic:claude-code`): Exercise an
  authenticated Claude agent workflow; requires
  `@anthropic-ai/claude-agent-sdk`.
- `anthropic:messages:<model>` or `anthropic:completion:<model>`: Call the
  Anthropic API directly; requires `@anthropic-ai/sdk` and API credentials.
- `echo`: Preview rendered prompts only. Do not use echo results as evidence
  that a live provider or its SDK works.

#### Provider decision table

| Behavior under test | Provider and execution mode |
| --- | --- |
| Prompt text, rendering, or deterministic content contracts | Use static/echo assertions and LLM-as-judge. A live OpenCode server is not required. |
| OpenCode authentication or model routing | Use `opencode:sdk` with the repository-targeted live OpenCode workflow below. |
| OpenCode runtime discovery of a candidate agent, skill, or plugin | Stage the candidate in an isolated target fixture before startup, then use `opencode:sdk` with a fresh server. |
| Claude Agent SDK behavior or direct Anthropic API behavior | Use the matching Anthropic provider; do not route through OpenCode merely because it is installed. |

#### Repository-targeted live OpenCode workflow

Use this workflow only when OpenCode authentication, model routing, or runtime
discovery is part of the acceptance criteria. Live-provider evaluation is
manual and opt-in, not a required CI gate.

1. Identify the explicit repository or linked-worktree target. For prompt-text
   evaluation, pass the candidate prompt directly to Promptfoo. For startup-
   loaded agent, skill, or plugin evaluation, stage the candidate in an isolated
   target fixture before launching OpenCode; do not accidentally test the
   currently installed configuration.
2. From the explicit target, start a fresh owned server with
   `opencode serve --hostname 127.0.0.1 --port 0`. Port `0` asks the operating
   system for one automatically selected available loopback port; it does not
   expose all ports. Capture the advertised endpoint and supply it as the
   Promptfoo `opencode:sdk` `baseUrl`. Never hard-code a port or invent a
   repository-specific environment variable name.
3. Before evaluation, require `/global/health` to report healthy with the
   expected OpenCode version. Verify `/path` identifies the exact target. Also
   verify `/project/current`: for a linked worktree its common project root may
   differ from the target, so accept the target only when it is the project
   worktree or appears in `sandboxes`. Reject a healthy server for the wrong
   repository or concurrent worktree.
4. A caller may provide a server it owns through the session-scoped
   `PROMPTFOO_OPENCODE_BASE_URL`. Interpolate it as
   `baseUrl: '{{env.PROMPTFOO_OPENCODE_BASE_URL}}'`, perform the same health,
   version, and identity checks, and never terminate a caller-owned process.
5. OpenCode loads agent, skill, plugin, and other runtime configuration at
   startup. Restart the owned server after candidate configuration changes.
   Direct prompt-text changes do not require a configuration restart.
6. For prompt-only tests, explicitly set all Promptfoo-supported tools to
   `false`; do not rely on provider defaults. To evaluate tool behavior, use an
   isolated fixture and enable only the smallest required tool set.
7. Treat health, identity, SDK loading, authentication, and process-start
   failures as evaluation-infrastructure failures, not prompt failures. Record
   the failure and fall back to LLM-as-judge rather than claiming live-provider
   evidence.
8. Keep one-off configs, logs, and results task-local and ephemeral. Commit
   durable Promptfoo assets only for a stable public contract or intentional
   regression gate. In all cases, terminate only the server process you started
   and report the provider, target, endpoint validation, cleanup, and results.

**Workflow**:
1. Select the provider whose runtime behavior the evaluation must cover.
2. Generate a `promptfooconfig.yaml` from the prompt and test scenarios.
3. Define assertion types per test case:
   - `contains` / `not-contains`: String presence checks.
   - `regex`: Pattern matching.
   - `llm-rubric`: LLM-judged quality criteria.
   - `python`: Custom Python assertion scripts.
   - `latency`: Response time thresholds.
4. Run `promptfoo eval`.
5. Parse results and generate the evaluation report.
6. If failures are found, propose specific prompt edits and re-evaluate.

See `references/promptfoo-guide.md` for configuration reference.

## Evaluation report format

```markdown
## Prompt Evaluation Report

### Summary
- **Prompt**: [name/path]
- **Mode**: LLM-as-Judge | Promptfoo
- **Date**: [date]
- **Verdict**: PASS | FAIL | CONDITIONAL

### Scores
| Criterion | Score (0-5) | Notes |
|-----------|-------------|-------|
| Role clarity | | |
| Task specificity | | |
| Constraint enforcement | | |
| Output format compliance | | |
| Edge case handling | | |
| Safety/guardrails | | |
| Coherence under stress | | |

### Test Results
| Test ID | Input | Expected | Actual | Pass/Fail |
|---------|-------|----------|--------|-----------|

### Failure Analysis
[For each failure, root cause and suggested fix]

### Recommendations
[Prioritized list of improvements]
```

## Verdicts

- **PASS**: All criteria score >= 3, no critical failures.
- **CONDITIONAL**: Minor gaps identified; prompt is usable with noted caveats.
- **FAIL**: Any criterion scores <= 1, or critical safety/guardrail failure.

## Iteration protocol

When verdict is FAIL or CONDITIONAL:
1. Apply recommended fixes to the prompt.
2. Re-run evaluation (same test cases + any new cases targeting the fixes).
3. Repeat until PASS or human override.
Maximum 3 iterations before escalating to human review.
