---
name: prompt-engineer
description: Author, rewrite, and improve AI agent prompts using structured
  patterns, checklists, and model-specific best practices
license: MIT
compatibility: opencode
metadata:
  audience: agent-engineers
  workflow: prompt-authoring
---

# prompt-engineer

Craft high-quality AI agent prompts following proven authoring patterns and
structured methodology. Covers role definition, task specification, constraint
setting, output formatting, and model-specific optimization.

## Use this skill when

- Writing a new agent prompt from scratch.
- Rewriting or improving an existing prompt that underperforms.
- Adapting a prompt for a different model (Claude, GPT, Gemini, etc.).
- Reviewing a prompt for completeness against a quality checklist.

## Do not use this skill when

- You need to refine vague requirements into structured specs (use
  `prompt-optimizer` instead).
- You need adversarial evaluation of a prompt (use `prompt-evaluator` instead).
- You are packaging a prompt into a reusable skill (use `skill-creator` instead).

## Prompt authoring workflow

1. **Define the role**: State who the agent is, its expertise, and its persona.
   Use imperative form. Be specific about domain knowledge.
2. **Specify the task**: Describe exactly what the agent must accomplish. Break
   complex tasks into numbered steps. State success criteria.
3. **Set constraints**: Define boundaries, forbidden actions, required formats,
   and guardrails. Use explicit negative instructions ("Do NOT...").
4. **Structure the output**: Specify the expected output format (markdown, JSON,
   XML, etc.). Provide a template or example when possible.
5. **Add examples**: Include 2-3 few-shot examples for non-trivial tasks.
   Examples should cover typical cases and at least one edge case.
6. **Enable reasoning**: For complex tasks, instruct the agent to think
   step-by-step before answering. Use chain-of-thought or structured reasoning
   sections.
7. **Provide context**: Include relevant background, definitions, or reference
   material the agent needs. Use progressive disclosure for large context.
8. **Review against checklist**: Run through the improvement checklist (see
   bundled reference) before finalizing.

## Key principles

- **Clarity over cleverness**: Simple, direct language beats elegant ambiguity.
- **Context then task**: Provide background before instructions.
- **Examples demonstrate intent**: Show, don't just tell.
- **Structure for scannability**: Use headings, lists, and sections the model
  can parse quickly.
- **Feedback loops**: Build in self-check steps where the agent verifies its
  own output.

## Model-specific guidance

See `references/model-specific.md` for detailed model-specific patterns:
- **Claude**: XML tags for structure, thinking blocks, artifact patterns.
- **GPT**: System/user/assistant role separation, function calling patterns.
- **General**: Temperature, top-p, and sampling guidance per task type.

## Output

Deliver the prompt as a markdown document with clear sections. Include inline
comments (as HTML comments) explaining non-obvious design choices.
