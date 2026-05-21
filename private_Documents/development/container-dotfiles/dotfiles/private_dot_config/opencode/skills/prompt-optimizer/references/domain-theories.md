# Domain Theory Catalog

Map agent behavior to established frameworks. Use these to ground requirements
in proven patterns rather than inventing behavior from scratch.

## Workflow & Productivity

### GTD (Getting Things Done)

- **Capture**: Collect all inputs into a trusted system.
- **Clarify**: Determine next action for each input.
- **Organize**: Categorize by context, priority, and project.
- **Reflect**: Regular review of all commitments.
- **Engage**: Execute based on context and energy.

**Apply to**: Task management agents, planning agents, inbox processors.

### Kanban

- **Visualize work**: Make all work items visible.
- **Limit WIP**: Constrain work-in-progress to prevent overload.
- **Pull-based flow**: Start new work only when capacity exists.
- **Measure flow**: Track cycle time and throughput.

**Apply to**: Project coordination agents, pipeline agents, queue processors.

## User Interaction

### BJ Fogg Behavior Model

Behavior = Motivation x Ability x Prompt (B = MAP).

- **Motivation**: Make the desired action appealing.
- **Ability**: Make the desired action easy.
- **Prompt**: Provide a clear trigger at the right moment.

**Apply to**: User-facing agents, onboarding flows, recommendation agents.

### Nielsen's Heuristics (adapted for agents)

1. **Visibility of status**: Tell the user what the agent is doing.
2. **Match real-world language**: Use the user's terminology.
3. **User control**: Let the user abort, undo, or redirect.
4. **Consistency**: Same input should produce same behavior pattern.
5. **Error prevention**: Validate before executing.
6. **Recognition over recall**: Show options, don't require memorization.
7. **Flexibility**: Support both novice and expert interaction styles.
8. **Minimalist output**: No irrelevant information.
9. **Error recovery**: Help users fix mistakes.
10. **Help available**: Provide guidance when requested.

**Apply to**: Any user-facing agent.

## Analysis & Reasoning

### Scientific Method (adapted)

1. **Observe**: Gather data about the problem.
2. **Hypothesize**: Form a testable explanation.
3. **Predict**: State what should happen if the hypothesis is correct.
4. **Test**: Execute the test and collect results.
5. **Conclude**: Accept, reject, or refine the hypothesis.

**Apply to**: Debugging agents, diagnostic agents, research agents.

### Root Cause Analysis (5 Whys)

Ask "why" iteratively until the root cause is found. Typically 5 levels deep.

**Apply to**: Incident response agents, bug analysis agents.

## Creative & Design

### Design Thinking

1. **Empathize**: Understand the user's needs and context.
2. **Define**: Frame the problem clearly.
3. **Ideate**: Generate multiple potential solutions.
4. **Prototype**: Create a minimal version of the best idea.
5. **Test**: Validate with the user.

**Apply to**: Feature design agents, solution proposal agents.

### Diverge/Converge

- **Diverge phase**: Generate many options without judgment.
- **Converge phase**: Evaluate and select the best options.
- Never do both simultaneously.

**Apply to**: Brainstorming agents, option analysis agents.

## Software Engineering

### SOLID Principles (adapted for agent design)

- **Single Responsibility**: Each agent does one thing well.
- **Open/Closed**: Extend via skills, don't modify core prompts.
- **Liskov Substitution**: Agents with the same interface are interchangeable.
- **Interface Segregation**: Don't force agents to handle irrelevant inputs.
- **Dependency Inversion**: Agents depend on abstractions (skills), not
  concrete implementations.

**Apply to**: Multi-agent system design, agent decomposition.

## Selection Guide

| Agent Type   | Primary Theory    | Secondary Theory     |
| ------------ | ----------------- | -------------------- |
| Task manager | GTD               | Kanban               |
| User-facing  | BJ Fogg           | Nielsen's Heuristics |
| Debugger     | Scientific Method | 5 Whys               |
| Designer     | Design Thinking   | Diverge/Converge     |
| Code agent   | SOLID             | Scientific Method    |
| Coordinator  | Kanban            | GTD                  |
