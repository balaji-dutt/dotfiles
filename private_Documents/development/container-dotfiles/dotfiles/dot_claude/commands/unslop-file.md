---
description: Humanize a markdown or prose file with unslop-file
---

Use the `unslop-file` skill.

Humanize the requested natural-language file. Preserve fenced code, inline code, URLs, paths, commands, headings, tables, YAML frontmatter, numbers, and technical terms exactly.

If no file path was provided with the command, ask for one. If the skill's sibling `scripts/` directory is available, prefer `python3 -m scripts <absolute_filepath>` from the skill directory. Otherwise, use an installed `unslop` CLI when available.
