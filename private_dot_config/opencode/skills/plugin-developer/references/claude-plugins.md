# Claude Code Plugin Reference

Detailed reference for building Claude Code plugins (YAML/markdown).

## Plugin Structure

```
plugin-name/
├── .claude-plugin/
│   └── plugin.json           # Plugin metadata
├── agents/                   # Agent definitions
│   └── agent-name.md         # Markdown with YAML frontmatter
├── skills/                   # Skill definitions (optional)
│   └── skill-name.md
├── hooks/                    # Lifecycle hooks (optional)
│   ├── hooks.json            # Hook configuration
│   └── hook-name.md          # Hook implementation
├── han-plugin.yml            # If using Han framework (optional)
└── README.md                 # Documentation
```

## plugin.json

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "What this plugin does",
  "author": "Author Name",
  "license": "MIT"
}
```

## Agent Definition (agents/agent-name.md)

```markdown
---
name: agent-name
description: What this agent specializes in
model: claude-sonnet-4-20250514
tools:
  - read
  - write
  - edit
  - bash
  - glob
  - grep
---

# Agent Name

[Agent system prompt / instructions here]

## Capabilities
- [Capability 1]
- [Capability 2]

## Workflow
1. [Step 1]
2. [Step 2]
```

### Frontmatter fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Kebab-case identifier |
| `description` | Yes | 1-2 sentence purpose |
| `model` | No | Override default model |
| `tools` | No | List of allowed tools |

## Skill Definition (skills/skill-name.md)

Same structure as standalone skills:

```markdown
---
name: skill-name
description: What this skill does
---

# Skill Name

[Skill instructions]
```

## Hooks Configuration (hooks/hooks.json)

```json
{
  "hooks": [
    {
      "name": "ensure-quality",
      "description": "Run quality checks before completion",
      "trigger": "before-completion",
      "implementation": "hook-name.md"
    },
    {
      "name": "ensure-subagent-quality",
      "description": "Run quality checks before subagent completion",
      "trigger": "before-subagent-completion",
      "implementation": "hook-name.md"
    }
  ]
}
```

### Hook triggers

| Trigger | When it fires |
|---------|---------------|
| `before-completion` | Before main agent marks work complete |
| `before-subagent-completion` | Before subagent marks work complete |
| `on-error` | When an error occurs |
| `on-start` | When the agent session starts |

## Hook Implementation (hooks/hook-name.md)

```markdown
---
name: ensure-quality
description: Validate plugin quality before completion
---

# Quality Enforcement

Before marking work as complete, run:

1. `claude plugin validate .` - must pass with 0 errors, 0 warnings.
2. Markdownlint validation on all .md files.
3. [Any other quality checks]

If any check fails, fix the issues before completing.
```

## Validation

```bash
# Validate plugin structure
claude plugin validate .

# Validate a specific plugin
claude plugin validate /path/to/plugin

# Checks performed:
# - Valid YAML frontmatter in all agents/skills
# - Required frontmatter fields present
# - Proper JSON in configuration files
# - Hook configurations match implementation files
```

## Best Practices

- **Single responsibility**: Each agent/skill does one thing well.
- **Kebab-case naming**: All identifiers use kebab-case.
- **Valid frontmatter**: YAML frontmatter with all required fields.
- **Quality hooks**: Use hooks to enforce validation before completion.
- **Comprehensive README**: Document all agents, skills, hooks, and usage.
- **Semantic versioning**: Update version in plugin.json with each release.

## Marketplace Registration

If publishing to a marketplace, add an entry:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "Plugin description",
  "author": "Author",
  "url": "https://github.com/user/plugin",
  "tags": ["tag1", "tag2"]
}
```
