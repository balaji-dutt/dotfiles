---
name: plugin-developer
description: Build plugins for OpenCode or Claude Code with agents, hooks,
  custom tools, and proper validation workflows
license: MIT
compatibility: opencode
metadata:
  audience: agent-engineers
  workflow: plugin-development
---

# plugin-developer

Develop plugins that extend AI coding assistants with custom agents, lifecycle
hooks, and tools. Supports both OpenCode (TypeScript plugins) and Claude Code
(YAML/markdown plugins) platforms with automatic detection.

## Use this skill when

- Building a new plugin for OpenCode or Claude Code.
- Adding agents, hooks, or custom tools to an existing plugin.
- Enforcing agentic workflows through plugin hooks.
- Converting a multi-agent workflow design into a plugin implementation.

## Do not use this skill when

- You are creating a standalone skill without hooks/tools (use `skill-creator`).
- You are writing an agent prompt without plugin packaging (use
  `prompt-engineer`).

## Platform detection

Before starting, determine the target platform:

1. **OpenCode**: Check for `.opencode/opencode.jsonc` in the repo root.
2. **Claude Code**: Check for `.claude/` directory or `CLAUDE.md` in the repo.
3. **Both**: If both are present, ask the user which platform to target.

## Plugin architectures

### OpenCode plugins

See `references/opencode-plugins.md` for detailed reference.

**Structure**:
```
plugin-name/
  src/
    index.ts          # Plugin entry point (default export)
  package.json        # npm package metadata
  tsconfig.json       # TypeScript config
```

**Key concepts**:
- Plugin is a TypeScript function returning a config object.
- Config can define: agents, hooks, tools, permissions.
- Agent modes: `primary` (full agent) or `subagent` (delegated tasks).
- Hooks: `event`, `tool.execute.before`, `tool.execute.after`,
  `chat.message`, `experimental.session.compacting`.
- Custom tools: defined with Zod schemas for input validation.
- Distribution: npm packages.

**Constraints**:
- Only one default export per plugin (the Plugin function).
- Subagents cannot spawn other subagents.
- `task()` calls block explore-type agents; avoid in explore context.

### Claude Code plugins

See `references/claude-plugins.md` for detailed reference.

**Structure**:
```
plugin-name/
  .claude-plugin/
    plugin.json       # Plugin metadata
  agents/             # Agent definitions (markdown + YAML frontmatter)
    agent-name.md
  skills/             # Skill definitions (optional)
    skill-name.md
  hooks/              # Lifecycle hooks (optional)
    hooks.json
    hook-name.md
  README.md
```

**Key concepts**:
- Agents and skills are markdown files with YAML frontmatter.
- Hooks enforce quality gates (e.g., run validation before completion).
- `claude plugin validate` validates structure.
- Marketplace integration via marketplace.json.

## Plugin development workflow

### Step 1: Define scope

Answer:
- What agents does this plugin provide?
- What hooks are needed (quality gates, workflow enforcement)?
- What custom tools are required?
- What permissions does the plugin need?

### Step 2: Create structure

Create the directory layout for the target platform. Include all
required configuration files.

### Step 3: Implement agents

For each agent:
1. Define its mode (primary/subagent).
2. Write its prompt (use `prompt-engineer` skill).
3. Configure model, thinking, and tool permissions.
4. Set permissions and constraints.

### Step 4: Implement hooks (if needed)

For workflow enforcement:
1. Identify trigger points (before/after tool execution, on message, etc.).
2. Write hook logic (validation, gating, transformation).
3. Define failure behavior (block, warn, log).

### Step 5: Implement custom tools (if needed)

For each tool:
1. Define the Zod schema for inputs.
2. Implement the tool function.
3. Write a clear description for the agent to understand when to use it.

### Step 6: Validate

- OpenCode: Build and test the plugin locally.
- Claude Code: Run `claude plugin validate .`
- Both: Test all agents, hooks, and tools end-to-end.

### Step 7: Document

Write a README.md covering:
- What the plugin does.
- How to install it.
- How to use each agent/hook/tool.
- Configuration options.

## Output

Deliver the complete plugin directory ready for installation or publishing.
Include all source files, configuration, and documentation.
