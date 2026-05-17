---
description: Humanize a markdown or prose file with saved voice matching
---

Use the `unslop-file-voice` skill.

Humanize the requested natural-language file with the saved unslop voice profile, or with an explicit `--voice-sample <path>` if the user provided one. Preserve fenced code, inline code, URLs, paths, commands, headings, tables, YAML frontmatter, numbers, versions, errors, and technical terms exactly.

If no file path was provided, ask for one. If no explicit sample was provided, preflight-load saved style memory before invoking `--voice-memory`; the CLI silently ignores missing memory, so a missing profile must stop this command. Tell the user to run `unslop --save-voice-profile samples/my-writing.md`; do not silently run generic `/unslop-file`.
