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
