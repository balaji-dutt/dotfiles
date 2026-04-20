# Skill Review Checklist

Detailed checklist for skill reviews. Each item is scored as PASS, PARTIAL,
or FAIL.

## Frontmatter (4 items)

### F1: Name format
- **PASS**: Kebab-case, matches directory name, descriptive.
- **PARTIAL**: Kebab-case but generic or doesn't match directory.
- **FAIL**: Not kebab-case, missing, or misleading.

### F2: Description quality
- **PASS**: Specific, includes action verb and domain, < 100 words, would
  correctly trigger in 5/5 test scenarios.
- **PARTIAL**: Mostly specific but might false-trigger in 1-2 scenarios.
- **FAIL**: Vague, too broad, or misleading.

### F3: Compatibility field
- **PASS**: Correctly specifies target platform(s).
- **PARTIAL**: Present but may be incorrect.
- **FAIL**: Missing.

### F4: Required fields complete
- **PASS**: name, description, license, compatibility all present.
- **PARTIAL**: One optional but recommended field missing.
- **FAIL**: Any required field missing.

## Instructions (5 items)

### I1: Trigger conditions
- **PASS**: "Use this skill when" has 3+ specific, testable conditions.
- **PARTIAL**: Has trigger conditions but they are vague or fewer than 3.
- **FAIL**: Missing or single vague trigger.

### I2: Anti-triggers
- **PASS**: "Do not use this skill when" has 2+ conditions that prevent
  false loading. References alternative skills by name.
- **PARTIAL**: Has anti-triggers but incomplete.
- **FAIL**: Missing anti-triggers.

### I3: Workflow quality
- **PASS**: Numbered steps, actionable verbs, decision branches covered,
  complete from input to output.
- **PARTIAL**: Steps present but some are vague or branches missing.
- **FAIL**: No structured workflow, or major gaps.

### I4: Output specification
- **PASS**: Output format specified with template or example. Covers
  normal and error cases.
- **PARTIAL**: Output mentioned but no template/example.
- **FAIL**: No output specification.

### I5: Length appropriateness
- **PASS**: SKILL.md is 500-2000 words. Dense and focused.
- **PARTIAL**: 2000-5000 words. Could move content to references.
- **FAIL**: Over 5000 words, or under 200 words (too sparse).

## Resources (4 items)

### R1: Reference completeness
- **PASS**: All files mentioned in SKILL.md exist in `references/`.
- **PARTIAL**: Most exist; one or two missing but non-critical.
- **FAIL**: Critical referenced files missing.

### R2: Reference quality
- **PASS**: Each reference is self-contained, focused, and well-structured.
- **PARTIAL**: References exist but some are thin or unfocused.
- **FAIL**: References are stubs or contain copy-pasted noise.

### R3: No orphans
- **PASS**: Every file in `references/` is mentioned in SKILL.md.
- **PARTIAL**: One orphan file that may be useful.
- **FAIL**: Multiple orphan files.

### R4: No redundancy
- **PASS**: No content duplicated between SKILL.md and references.
- **PARTIAL**: Minor overlap.
- **FAIL**: Significant duplication.

## Quality (4 items)

### Q1: Writing style
- **PASS**: Imperative mood, consistent tone, scannable structure.
- **PARTIAL**: Mostly good but inconsistent in places.
- **FAIL**: Passive voice, inconsistent, hard to scan.

### Q2: Specificity
- **PASS**: No vague verbs. Actions are concrete and measurable.
- **PARTIAL**: Mostly specific with 1-2 vague spots.
- **FAIL**: Multiple vague instructions.

### Q3: Edge cases
- **PASS**: Common edge cases addressed with explicit handling.
- **PARTIAL**: Some edge cases mentioned but handling unclear.
- **FAIL**: No edge case consideration.

### Q4: Guardrails
- **PASS**: Safety constraints present where needed. Scope limits clear.
- **PARTIAL**: Some guardrails but gaps for sensitive operations.
- **FAIL**: No guardrails for a skill that needs them.

## Scoring

```
Total items: 17
PASS = 2 points, PARTIAL = 1 point, FAIL = 0 points
Max score: 34

Verdict:
- PASS:        Score >= 28 AND no FAIL in F1-F4 or I3-I4
- CONDITIONAL: Score >= 20 AND no more than 2 FAILs
- FAIL:        Score < 20 OR 3+ FAILs
```
