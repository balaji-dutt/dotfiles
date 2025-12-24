<!-- markdownlint-disable MD007 MD022 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

---
description: Manage the project TODO.md list (add, complete, pause, or resume tasks)
---
1. Check if `TODO.md` exists in the project root.
2. If it DOES NOT exist, create it with this EXACT content at the top:
<!-- markdownlint-disable MD007 MD022 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->
3. Add '# TODO' as the title
4. Analyze `$ARGUMENTS` to determine the action:
   - **COMPLETING**: If it contains "done", "complete", or "finished":
     - Find task, change `[ ]` to `[x]`, remove `[PAUSED]`.
     - Append `Completed: $(date '+%Y-%m-%d %H:%M')` to audit log.
     - Execute `~/.claude/commit-todo.sh "complete"`
   - **PAUSING**: If it contains "pause":
     - Find task, prefix with `[PAUSED]`.
     - Append `Paused: $(date '+%Y-%m-%d %H:%M')` to audit log.
     - Execute `~/.claude/commit-todo.sh "pause"`
   - **RESUMING**: If it contains "resume":
     - Find task, remove `[PAUSED]`.
     - Append `Resumed: $(date '+%Y-%m-%d %H:%M')` to audit log.
     - Execute `~/.claude/commit-todo.sh "resume"`
   - **ADDING**: Default case:
     - Append `- [ ] $ARGUMENTS` with `Added: $(date '+%Y-%m-%d %H:%M')`.
     - Execute `~/.claude/commit-todo.sh "add"`
5. **FINAL STEP**: Provide a very brief, one-sentence confirmation (e.g., "Task added and TODO list committed." or "Task paused and TODO list committed"). 
6. **DO NOT** attempt to start working on the task, do not analyze the codebase, and do not suggest next steps. Stop immediately after the confirmation.
