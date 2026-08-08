# MCP Tool Usage

## Codebase Memory (Structural Code Intelligence)

Use codebase-memory-mcp for structural analysis of indexed repositories. Treat
graph results as supporting evidence and verify important findings against the
current source.

### Build context explicitly

codebase-memory-mcp has no task-to-ranked-files equivalent of
`prepare_context`. Compose task context from the graph:

- In an unfamiliar repository, call `get_graph_schema` once, then
  `get_architecture`.
- For a non-trivial task, use `search_graph` to find relevant symbols and
  files, then call `trace_path` on key entry points.
- Before editing shared templates or scripts, use inbound `trace_path`. If a
  working-tree diff exists, use `detect_changes` instead or as a second check.
- After making changes, use `detect_changes` to assess affected symbols and
  risk.
- When assessing risky areas, inspect the hotspots section of
  `get_architecture`.

When indexing for local-only use, set `index_repository`'s `persistence`
parameter to `false`. Without it, CBM exports a repository snapshot and may
modify `.gitattributes` even when `.codebase-memory/` is ignored.

### Specialized queries

When `query_graph` is permitted, use this read-only query for a dead-code
sweep:

```cypher
MATCH (f:Function)
WHERE NOT EXISTS { (f)<-[:CALLS]-() }
RETURN f
```

Do not claim exhaustive dead code when `query_graph`, index coverage, or source
verification is unavailable.

### When not to use Codebase Memory

- A grep or glob answers the question directly.
- The change is isolated and has no structural impact.
- The repository is not indexed and indexing is outside the agent's
  permissions.

Do not run `codebase-memory-mcp install`. Chezmoi owns client configuration;
the native installer would modify managed files, skills, hooks, and agents.

## DeepWiki (Library Documentation)

You have access to DeepWiki MCP tools for querying documentation of open-source projects.

### When to use DeepWiki tools

- You need API reference or usage patterns for a third-party library.
- The user asks about an external project's capabilities or configuration.
- You need to verify how a framework or tool works before integrating it.

### When NOT to use DeepWiki

- The answer is in the local codebase or project docs.
- General programming concepts covered by your training data.
