---
name: skill-reviewer
description: Review and validate skills for quality, structure, and
  effectiveness using a structured checklist and critic perspective
license: MIT
compatibility: opencode
metadata:
  audience: agent-engineers
  workflow: skill-review
---

# skill-reviewer

Act as a critical reviewer for skills. Evaluate structure, content quality,
trigger accuracy, and completeness. Produce a structured review with pass/fail
verdict and actionable improvement recommendations.

## Use this skill when

- A newly created skill needs quality validation before deployment.
- An external skill (from prior art or another repo) needs suitability review.
- You need to decide whether to adopt, fork, or reject an external skill.
- A skill has been modified and needs re-review.

## Do not use this skill when

- You are creating a skill from scratch (use `skill-creator` instead).
- You are evaluating an agent prompt (use `prompt-evaluator` instead).

## Review modes

### Mode 1: Self-review
Review a skill you or the agent just created. Focus on quality and completeness.

### Mode 2: External review
Review a skill from an external source. Focus on suitability, quality, and
adaptation needs.

### Mode 3: Comparison review
Compare two skills that serve similar purposes. Recommend which to adopt or
how to synthesize the best of both.

## Review workflow

### Step 1: Read the skill

Read the complete SKILL.md and all reference files. Note the skill's claimed
purpose, triggers, workflow, and output format.

### Step 2: Evaluate against checklist

Score each criterion as PASS, PARTIAL, or FAIL:

**Frontmatter**:
- [ ] `name` is kebab-case and matches directory name.
- [ ] `description` is concise, specific, and trigger-accurate.
- [ ] `compatibility` is correctly specified.
- [ ] All required fields present.

**Instructions**:
- [ ] "Use this skill when" section has clear trigger conditions.
- [ ] "Do not use this skill when" section prevents false triggers.
- [ ] Workflow steps are numbered, actionable, and complete.
- [ ] Output format is specified with template or example.
- [ ] SKILL.md is under 5000 words.

**Resources**:
- [ ] All referenced files exist in `references/`.
- [ ] References are self-contained and focused.
- [ ] No orphan references (files not mentioned in SKILL.md).
- [ ] No redundancy between SKILL.md and references.

**Quality**:
- [ ] Imperative mood used for instructions.
- [ ] No vague verbs ("handle", "manage", "process").
- [ ] Edge cases addressed or flagged.
- [ ] Guardrails present for safety-sensitive operations.

### Step 3: Trigger accuracy test

Mentally simulate 5 scenarios (3 positive, 2 negative) and check whether the
description would correctly trigger/skip the skill.

### Step 4: Produce the review

## Review report format

```markdown
## Skill Review: [skill-name]

### Mode: Self-review | External | Comparison
### Verdict: PASS | CONDITIONAL | FAIL

### Checklist Results
| Category | Item | Result |
|----------|------|--------|
| Frontmatter | name | PASS/PARTIAL/FAIL |
| ... | ... | ... |

### Trigger Accuracy: [X/5]

### Strengths
- [Strength 1]
- [Strength 2]

### Issues
- [CRITICAL] [Issue requiring fix before use]
- [MINOR] [Issue that should be fixed but doesn't block use]

### Recommendations
- [Specific, actionable recommendation]

### For external skills: Suitability Assessment
- **Adopt as-is**: [yes/no and why]
- **Fork and adapt**: [what needs changing]
- **Reject**: [why it's not suitable]
```

## Review principles

- **Additive only for external skills**: Never propose removing content from
  external skills. Only suggest additions or adaptations.
- **Specificity over generality**: "The description should mention 'EARS
  methodology'" not "the description could be more specific."
- **Actionable feedback**: Every issue must have a clear fix.
- **Respectful tone**: The skill author made reasonable choices; explain why
  an alternative is better, don't just criticize.
