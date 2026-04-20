# Agent Engineer Workflow Reference

Detailed step-by-step workflow for the Agent Engineer coordinator.

## Workflow Diagram

```
User Input (loose idea)
    │
    ▼
┌─────────────┐
│ 1. Intake   │ ── Classify: single / multi-agent / improvement
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│ 2. Requirements │ ── prompt-optimizer: EARS specs + domain grounding
└──────┬──────────┘
       │
       ▼
┌─────────────┐
│ 3. Author   │ ── prompt-engineer: RTCF pattern + model-specific
└──────┬──────┘
       │
       ▼
┌──────────────┐     ┌─────────┐
│ 4. Evaluate  │────▶│ Fix &   │──┐
└──────┬───────┘     │ Re-eval │  │ (max 3x)
       │             └─────────┘──┘
       │ PASS
       ▼
┌────────────────┐
│ 5. Skills      │ ── Search prior art → review → create/adopt
└──────┬─────────┘
       │
       ▼
┌────────────────┐
│ 6. Plugins     │ ── (on-demand) plugin-developer
└──────┬─────────┘
       │
       ▼
┌────────────────┐
│ 7. Deliver     │ ── Prompt + config + skills + plugin + report
└────────────────┘
```

## Phase details

### Phase 1: Intake — Decision matrix

| Signal | Classification |
|--------|---------------|
| "I need an agent that..." | Single agent |
| "I need agents that work together..." | Multi-agent |
| "This agent isn't working well..." | Improvement |
| "I want to enforce a workflow..." | Multi-agent + plugin |
| "Review this prompt..." | Improvement (evaluation only) |

Questions to ask:
1. What is the agent's primary purpose?
2. Who is the target user (developer, end-user, other agents)?
3. What platform? (OpenCode / Claude Code / platform-agnostic)
4. What model? (Or should I recommend based on requirements?)
5. Are there existing prompts, skills, or plugins to build on?
6. What tools/permissions does the agent need?
7. Is workflow enforcement needed (hooks, quality gates)?

### Phase 2: Requirements — EARS checklist

For each requirement produced by `prompt-optimizer`, verify:
- [ ] Uses one of the 5 EARS patterns (ubiquitous, event, state, conditional,
      unwanted).
- [ ] Has a corresponding test scenario.
- [ ] Is independently testable (no compound requirements).
- [ ] Uses specific verbs (not "handle", "manage", "process").

### Phase 3: Authoring — Quality gates

Before moving to evaluation, the prompt must:
- [ ] Score >= 16/20 on the improvement checklist.
- [ ] Address all EARS requirements from Phase 2.
- [ ] Include model-specific optimizations for the target model.
- [ ] Have guardrails for all "unwanted behavior" requirements.

### Phase 4: Evaluation — Escalation protocol

| Iteration | Action |
|-----------|--------|
| 1 | Fix all FAIL criteria, re-evaluate |
| 2 | Fix remaining issues, re-evaluate |
| 3 | If still failing, present report to user with specific questions |

Never iterate more than 3 times without human input.

### Phase 5: Skills — Prior art search strategy

Suggest these search patterns to the user:

```
"[capability] skill" site:github.com
"[capability] SKILL.md" site:github.com
"[capability] claude code" agent
"[capability] opencode" skill
awesome-[domain] site:github.com
```

For each piece of prior art provided:
1. Load `skill-reviewer` in External mode.
2. Score against the review checklist.
3. Classify: adopt / fork / reject.
4. If forking, specify exactly what to change and why.

### Phase 6: Plugins — Decision criteria

Enter plugin development if ANY of these are true:
- User explicitly asks for plugins.
- Multi-agent workflow needs routing/orchestration hooks.
- Quality gates need enforcement before task completion.
- Custom tools are needed beyond the standard tool set.
- Workflow state tracking is needed across agent interactions.

### Phase 7: Delivery — Artifact checklist

| Artifact | Always | Conditional |
|----------|--------|-------------|
| Agent prompt (.md) | Yes | |
| Agent config snippet | Yes | |
| Evaluation report | Yes | |
| Skills (SKILL.md + refs) | | If Phase 5 produced skills |
| Plugin source | | If Phase 6 was executed |
| Installation guide | Yes | |

## Multi-agent workflow patterns

When designing multi-agent systems:

### Sequential pipeline
```
Agent A → Agent B → Agent C
```
Each agent processes and passes to the next. Use when tasks have clear stages.

### Parallel fan-out
```
        ┌── Agent B
Agent A ─┤
        └── Agent C
```
Coordinator dispatches to specialists. Use when tasks can be decomposed.

### Hierarchical delegation
```
Coordinator
├── Planner Agent
├── Builder Agent
└── Reviewer Agent
```
One agent orchestrates others. Use when tasks need planning + execution + review.

### Router pattern
```
Router → [Agent A | Agent B | Agent C] based on input classification
```
A thin routing agent selects the best specialist. Use when inputs vary widely.
