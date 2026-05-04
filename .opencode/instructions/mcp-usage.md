# MCP Tool Usage

## TempoGraph (Code Graph Analysis)

You have access to TempoGraph MCP tools for structural dependency analysis of this codebase.
TempoGraph parses code with tree-sitter into a dependency graph stored in SQLite.

### When to use TempoGraph tools

- **Before editing shared templates or scripts:** Use `tempograph_blast_radius` to check what else might break.
- **Starting a non-trivial task:** Use `tempograph_prepare_context` with a task description to get the relevant files.
- **Exploring an unfamiliar area:** Use `tempograph_focus` to see everything related to a concept.
- **After making changes:** Use `tempograph_diff_context` to verify no unintended impacts.
- **Assessing risk:** Use `tempograph_hotspots` to identify the riskiest files to change.

### When NOT to use TempoGraph

- A simple grep or glob will find what you need.
- Single-file edits with no cross-cutting concerns.
- Docs-only changes with no template dependencies.

## DeepWiki (Library Documentation)

You have access to DeepWiki MCP tools for querying documentation of open-source projects.

### When to use DeepWiki tools

- You need API reference or usage patterns for a third-party library.
- The user asks about an external project's capabilities or configuration.
- You need to verify how a framework or tool works before integrating it.

### When NOT to use DeepWiki

- The answer is in the local codebase or project docs.
- General programming concepts covered by your training data.
