---
name: unslop-file-voice
description: >
  Humanize a markdown or prose file with unslop-file while matching the saved numeric voice profile.
  Use when the user asks for /unslop-file-voice, voice-matched file cleanup, or a document rewrite that should sound like their own writing.
  Requires an existing unslop voice profile or an explicit --voice-sample path; do not silently fall back to generic voice.
---

# Unslop File Voice

Rewrite a natural-language file with `unslop-file` and opt in to voice matching. This is separate from `unslop-file` because it uses personal style memory and should never be the invisible default.

## Use this skill when

- The user runs `/unslop-file-voice <filepath>`.
- The user asks to humanize a file and match their saved voice profile.
- The user provides `--voice-sample <path>` for a one-off file rewrite.

## Do not use this skill when

- The user asks for deterministic/no-network cleanup. Voice matching is LLM-only.
- The file is code, config, secrets, credentials, lockfiles, or any sensitive path.
- No saved voice profile or explicit voice sample is available. Ask the user to create/provide one instead of doing a generic rewrite.

## Voice source order

1. If the user supplied `--voice-sample <path>`, use that sample for this run.
2. Otherwise look for a saved numeric style profile without printing its contents:
   - `$UNSLOP_STYLE_MEMORY`
   - `$XDG_CONFIG_HOME/unslop/style-memory.json`
   - `~/.config/unslop/style-memory.json`
   - `%APPDATA%/unslop/style-memory.json` on Windows
3. If no profile exists, stop and tell the user to run:

```bash
unslop --save-voice-profile samples/my-writing.md
```

The saved profile stores numeric style targets only. It does not store prose samples.

## Process

Prefer the `scripts/` directory from the sibling `unslop-file` skill.

Common layouts:

- `skills/unslop-file-voice/SKILL.md` plus `skills/unslop-file/scripts/`
- `~/.config/opencode/skills/unslop-file-voice/SKILL.md` plus `~/.config/opencode/skills/unslop-file/scripts/`
- Installed CLI fallback: `unslop`

Steps:

1. Confirm the target is `.md`, `.markdown`, `.txt`, `.rst`, or extensionless prose.
2. Refuse sensitive files and non-prose/code/config formats using the same boundaries as `unslop-file`.
3. Resolve the voice source. Do not print profile JSON or sample text.
4. If using saved memory, fail closed with a preflight load check before rewriting. From the `unslop-file` skill directory, run:

```bash
python3 - <<'PY'
from scripts.style_memory import StyleMemoryError, load_profile

try:
    profile = load_profile()
except StyleMemoryError:
    profile = None
if profile is None:
    raise SystemExit(
        "no unslop style memory found; run "
        "unslop --save-voice-profile samples/my-writing.md first"
    )
PY
```

If this check exits non-zero, stop. Do not run `--voice-memory`, because the CLI treats missing memory as a generic rewrite fallback.
5. Run LLM mode with voice matching:

```bash
python3 -m scripts --voice-memory --mode full <absolute_filepath>
```

For a one-off sample:

```bash
python3 -m scripts --voice-sample <sample_path> --mode full <absolute_filepath>
```

If only an installed CLI is available, use the same flags with `unslop`.

6. Preserve fenced code, inline code, URLs, paths, commands, headings, tables, YAML frontmatter, numbers, versions, and errors exactly.
7. Report the rewritten file path, backup path, mode, and which voice source type was used: saved profile or explicit sample. Do not reveal profile contents.

## Defaults

- Default intensity: `--mode full`.
- If the user explicitly asks for `subtle` or `balanced`, pass that `--mode` instead.
- Do not pass `--deterministic`; it ignores voice matching.
- Do not use exported markdown profiles (`unslop-voice-profile.md`) for the CLI run. The CLI expects saved JSON style memory or a prose sample path.

## Boundaries

- This skill changes files in place and writes `FILE.original.md` backup first.
- If validation fails, report the error and leave the original restored per `unslop-file` behavior.
- Voice match means cadence, register, punctuation, contraction rate, and sentence rhythm. It does not mean inventing facts, preferences, biography, opinions, or emotional tone.
