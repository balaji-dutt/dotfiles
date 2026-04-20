# OpenCode Plugin Reference

Detailed reference for building OpenCode plugins (TypeScript).

## Plugin Entry Point

```typescript
import { Plugin } from "@anthropic-ai/opencode";

const plugin: Plugin = () => ({
  name: "my-plugin",
  version: "1.0.0",

  // Agent definitions
  agents: {
    "my-agent": {
      mode: "subagent",
      description: "Description for agent selection",
      model: "anthropic/claude-sonnet-4-20250514",
      thinking: {
        type: "enabled",
        budgetTokens: 10000,
      },
      tools: {
        write: true,
        edit: true,
        bash: true,
      },
      prompt: "Agent instructions here...",
    },
  },

  // Lifecycle hooks
  hooks: {
    "tool.execute.before": [
      {
        name: "validate-before-write",
        description: "Validate before file writes",
        match: { tool: "write" },
        handler: async (context) => {
          // Return { abort: true, reason: "..." } to block
          // Return {} to allow
          return {};
        },
      },
    ],
    "tool.execute.after": [
      {
        name: "audit-after-edit",
        description: "Run audit after edits",
        match: { tool: "edit" },
        handler: async (context) => {
          // Post-execution logic
        },
      },
    ],
    "chat.message": [
      {
        name: "on-message",
        description: "Process each chat message",
        handler: async (context) => {
          // Message processing logic
        },
      },
    ],
    event: [
      {
        name: "on-session-start",
        description: "Initialize on session start",
        match: { event: "session.start" },
        handler: async (context) => {
          // Initialization logic
        },
      },
    ],
  },

  // Custom tools
  tools: [
    {
      name: "my-custom-tool",
      description: "What this tool does",
      input: z.object({
        param1: z.string().describe("Description of param1"),
        param2: z.number().optional().describe("Optional param2"),
      }),
      handler: async (input) => {
        // Tool implementation
        return { result: "output" };
      },
    },
  ],

  // Permission overrides
  permissions: {
    bash: {
      "my-safe-command*": "allow",
      "dangerous-command*": "deny",
    },
  },
});

export default plugin;
```

## Agent Configuration Options

```typescript
{
  mode: "primary" | "subagent",
  description: string,              // Used for agent selection
  model: string,                    // e.g., "anthropic/claude-sonnet-4-20250514"
  thinking?: {
    type: "enabled" | "adaptive",
    budgetTokens?: number,          // For type: "enabled"
  },
  output_config?: {
    effort: "low" | "medium" | "high",  // For adaptive thinking
  },
  temperature?: number,             // May be overridden by thinking
  tools?: {
    write?: boolean,
    edit?: boolean,
    bash?: boolean,
    glob?: boolean,
    grep?: boolean,
    read?: boolean,
    task?: boolean,
  },
  permission?: {
    task?: Record<string, "allow" | "deny">,
    bash?: Record<string, "allow" | "deny">,
    skill?: Record<string, "allow" | "deny">,
  },
  prompt?: string,                  // Agent system prompt
}
```

## Hook Types

| Hook | Trigger | Use Case |
|------|---------|----------|
| `event` | Session/lifecycle events | Init, cleanup, state tracking |
| `tool.execute.before` | Before a tool runs | Validation, gating, transformation |
| `tool.execute.after` | After a tool runs | Auditing, post-processing |
| `chat.message` | Each chat message | Logging, routing, context injection |
| `experimental.session.compacting` | Context compaction | Custom summarization |

## Custom Tool Schema (Zod)

```typescript
import { z } from "zod";

// String enum
z.enum(["option1", "option2", "option3"])

// Object with optional fields
z.object({
  required: z.string(),
  optional: z.string().optional(),
  withDefault: z.number().default(10),
  described: z.string().describe("Human-readable description"),
})

// Array
z.array(z.string())

// Union
z.union([z.string(), z.number()])
```

## Distribution

Package as an npm module:

```json
{
  "name": "opencode-plugin-my-plugin",
  "version": "1.0.0",
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "peerDependencies": {
    "@anthropic-ai/opencode": ">=0.1.0"
  }
}
```

Register in `opencode.jsonc`:
```jsonc
{
  "plugin": ["opencode-plugin-my-plugin@1.0.0"]
}
```

## Key Constraints

- Only one default export per plugin.
- Subagents cannot spawn other subagents.
- `task()` calls block explore-type agents; use direct tool calls instead.
- Hooks run synchronously in the plugin pipeline; keep them fast.
- No non-plugin exports from the entry file.
