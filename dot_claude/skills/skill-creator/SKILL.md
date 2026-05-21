---
name: skill-creator
description: Package agent prompts and workflows into reusable, testable skills
  with proper structure and progressive disclosure
license: MIT
compatibility: opencode
metadata:
  audience: agent-engineers
  workflow: skill-packaging
---

# skill-creator

Package agent prompts, workflows, and reference material into reusable skills
following the progressive disclosure pattern: metadata (trigger description) ->
SKILL.md body (instructions) -> bundled references (detailed resources).

## Use this skill when

- An agent prompt or workflow needs to be packaged as a reusable skill.
- You are creating a new skill from scratch for a specific capability.
- You need to convert an existing workflow into a shareable, portable format.
- You want to structure reference material for progressive context loading.

## Do not use this skill when

- You are still defining requirements (use `prompt-optimizer` first).
- You are reviewing an existing skill (use `skill-reviewer` instead).
- You are building a plugin with hooks/tools (use `plugin-developer` instead).

## Skill structure

```
skill-name/
  SKILL.md              # Frontmatter + core instructions (< 2000 words)
  references/           # Bundled resources (unlimited size)
    reference-1.md
    reference-2.md
    template.md
```

## Progressive disclosure layers

1. **Metadata** (~100 words): The YAML frontmatter `description` field. This is
   what the agent sees when deciding whether to load the skill. Must be
   precise enough to trigger correctly and avoid false matches.

2. **SKILL.md body** (< 5000 words, target 1500-2000): The core instructions
   loaded when the skill is activated. Contains workflow steps, decision
   logic, and output format. Should be self-sufficient for the common case.

3. **Bundled references** (unlimited): Detailed resources loaded on demand.
   Templates, examples, checklists, syntax references. Only loaded when the
   skill instructions explicitly reference them.

## Skill creation workflow

### Step 1: Capture intent

Answer these questions:

- What capability does this skill provide?
- When should an agent load this skill? (trigger conditions)
- When should an agent NOT load this skill? (anti-triggers)
- What inputs does the skill expect?
- What outputs does the skill produce?

### Step 2: Research prior art

Search for existing skills that cover similar ground:

- Suggest keyword searches to the user.
- If prior art is provided, evaluate with `skill-reviewer`.
- Decide: create from scratch, fork and adapt, or compose existing skills.

### Step 3: Write the SKILL.md

Follow this structure:

```markdown
---
name: [kebab-case-name]
description: [1-2 sentence trigger description, ~100 words max]
license: [license]
compatibility: [opencode|claude-code|both]
metadata:
  audience: [target users]
  workflow: [workflow-name]
---

# [skill-name]

[1-2 sentence summary of what the skill does.]

## Use this skill when

[Bullet list of trigger conditions]

## Do not use this skill when

[Bullet list of anti-triggers]

## Inputs

[What the skill expects to receive]

## Workflow

[Numbered steps for the core process]

## Output

[Expected output format and markers]

## Guardrails

[Safety constraints and limitations]
```

### Step 4: Create bundled references

For each reference file:

- Name it descriptively (kebab-case).
- Include it only if the SKILL.md body references it.
- Keep each reference focused on one topic.

### Step 5: Validate the skill

Run through this checklist:

- [ ] Frontmatter has all required fields (name, description, compatibility).
- [ ] Description is precise enough to trigger correctly.
- [ ] SKILL.md body is under 5000 words.
- [ ] Workflow steps are numbered and actionable.
- [ ] Output format is specified.
- [ ] All referenced files exist in `references/`.
- [ ] No redundancy between SKILL.md body and references.

See `references/skill-structure.md` for detailed structural requirements.
See `references/testing-guide.md` for evaluation methodology.

## Composability rules

- Skills should be orthogonal: each skill does one thing.
- Skills can reference other skills by name (e.g., "use `prompt-engineer`
  for authoring").
- Skills must NOT assume they run as subagents (subagents cannot spawn
  subagents in most frameworks).
- Pipeline handoffs: if skill A's output feeds skill B, document the
  interface explicitly.

## Output

Deliver the complete skill directory contents (SKILL.md + references) ready
for placement in the target `.opencode/skills/` or `.claude/skills/` directory.
