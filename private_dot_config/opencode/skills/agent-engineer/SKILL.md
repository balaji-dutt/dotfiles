---
name: agent-engineer
description: Design, build, evaluate, and package AI agent prompts, skills,
  and plugins by coordinating specialized sub-skills
license: MIT
compatibility: opencode
metadata:
  audience: agent-engineers
  workflow: agent-engineering
---

# agent-engineer

Meta-agent that coordinates specialized skills to engineer complete AI agent
definitions. Given a loose starting idea, produces production-ready agent
prompts, skills, and optionally plugins through a structured workflow of
requirements refinement, prompt authoring, adversarial evaluation, and
packaging.

## Use this skill when

- Designing a new AI agent from a loose idea or requirement.
- Building a multi-agent workflow with orchestration.
- Improving an existing agent's prompt, skills, or configuration.
- Evaluating whether an agent needs skills or plugins.
- Packaging agent artifacts for reuse across repos.

## Do not use this skill when

- Doing a single, narrow prompt tweak (use `prompt-engineer` directly).
- Only reviewing a skill (use `skill-reviewer` directly).
- The task has nothing to do with agent/prompt engineering.

## Coordinator workflow

Follow the steps in `references/workflow.md` for the full process. Summary:

1. **Intake**: Classify the request (single agent / multi-agent / improvement).
2. **Requirements**: Use `prompt-optimizer` to produce EARS specifications.
3. **Authoring**: Use `prompt-engineer` to draft the prompt(s).
4. **Evaluation**: Use `prompt-evaluator` to adversarially test. Iterate until
   PASS (max 3 rounds, then escalate to human).
5. **Skill assessment**: Determine if skills would benefit the agent. Suggest
   prior art keyword searches. Review provided prior art with
   `skill-reviewer`. Create skills with `skill-creator` if needed.
6. **Plugin development** (on-demand): If workflow enforcement or multi-agent
   orchestration is needed, use `plugin-developer`.
7. **Delivery**: Output all artifacts (prompt, config, skills, plugins).

## Inputs

- A loose description of the desired agent or workflow.
- (Optional) Prior art links or skill files for review.
- (Optional) Target platform preference (OpenCode / Claude Code / both).
- (Optional) Request for plugin development.

## Output

Complete agent engineering deliverables:
- Agent prompt document (markdown).
- Agent configuration snippet (for opencode.jsonc or equivalent).
- Skills (SKILL.md + references) if applicable.
- Plugin source if requested.
- Evaluation report from prompt-evaluator.

## Guardrails

- Never skip evaluation. Every prompt must pass `prompt-evaluator` before
  delivery.
- Never assume tool availability. Check for promptfoo, plugin systems, etc.
  and degrade gracefully.
- Always ask for human input on architectural decisions (single vs multi-agent,
  model selection, permission scope).
- Subagents cannot spawn subagents. Design skill interactions as sequential
  loads, not nested delegations.

See `agent-engineer-prompt.md` for the full coordinator prompt.
