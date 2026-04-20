# Skill Structure Reference

Detailed requirements for each component of a well-formed skill.

## YAML Frontmatter (required)

```yaml
---
name: kebab-case-name          # Required. Must match directory name.
description: >-                # Required. 1-2 sentences, < 100 words.
  Concise trigger description
  that helps the agent decide
  whether to load this skill.
license: MIT                   # Required. MIT, Apache-2.0, Proprietary, etc.
compatibility: opencode        # Required. opencode | claude-code | both
metadata:                      # Optional. Additional classification.
  audience: target-users
  workflow: workflow-name
---
```

### Description writing rules

The description is the most critical field. It determines whether the skill
gets loaded at the right time.

- **Be specific**: "Evaluate agent prompts using adversarial testing" not
  "Help with prompts."
- **Include the action verb**: "Transform", "Evaluate", "Package", "Review."
- **Include the domain**: "agent prompts", "EARS requirements", "skill files."
- **Exclude implementation details**: Don't mention tools or methods.
- **Test**: Would an agent reading only this description correctly decide to
  load/skip this skill for 5 different scenarios?

## SKILL.md Body Structure

### Required sections

1. **Title** (`# skill-name`): Matches the frontmatter name.
2. **Summary**: 1-2 sentences after the title.
3. **Use this skill when**: Bullet list of trigger conditions.
4. **Do not use this skill when**: Bullet list of anti-triggers.
5. **Workflow**: Numbered steps for the core process.
6. **Output**: Expected output format.

### Recommended sections

7. **Inputs**: What the skill expects to receive.
8. **Guardrails**: Safety constraints and limitations.
9. **References**: Pointers to bundled reference files.

### Style rules

- Use imperative mood for instructions ("Read the input", not "You should
  read the input").
- Use third-person for descriptions ("The skill evaluates...", not "I
  evaluate...").
- Keep SKILL.md under 5000 words (target 1500-2000).
- Use markdown headers, lists, and tables for structure.
- No inline code longer than one line; move to references.

## References Directory

### When to create a reference file

- Content exceeds 200 words and is not part of the core workflow.
- Content is a template, schema, or example that agents copy from.
- Content is a detailed specification that only some invocations need.

### Naming convention

- `kebab-case.md` for markdown references.
- `template-name.yaml` or `template-name.json` for config templates.
- `script-name.sh` or `script-name.ps1` for executable scripts.

### Reference file structure

Each reference file should be self-contained:
- Title (`# Reference Name`)
- Brief intro (what this reference contains and when to use it).
- The actual content.
- No circular references between reference files.

## Directory Layout

```
skill-name/
├── SKILL.md                    # Always present
└── references/                 # Optional, only if needed
    ├── detailed-guide.md       # Detailed instructions
    ├── template.yaml           # Config template
    ├── examples.md             # Worked examples
    └── checklist.md            # Validation checklist
```

## Anti-patterns

- **Bloated SKILL.md**: If it exceeds 3000 words, move detail to references.
- **Orphan references**: Files in `references/` not mentioned in SKILL.md.
- **Redundant content**: Same information in SKILL.md and a reference.
- **Vague triggers**: "Use when you need help" — too broad.
- **Missing anti-triggers**: Without "do not use when", the skill loads too
  often.
- **Assumed subagent context**: Skills should work whether loaded by a primary
  agent or a subagent.
