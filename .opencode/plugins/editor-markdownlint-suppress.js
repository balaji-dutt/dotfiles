// editor-markdownlint-suppress.js
//
// Injects a configurable comment (default: <!-- markdownlint-disable -->) at
// the top of any .md file created in the OS temp directory.  This suppresses
// VSCode markdownlint warnings on the ephemeral files that OpenCode creates
// when the /editor command is invoked.
//
// The watcher/poller is set up at plugin-load time and runs for the lifetime
// of the OpenCode session.  No event hook is needed — /editor is a slash
// command with no corresponding tool or lifecycle event.
//
// Configuration (optional):
//   Add "editorSuppressHeader" to .opencode/opencode-tooling.config.jsonc.
//   Falls back to <!-- markdownlint-disable --> if absent or unreadable.
//
// Opt-out:
//   Set OPENCODE_EDITOR_MD_SUPPRESS=0  (or "false" / "off") to disable.
//
// Debug logging:
//   Set OPENCODE_EDITOR_MD_DEBUG=1 to write diagnostic logs to
//   ~/.local/state/editor-suppress-debug.log (off by default).
//
// Platform support:
//   macOS  — os.tmpdir() returns a symlink (/var/folders/…/T); we resolve it
//            to the realpath (/private/var/folders/…/T) so FSEvents fires.
//            Uses fs.watch + 250ms debounce (VS Code natively reloads on-disk
//            changes, so timing is relaxed).
//   Linux / WSL2 — os.tmpdir() → /tmp (no symlink issue).  Uses polling
//            (readdirSync every 30ms) instead of fs.watch because Bun's
//            fs.watch crashes on protected entries in /tmp (systemd-private
//            dirs → EACCES, MCP sockets → ENXIO).  Injects immediately on
//            detection — no debounce needed.
//   Windows      — os.tmpdir() → user temp dir (no symlink issue)

import { readFile, writeFile } from "node:fs/promises";
import { watch, realpathSync, readdirSync, appendFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const PLUGIN_VERSION = "1.1.0";

const DEFAULT_HEADER = "<!-- markdownlint-disable -->\n";

const isLinux = process.platform === "linux";

// ── Diagnostic log (opt-in via OPENCODE_EDITOR_MD_DEBUG=1) ──────────────────
const DEBUG_ENABLED = /^(1|true|on)$/i.test(process.env.OPENCODE_EDITOR_MD_DEBUG || "");
const DEBUG_LOG = path.join(os.homedir(), ".local", "state", "editor-suppress-debug.log");
function dbg(msg) {
  if (!DEBUG_ENABLED) return;
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  try { appendFileSync(DEBUG_LOG, line); } catch {}
}

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
  dbg(`v${PLUGIN_VERSION} factory called. platform=${process.platform} runtime=${typeof Bun !== "undefined" ? "bun" : "node"}`);
  const enabled = !/^(0|false|off)$/i.test(
    process.env.OPENCODE_EDITOR_MD_SUPPRESS || ""
  );
  if (!enabled) { dbg("Plugin DISABLED via env var"); return { event: async () => {} }; }

  const baseDir =
    ctx?.worktree || ctx?.project?.worktree || ctx?.directory || process.cwd();
  dbg(`baseDir=${baseDir}`);

  const header = await loadHeader(baseDir);

  // ── Dedup guard ───────────────────────────────────────────────────────────
  const processed = new Set();
  const pending = new Set();
  const timers = new Map();

  function markProcessed(p) {
    processed.add(p);
    if (processed.size > 200) {
      processed.delete(processed.values().next().value);
    }
  }

  // ── Injection (shared by both watcher and poller paths) ───────────────────
  async function inject(filePath) {
    if (processed.has(filePath)) return;
    if (pending.has(filePath)) return;
    pending.add(filePath);
    dbg(`inject START: ${filePath}`);

    try {
      for (let attempt = 0; attempt < 5; attempt++) {
        const delay = isLinux && attempt === 0 ? 5 : 60 * (attempt + 1);
        await new Promise((r) => setTimeout(r, delay));
        try {
          const existing = await readFile(filePath, "utf8");
          if (existing.startsWith("<!-- markdownlint-disable")) {
            dbg(`inject SKIP (already has header)`);
            markProcessed(filePath);
            return;
          }
          await writeFile(filePath, header + existing, "utf8");
          dbg(`inject OK: wrote header + ${existing.length} bytes`);
          markProcessed(filePath);
          return;
        } catch (e) {
          dbg(`inject attempt=${attempt} ERROR: ${e.message}`);
        }
      }
      dbg(`inject EXHAUSTED all attempts for ${filePath}`);
    } finally {
      pending.delete(filePath);
    }
  }

  // ── macOS: fs.watch (FSEvents — stable, no /tmp permission issues) ────────
  function scheduleInject(filePath) {
    if (processed.has(filePath)) return;
    const existingTimer = timers.get(filePath);
    if (existingTimer) clearTimeout(existingTimer);

    const timer = setTimeout(() => {
      timers.delete(filePath);
      void inject(filePath);
    }, 250);
    timers.set(filePath, timer);
  }

  function watchDir(dir) {
    try {
      const watcher = watch(dir, { persistent: false }, (event, filename) => {
        if (!filename) return;
        if (!filename.endsWith(".md")) return;
        if (event !== "rename" && event !== "change") return;
        scheduleInject(path.join(dir, filename));
      });
      watcher.on("error", () => {});
      dbg(`fs.watch set up on ${dir}`);
    } catch {
      // Directory may not exist on this platform — ignore.
    }
  }

  // ── Linux: polling (Bun's fs.watch crashes on protected /tmp entries) ─────
  function pollDir(dir) {
    const seen = new Set();
    // Seed with existing .md files so we don't inject into stale files.
    try {
      for (const f of readdirSync(dir)) {
        if (f.endsWith(".md")) seen.add(f);
      }
    } catch {}

    dbg(`Polling ${dir} every 30ms (seeded ${seen.size} existing .md files)`);

    setInterval(() => {
      try {
        const entries = readdirSync(dir);
        for (const f of entries) {
          if (!f.endsWith(".md")) continue;
          if (seen.has(f)) continue;
          seen.add(f);
          dbg(`Poll detected new .md: ${f}`);
          void inject(path.join(dir, f));
        }
      } catch {}
    }, 30);
  }

  // ── Resolve and start watching/polling ────────────────────────────────────
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
    if (isLinux) {
      pollDir(dir);
    } else {
      watchDir(dir);
    }
  }

  dbg(`v${PLUGIN_VERSION} setup complete. ${isLinux ? "Polling" : "Watching"} ${watchDirs.size} dir(s)`);
  return { event: async () => {} };
};
