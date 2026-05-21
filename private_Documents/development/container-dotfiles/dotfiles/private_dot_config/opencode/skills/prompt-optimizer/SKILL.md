---
name: prompt-optimizer
description: Transform vague requirements into precise, testable agent
  specifications using EARS methodology and domain theory grounding
license: MIT
compatibility: opencode
metadata:
  audience: agent-engineers
  workflow: requirements-refinement
---

# prompt-optimizer

Turn loose, ambiguous agent requirements into structured, testable
specifications. Uses EARS (Easy Approach to Requirements Syntax) methodology
combined with domain theory grounding to produce specifications that can be
directly translated into agent prompts.

## Use this skill when

- You have a vague or high-level description of what an agent should do.
- Requirements are ambiguous and need structured decomposition.
- You need to identify edge cases and failure modes before writing a prompt.
- You want to ground agent behavior in established domain patterns.

## Do not use this skill when

- Requirements are already clear and well-structured.
- You are writing the actual prompt (use `prompt-engineer` instead).
- You are evaluating an existing prompt (use `prompt-evaluator` instead).

## Workflow

### Step 1: Analyze the raw requirement

Read the input requirement. Identify:

- The core intent (what the user actually wants).
- Implicit assumptions that need to be made explicit.
- Ambiguous terms that need definitions.
- Missing information that requires clarification.

### Step 2: Transform using EARS patterns

Apply EARS syntax patterns to rewrite each requirement:

| Pattern          | Template                                                      | Use when             |
| ---------------- | ------------------------------------------------------------- | -------------------- |
| **Ubiquitous**   | "The agent shall [action]."                                   | Always-on behavior   |
| **Event-driven** | "When [event], the agent shall [action]."                     | Triggered behavior   |
| **State-driven** | "While [state], the agent shall [action]."                    | Ongoing conditional  |
| **Conditional**  | "If [condition], then the agent shall [action]."              | One-time conditional |
| **Unwanted**     | "If [unwanted situation], then the agent shall [mitigation]." | Error/edge handling  |

See `references/ears-syntax.md` for detailed EARS syntax reference.

### Step 3: Ground in domain theories

Map requirements to relevant domain frameworks:

- Workflow agents: GTD (Getting Things Done), Kanban principles.
- User-facing agents: BJ Fogg behavior model, UX heuristics.
- Analytical agents: scientific method, hypothesis testing.
- Creative agents: design thinking, diverge/converge patterns.

See `references/domain-theories.md` for the theory catalog.

### Step 4: Extract test scenarios

For each EARS requirement, generate:

- A happy-path test case.
- An edge-case test case.
- A failure-mode test case.

### Step 5: Produce the structured specification

Output format:

```markdown
## Agent Specification: [Name]

### Role

[1-2 sentence role definition]

### Core Skills

- [Skill 1]: [Description]
- [Skill 2]: [Description]

### Workflows

1. [Workflow name]: [Step-by-step process]

### Requirements (EARS)

- [REQ-001] [EARS requirement]
- [REQ-002] [EARS requirement]

### Test Scenarios

| ID    | Requirement | Input   | Expected Output |
| ----- | ----------- | ------- | --------------- |
| T-001 | REQ-001     | [input] | [output]        |

### Edge Cases

- [Edge case 1]: [Handling strategy]

### Open Questions

- [Question requiring human input]
```

## Output

Deliver a structured agent specification in the format above. Flag any open
questions that require human input before the specification can be finalized.
