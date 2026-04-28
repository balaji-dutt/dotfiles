// review-loop-marker.js
// Watches for file edits and writes a session-scoped sentinel file to signal
// that a review is required. Configured via .opencode/review-loop.config.jsonc.
import { mkdir, writeFile, unlink, readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

// Resolve picomatch from ../node_modules/picomatch/ relative to this file.
// In the shared opencode-tooling repo: resolves to <repo-root>/node_modules/picomatch/.
// In a seeded target repo:            resolves to .opencode/node_modules/picomatch/.
const _require = createRequire(import.meta.url);
const picomatch = _require("../node_modules/picomatch/index.js");

// ── JSONC parser (comments + trailing commas) ─────────────────────────────────
// Strips // line comments, /* block comments */, and trailing commas before } or ].
// Handles escaped characters inside strings correctly.
// Limitation: a string value literally containing ,} or ,] would be corrupted —
// this is not a realistic concern for config files of this shape.
function parseJsonc(src) {
  let out = "";
  let i = 0;
  const len = src.length;
  while (i < len) {
    if (src[i] === '"') {
      // String literal — copy verbatim until closing unescaped quote
      out += src[i++];
      while (i < len) {
        if (src[i] === "\\" && i + 1 < len) { out += src[i++]; out += src[i++]; continue; }
        if (src[i] === '"') { out += src[i++]; break; }
        out += src[i++];
      }
    } else if (src[i] === "/" && i + 1 < len && src[i + 1] === "/") {
      // Line comment — skip to end of line
      while (i < len && src[i] !== "\n") i++;
    } else if (src[i] === "/" && i + 1 < len && src[i + 1] === "*") {
      // Block comment — skip to */
      i += 2;
      while (i < len && !(src[i] === "*" && i + 1 < len && src[i + 1] === "/")) i++;
      i += 2;
    } else {
      out += src[i++];
    }
  }
  // Strip trailing commas before } or ]
  out = out.replace(/,(\s*[}\]])/g, "$1");
  return JSON.parse(out);
}

// ── Config loader ─────────────────────────────────────────────────────────────
async function loadConfig(baseDir) {
  const cfgPath = path.join(baseDir, ".opencode", "review-loop.config.jsonc");
  try {
    const raw = await readFile(cfgPath, "utf8");
    const cfg = parseJsonc(raw);
    const required = [
      "reviewerAgent",
      "resultMarkerPrefix",
      "sentinelBase",
      "enforcerStateBase",
      "reviewLabel",
    ];
    for (const k of required) {
      if (typeof cfg[k] !== "string" || !cfg[k]) {
        throw new Error(`missing or empty required field "${k}"`);
      }
    }
    const patterns = Array.isArray(cfg.exemptPaths) ? cfg.exemptPaths : [];
    // Split into positive and negation patterns. picomatch treats a standalone
    // negation like "!docs/agents/**" as "everything that is NOT that path",
    // which would make isExempt return true for all other files. Instead,
    // build two separate matchers: a file is exempt if it matches a positive
    // pattern AND does NOT match any negation pattern.
    const positivePatterns = patterns.filter((p) => !p.startsWith("!"));
    const negationPatterns = patterns
      .filter((p) => p.startsWith("!"))
      .map((p) => p.slice(1));
    const matchPositive =
      positivePatterns.length > 0
        ? picomatch(positivePatterns, { dot: true })
        : () => false;
    const matchNegation =
      negationPatterns.length > 0
        ? picomatch(negationPatterns, { dot: true })
        : () => false;
    const isExempt = (p) => matchPositive(p) && !matchNegation(p);
    return {
      ...cfg,
      isExempt,
      watchEvents: Array.isArray(cfg.watchEvents)
        ? cfg.watchEvents
        : ["file.edited"],
      legacyGatePaths: Array.isArray(cfg.legacyGatePaths)
        ? cfg.legacyGatePaths
        : [],
    };
  } catch (err) {
    console.warn(
      `[review-loop-marker] Config load failed (${cfgPath}): ${err.message}. Plugin disabled.`
    );
    return null;
  }
}

