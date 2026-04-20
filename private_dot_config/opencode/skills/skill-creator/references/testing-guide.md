# Skill Testing Guide

How to validate that a skill works correctly before deployment.

## Testing methodology

### 1. Trigger accuracy test

Verify the skill loads when it should and does not load when it should not.

Create 5 scenarios:
- 3 where the skill SHOULD be loaded (positive triggers).
- 2 where the skill should NOT be loaded (negative triggers).

For each scenario, ask: "Given only the skill description, would an agent
correctly decide to load/skip this skill?"

**Pass criteria**: 5/5 correct decisions.

### 2. Workflow completeness test

Walk through the skill's workflow with a realistic input:
1. Does each step have clear instructions?
2. Are there decision points with all branches covered?
3. Does the workflow produce the specified output format?
4. Are edge cases handled or flagged?

**Pass criteria**: Complete walkthrough with no ambiguous steps.

### 3. Baseline comparison (with-skill vs without-skill)

Compare agent performance on a task with and without the skill loaded:
- **Without skill**: Agent receives the raw task with no skill guidance.
- **With skill**: Agent receives the same task and loads the skill.

Evaluate both outputs on:
- Completeness (does it cover all requirements?).
- Structure (is the output well-organized?).
- Quality (is the content accurate and useful?).
- Consistency (does it follow the expected format?).

**Pass criteria**: With-skill output is measurably better on at least 2 of 4
dimensions.

### 4. Reference accessibility test

For each reference file mentioned in SKILL.md:
1. Is the file present in `references/`?
2. Is the reference self-contained (readable without other references)?
3. Does the SKILL.md clearly indicate when to load this reference?

**Pass criteria**: All references present, accessible, and clearly triggered.

### 5. Cross-platform compatibility test

If the skill claims compatibility with multiple platforms:
1. Verify the skill structure matches each platform's conventions.
2. Check that platform-specific features are conditional.
3. Confirm reference files use platform-agnostic formats.

**Pass criteria**: Skill loads and functions on all claimed platforms.

## Test report format

```markdown
## Skill Test Report: [skill-name]

### Trigger Accuracy: PASS/FAIL
- Positive triggers: [X/3]
- Negative triggers: [X/2]

### Workflow Completeness: PASS/FAIL
- Ambiguous steps: [list or "none"]
- Missing edge cases: [list or "none"]

### Baseline Comparison: PASS/FAIL
- Completeness: with-skill [better/same/worse]
- Structure: with-skill [better/same/worse]
- Quality: with-skill [better/same/worse]
- Consistency: with-skill [better/same/worse]

### Reference Accessibility: PASS/FAIL
- Missing references: [list or "none"]
- Unclear triggers: [list or "none"]

### Overall: PASS/FAIL
```

## Iteration protocol

If any test fails:
1. Identify the root cause.
2. Fix the specific issue in SKILL.md or references.
3. Re-run only the failed test.
4. Maximum 3 iterations before requesting human review.
