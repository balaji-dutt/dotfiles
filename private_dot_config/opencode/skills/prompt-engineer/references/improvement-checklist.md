# Prompt Improvement Checklist

Use this checklist to review any agent prompt before finalizing. Each item
should be explicitly satisfied or consciously marked as not applicable.

## 1. Clear Role Definition
- [ ] The agent's identity and expertise are stated in the first 1-2 sentences.
- [ ] The role is specific enough to constrain behavior (not just "helpful AI").
- [ ] Domain knowledge requirements are explicit.

## 2. Specific Task Description
- [ ] The task is described in concrete, measurable terms.
- [ ] Success criteria are defined (what does "done" look like?).
- [ ] Complex tasks are broken into numbered steps.

## 3. Explicit Constraints
- [ ] Boundaries are stated (what the agent must NOT do).
- [ ] Scope limits are defined (what is in/out of scope).
- [ ] Safety guardrails are present for sensitive domains.

## 4. Output Format Specification
- [ ] The expected output format is explicitly described.
- [ ] A template or example of the format is provided.
- [ ] Edge cases in formatting are addressed (empty results, errors, etc.).

## 5. Examples Provided
- [ ] At least 2 few-shot examples for non-trivial tasks.
- [ ] Examples cover a typical case and at least one edge case.
- [ ] Examples demonstrate the exact expected output format.

## 6. Reasoning Enabled
- [ ] For complex tasks, step-by-step reasoning is requested.
- [ ] Thinking/reasoning sections are structured (not just "think carefully").
- [ ] The agent is told when to show vs. hide its reasoning.

## 7. Context Provided
- [ ] All necessary background information is included.
- [ ] Context is organized with progressive disclosure (essential first).
- [ ] References to external docs/files are explicit and navigable.

## 8. Edge Cases Addressed
- [ ] Common failure modes have explicit handling instructions.
- [ ] Ambiguous inputs have a defined resolution strategy.
- [ ] The agent knows what to do when it is uncertain or stuck.

## 9. Appropriate Length
- [ ] The prompt is not longer than necessary.
- [ ] Redundant instructions are consolidated.
- [ ] Long reference material is in bundled references, not inline.

## 10. Consistent Tone
- [ ] The writing style is consistent throughout.
- [ ] Imperative mood is used for instructions.
- [ ] The tone matches the agent's intended persona.

## Scoring

Rate each item: 0 (missing), 1 (partial), 2 (fully met).

- **16-20**: Production-ready prompt.
- **11-15**: Good prompt; address gaps before deployment.
- **6-10**: Needs significant improvement.
- **0-5**: Rewrite from scratch using the authoring workflow.
