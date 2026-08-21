# MCP Tool Usage

## Codebase Memory (Structural Code Intelligence)

Use codebase-memory-mcp when the task depends on structural relationships:
architecture, module boundaries, definitions or usages across files,
callers/callees, data flow, dependencies, shared code, or transitive impact.
Treat graph results as supporting evidence and verify important findings against
the current source.

Do not invoke CBM for a known-file read, a literal search, an isolated
single-file change with no structural impact, or non-code content that direct
filesystem tools can answer.

### Establish availability

Auto-index is expected, but it can be skipped when repository-root detection or
discovery preflight fails, or when the repository exceeds the configured file
limit. Before structural discovery:

- When `list_projects` and `index_status` are available, call them first.
- Otherwise, call `get_architecture` or `search_graph` and treat an index error
  as the availability signal.
- If the index is missing or stale and `index_repository` is explicitly
  available, request approval and recover with `persistence: false`. Do not
  broaden the current agent's tool permissions to index. Do not use explicit
  recovery to bypass a known file-limit or safety refusal.
- If CBM is unavailable, skipped, over-limit, stale, or incomplete, continue
  with direct repository inspection and state the limitation.

### Build context explicitly

codebase-memory-mcp has no task-to-ranked-files equivalent of
`prepare_context`. Compose task context from the graph:

- In an unfamiliar repository, call `get_graph_schema` once when available, then
  `get_architecture`.
- Use `search_graph` to find relevant symbols and files, then call `trace_path`
  on key entry points.
- Before editing shared templates or scripts, use inbound `trace_path`. If a
  working-tree diff exists, use `detect_changes` instead or as a second check.
- After making changes, use `detect_changes` to assess affected symbols and
  risk.
- When assessing risky areas, inspect the hotspots section of
  `get_architecture`.

When indexing for local-only recovery, set `index_repository`'s `persistence`
parameter to `false`. Without it, CBM exports a repository snapshot and may
modify `.gitattributes` even when `.codebase-memory/` is ignored. Auto-index
uses the cache-local database and does not export repository artifacts.

### Specialized queries

When `query_graph` is permitted, use this read-only query for a dead-code
sweep:

```cypher
MATCH (f:Function)
WHERE NOT EXISTS { (f)<-[:CALLS]-() }
RETURN f
```

Do not make exhaustive negative claims, including dead-code or dependency
absence claims, when index coverage or source verification is unavailable.

Do not run `codebase-memory-mcp install`, `uninstall`, or `update`. Chezmoi owns
the binary and client configuration; the native lifecycle commands would modify
managed files, skills, hooks, and agents.

## DeepWiki (Library Documentation)

You have access to DeepWiki MCP tools for querying documentation of open-source projects.

### When to use DeepWiki tools

- You need API reference or usage patterns for a third-party library.
- The user asks about an external project's capabilities or configuration.
- You need to verify how a framework or tool works before integrating it.

### When NOT to use DeepWiki

- The answer is in the local codebase or project docs.
- General programming concepts covered by your training data.
