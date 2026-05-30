---
description: Review captured OpenCode permission prompts
---

Use the `opencode-permission-review` skill.

Read captured records with `permission_capture_read`, group them into safe and
unsafe permission patterns, and propose scoped `opencode.jsonc` rule changes.
Do not edit config files until the user approves the proposed rules.

After approved edits and validation, clear the capture log with
`permission_capture_clear` using `confirm` set to `clear permission capture`.