// ── Plugin factory ────────────────────────────────────────────────────────────
export default async (ctx = {}) => {
  const baseDir =
    ctx?.worktree || ctx?.project?.worktree || ctx?.directory || process.cwd();

  const cfg = await loadConfig(baseDir);
  if (!cfg) return { event: async () => {} };

  // Disable with: OPENCODE_MARK_REVIEW=0
  const envMark = (process.env.OPENCODE_MARK_REVIEW || "").toLowerCase();
  if (envMark === "0" || envMark === "false" || envMark === "off") {
    return { event: async () => {} };
  }

  // Toast toggle: OPENCODE_MARK_REVIEW_TOAST=0 disables
  const toastEnv = (process.env.OPENCODE_MARK_REVIEW_TOAST || "").toLowerCase();
  const toastEnabled =
    toastEnv !== "0" && toastEnv !== "false" && toastEnv !== "off";

  const TOAST_THROTTLE_MS = 10_000;
  let lastToastAtMs = 0;

  const sentinelDir = path.join(baseDir, ".opencode");
  const { sentinelBase, enforcerStateBase, reviewLabel } = cfg;
  const watchEventSet = new Set(cfg.watchEvents);

  // Session tracking: scoped sentinels prevent cross-session interference.
  let lastSessionID = null;
  const editedFiles = new Set();
  let pendingMarkWithoutSession = false;

  function extractSessionID(evt) {
    return (
      evt?.properties?.sessionID ||
      evt?.properties?.info?.sessionID ||
      evt?.properties?.info?.sessionId ||
      null
    );
  }

  function sanitizeSessionID(sessionID) {
    return String(sessionID || "")
      .trim()
      .replace(/[^A-Za-z0-9._-]/g, "_");
  }

  function sentinelPath(sessionID) {
    const sid = sanitizeSessionID(sessionID);
    if (sid) return path.join(sentinelDir, `${sentinelBase}.${sid}`);
    return path.join(sentinelDir, sentinelBase);
  }

  function normalize(p) {
    return String(p || "").replace(/\\/g, "/");
  }
  function normalizeLower(p) {
    return normalize(p).toLowerCase();
  }

  const baseNorm = normalize(baseDir);
  const baseNormLower = normalizeLower(baseDir);
  const isWin = process.platform === "win32";

  function isInsideRepo(p) {
    if (!p) return false;
    if (path.isAbsolute(p)) {
      const nl = normalizeLower(p);
      return isWin
        ? nl.startsWith(baseNormLower)
        : normalize(p).startsWith(baseNorm);
    }
    return true;
  }

  // Excludes runtime sentinel/state artifacts written by the plugins themselves.
  // Source files under .opencode/ (plugins/, skills/, commands/) are committed
  // dotfiles and should be tracked and reviewed normally.
  function isOpencodeRuntimeArtifact(p) {
    const n = normalizeLower(p);
    if (!n.includes("/.opencode/") && !n.startsWith(".opencode/")) return false;
    const basename = path.basename(p).toLowerCase();
    return (
      basename.startsWith(sentinelBase.toLowerCase()) ||
      basename.startsWith(enforcerStateBase.toLowerCase()) ||
      basename === ".ds_store" ||
      n.startsWith(".opencode/node_modules/") ||
      n.includes("/.opencode/node_modules/")
    );
  }

  function relPath(p) {
    if (!p) return "unknown file";
    if (!path.isAbsolute(p)) return normalize(p);
    try {
      const rel = path.relative(baseDir, p);
      return rel && rel !== "" ? normalize(rel) : normalize(p);
    } catch {
      return normalize(p);
    }
  }

  function repoRelLower(p) {
    return normalizeLower(relPath(p).replace(/^\.\//, ""));
  }

  function extractFileFromEvent(event) {
    return (
      event?.path ||
      event?.properties?.file ||
      event?.properties?.path ||
      event?.properties?.name ||
      event?.properties?.oldPath ||
      event?.properties?.newPath ||
      event?.file ||
      event?.filePath ||
      ""
    );
  }

  async function mark(file) {
    if (file) {
      const rel = relPath(file);
      if (rel && rel !== "unknown file") editedFiles.add(rel);
    }
    const sid = sanitizeSessionID(lastSessionID);
    const p = sentinelPath(lastSessionID);
    pendingMarkWithoutSession = !sid;
    await mkdir(path.dirname(p), { recursive: true });
    const payload = {
      timestamp: Math.floor(Date.now() / 1000),
      sessionID: lastSessionID || null,
      files: [...editedFiles],
    };
    await writeFile(p, JSON.stringify(payload, null, 2) + "\n", "utf8");
    // If we just wrote a scoped sentinel, remove the unsuffixed fallback so
    // cold-start sessions in other windows cannot accidentally consume it.
    if (sid) {
      try {
        await unlink(path.join(sentinelDir, sentinelBase));
      } catch {}
    }
  }

  async function maybeToast(message) {
    if (!toastEnabled) return;
    const now = Date.now();
    if (now - lastToastAtMs < TOAST_THROTTLE_MS) return;
    lastToastAtMs = now;
    try {
      await ctx.client?.tui?.showToast?.({ body: { message, variant: "info" } });
    } catch {}
  }

  async function markAndToast(file) {
    await mark(file);
    await maybeToast(`Marked for ${reviewLabel} review: ${relPath(file)}`);
  }

  async function handleFileChange(file) {
    if (!file) return;
    if (!isInsideRepo(file)) return;
    if (isOpencodeRuntimeArtifact(file)) return;
    if (cfg.isExempt(repoRelLower(file))) return;
    await markAndToast(file);
  }

  return {
    event: async ({ event }) => {
      if (!event?.type) return;

      // Track session ID so sentinel files are session-scoped.
      if (event.type === "message.updated") {
        const sid = extractSessionID(event);
        if (sid) {
          if (lastSessionID && sid !== lastSessionID) {
            // New session — discard file list from prior session.
            editedFiles.clear();
          }
          lastSessionID = sid;
          if (pendingMarkWithoutSession) {
            pendingMarkWithoutSession = false;
            await mark();
            await maybeToast(
              `Marked for ${reviewLabel} review: pending edit in current session`
            );
          }
        }
        return;
      }

      if (!watchEventSet.has(event.type)) return;

      const file = extractFileFromEvent(event);
      await handleFileChange(file);
    },
  };
};
