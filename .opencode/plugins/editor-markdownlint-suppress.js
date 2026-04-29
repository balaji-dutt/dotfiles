// editor-markdownlint-suppress.js
//
// Injects a configurable comment (default: <!-- markdownlint-disable -->) at
// the top of any .md file created in the OS temp directory.  This suppresses
// VSCode markdownlint warnings on the ephemeral files that OpenCode creates
// when the /editor command is invoked.
//
// The watcher is set up at plugin-load time and runs for the lifetime of the
// OpenCode session.  No event hook is needed — /editor is a slash command with
// no corresponding tool or lifecycle event.
//
// Configuration (optional):
//   Add "editorSuppressHeader" to .opencode/opencode-tooling.config.jsonc.
//   Falls back to <!-- markdownlint-disable --> if absent or unreadable.
//
// Opt-out:
//   Set OPENCODE_EDITOR_MD_SUPPRESS=0  (or "false" / "off") to disable.
//
// Platform support:
//   macOS  — os.tmpdir() returns a symlink (/var/folders/…/T); we resolve it
//            to the realpath (/private/var/folders/…/T) so FSEvents fires.
//   Linux / WSL2 — os.tmpdir() → /tmp (no symlink issue)
//   Windows      — os.tmpdir() → user temp dir (no symlink issue)

import { readFile, writeFile } from "node:fs/promises";
import { watch, realpathSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const DEFAULT_HEADER = "<!-- markdownlint-disable -->\n";

// ── JSONC parser (minimal — strips // and /* */ comments, trailing commas) ───
function parseJsonc(src) {
  let out = "";
  let i = 0;
  const len = src.length;
  while (i < len) {
    if (src[i] === '"') {
      out += src[i++];
      while (i < len) {
        if (src[i] === "\\" && i + 1 < len) {
          out += src[i++];
          out += src[i++];
          continue;
        }
        if (src[i] === '"') { out += src[i++]; break; }
        out += src[i++];
      }
    } else if (src[i] === "/" && i + 1 < len && src[i + 1] === "/") {
      while (i < len && src[i] !== "\n") i++;
    } else if (src[i] === "/" && i + 1 < len && src[i + 1] === "*") {
      i += 2;
      while (i < len && !(src[i] === "*" && i + 1 < len && src[i + 1] === "/")) i++;
      i += 2;
    } else {
      out += src[i++];
    }
  }
  out = out.replace(/,(\s*[}\]])/g, "$1");
  return JSON.parse(out);
}

// ── Config loader ─────────────────────────────────────────────────────────────
async function loadHeader(baseDir) {
  const cfgPath = path.join(baseDir, ".opencode", "opencode-tooling.config.jsonc");
  const legacyCfgPath = path.join(baseDir, ".opencode", "review-loop.config.jsonc");
  try {
    let raw;
    try { raw = await readFile(cfgPath, "utf8"); }
    catch { raw = await readFile(legacyCfgPath, "utf8"); }
    const cfg = parseJsonc(raw);
    if (typeof cfg.editorSuppressHeader === "string" && cfg.editorSuppressHeader.trim()) {
      const h = cfg.editorSuppressHeader;
      return h.endsWith("\n") ? h : h + "\n";
    }
  } catch {
    // Config absent or unreadable — use default.
  }
  return DEFAULT_HEADER;
}

// ── Plugin factory ────────────────────────────────────────────────────────────
export default async (ctx = {}) => {
  const enabled = !/^(0|false|off)$/i.test(
    process.env.OPENCODE_EDITOR_MD_SUPPRESS || ""
  );
  if (!enabled) return { event: async () => {} };

  const baseDir =
    ctx?.worktree || ctx?.project?.worktree || ctx?.directory || process.cwd();

  const header = await loadHeader(baseDir);

  // ── Dedup guard ───────────────────────────────────────────────────────────
  // Prevents double-injection when multiple watch events fire for the same file.
  const processed = new Set();

  function markProcessed(p) {
    processed.add(p);
    if (processed.size > 200) {
      processed.delete(processed.values().next().value);
    }
  }

  // ── Injection ─────────────────────────────────────────────────────────────
  async function inject(filePath) {
    if (processed.has(filePath)) return;
    markProcessed(filePath);
    try {
      // Brief pause: let the writer finish flushing the file before we read it.
      await new Promise((r) => setTimeout(r, 60));
      const existing = await readFile(filePath, "utf8");
      // Idempotent: skip if any markdownlint-disable comment is already present.
      if (existing.startsWith("<!-- markdownlint-disable")) return;
      await writeFile(filePath, header + existing, "utf8");
    } catch {
      // File may not yet be flushed, or was already deleted — ignore silently.
    }
  }

  // ── Watcher factory ───────────────────────────────────────────────────────
  function watchDir(dir) {
    try {
      const watcher = watch(dir, { persistent: false }, (event, filename) => {
        if (!filename) return;
        if (!filename.endsWith(".md")) return;

        // "rename" fires on file creation/deletion on all platforms.
        // "change" also accepted defensively (Bun behaviour can vary).
        if (event !== "rename" && event !== "change") return;

        void inject(path.join(dir, filename));
      });
      watcher.on("error", () => {});
    } catch {
      // Directory may not exist on this platform — ignore.
    }
  }

  // Resolve all candidate temp dirs to their realpaths before watching.
  // macOS: os.tmpdir() → /var/folders/…/T (symlink) but FSEvents fires on
  //        the realpath /private/var/folders/…/T, so we must watch that.
  const rawDirs = new Set([os.tmpdir()]);
  if (process.platform === "darwin") {
    rawDirs.add("/private/tmp");
    rawDirs.add("/tmp");
  }

  const watchDirs = new Set();
  for (const dir of rawDirs) {
    try {
      watchDirs.add(realpathSync(dir));
    } catch {
      // Dir doesn't exist on this platform — skip.
    }
  }

  for (const dir of watchDirs) {
    watchDir(dir);
  }

  // Return a no-op event hook so OpenCode's plugin system is satisfied.
  return { event: async () => {} };
};
