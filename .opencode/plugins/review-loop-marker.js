// review-loop-marker.js
// Watches for file edits and writes a session-scoped sentinel file to signal
// that a review is required. Configured via .opencode/opencode-tooling.config.jsonc.
import { appendFile, mkdir, writeFile, unlink, readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const PLUGIN_VERSION = "1.1.0";
const DEBUG_ENV_VAR = "DOTFILES_REVIEW_MARKER_DEBUG";

function safeLogName(s) {
  return (
    String(s || "")
      .toLowerCase()
      .replace(/[^a-z0-9._-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "opencode"
  );
}

function isPathInside(child, parent) {
  const rel = path.relative(path.resolve(parent), path.resolve(child));
  return rel === "" || (!!rel && !rel.startsWith("..") && !path.isAbsolute(rel));
}

function stateLogPath(baseDir, reviewLabel, suffix) {
  const defaultStateHome = path.join(os.homedir(), ".local", "state");
  const tmpStateHome = path.join(os.tmpdir(), "opencode-tooling-state");
  const configuredStateHome = process.env.XDG_STATE_HOME;
  let stateHome =
    configuredStateHome && path.isAbsolute(configuredStateHome)
      ? configuredStateHome
      : defaultStateHome;
  if (isPathInside(stateHome, baseDir)) stateHome = defaultStateHome;
  if (isPathInside(stateHome, baseDir)) stateHome = tmpStateHome;
  const repo = safeLogName(path.basename(baseDir));
  const label = safeLogName(reviewLabel);
  return path.join(stateHome, "opencode-tooling", `${repo}-${label}-${suffix}.log`);
}

// Resolve picomatch from ../node_modules/picomatch/ relative to this file.
// In the shared opencode-tooling repo: resolves to <repo-root>/node_modules/picomatch/.
// In a seeded target repo:            resolves to .opencode/node_modules/picomatch/.
const _require = createRequire(import.meta.url);

function loadPicomatch() {
  const candidates = [
    // Seeded target repo path (preferred)
    "../node_modules/picomatch/index.js",
    // Node module resolution fallback (useful during local dev or drift)
    "picomatch",
  ];

  const errors = [];
  for (const spec of candidates) {
    try {
      const mod = _require(spec);
      return mod?.default ?? mod;
    } catch (err) {
      errors.push(`${spec}: ${err?.message || String(err)}`);
    }
  }

  throw new Error(
    `Cannot load picomatch. Tried ${candidates.join(", ")}. ${errors.join(" | ")}`
  );
}

const picomatch = loadPicomatch();

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
  const cfgPath = path.join(baseDir, ".opencode", "opencode-tooling.config.jsonc");
  const legacyCfgPath = path.join(baseDir, ".opencode", "review-loop.config.jsonc");
  try {
    let raw;
    try { raw = await readFile(cfgPath, "utf8"); }
    catch { raw = await readFile(legacyCfgPath, "utf8"); }
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

  const sentinelDir = path.join(baseDir, ".opencode");
  const { sentinelBase, enforcerStateBase, reviewLabel } = cfg;

  const DEBUG = /^(1|true|on)$/i.test(process.env[DEBUG_ENV_VAR] || "");
  const logFile = stateLogPath(baseDir, reviewLabel, "review-marker");

  async function appendDebug(line) {
    if (!DEBUG) return;
    const ts = new Date().toISOString();
    try {
      await mkdir(path.dirname(logFile), { recursive: true });
      await appendFile(logFile, `${ts} [v${PLUGIN_VERSION}] ${line}\n`, "utf8");
    } catch {}
  }

  // Disable with: OPENCODE_MARK_REVIEW=0
  const envMark = (process.env.OPENCODE_MARK_REVIEW || "").toLowerCase();
  if (envMark === "0" || envMark === "false" || envMark === "off") {
    await appendDebug(`disabled by OPENCODE_MARK_REVIEW=${envMark}`);
    return { event: async () => {} };
  }

  // Toast toggle: OPENCODE_MARK_REVIEW_TOAST=0 disables
  const toastEnv = (process.env.OPENCODE_MARK_REVIEW_TOAST || "").toLowerCase();
  const toastEnabled =
    toastEnv !== "0" && toastEnv !== "false" && toastEnv !== "off";

  const TOAST_THROTTLE_MS = 10_000;
  let lastToastAtMs = 0;

  const watchEventSet = new Set(cfg.watchEvents);

  await appendDebug(
    `initialized repo=${path.basename(baseDir)} watchEvents=${[...watchEventSet].join(",")} debugEnv=${DEBUG_ENV_VAR}`
  );

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

  function shortSessionID(sessionID) {
    const sid = sanitizeSessionID(sessionID);
    if (!sid) return "none";
    return sid.length > 12 ? `${sid.slice(0, 8)}…${sid.slice(-4)}` : sid;
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
      basename.endsWith("-review-gate.log") ||
      basename.endsWith("-review-marker.log") ||
      basename.endsWith("-review-enforcer.log") ||
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

  function debugFilePath(p) {
    if (!p) return "unknown file";
    if (!isInsideRepo(p)) return `outside-repo:${path.basename(p) || "unknown"}`;
    return relPath(p);
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
    await appendDebug(
      `marked sentinel=${relPath(p)} session=${shortSessionID(lastSessionID)} fileCount=${payload.files.length}`
    );
    // If we just wrote a scoped sentinel, remove the unsuffixed fallback so
    // cold-start sessions in other windows cannot accidentally consume it.
    if (sid) {
      try {
        await unlink(path.join(sentinelDir, sentinelBase));
        await appendDebug(`removed unsuffixed fallback sentinel=${sentinelBase}`);
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
    if (!file) {
      await appendDebug("skip file event: no file path found");
      return;
    }
    if (!isInsideRepo(file)) {
      await appendDebug(`skip file event: outside repo file=${debugFilePath(file)}`);
      return;
    }
    if (isOpencodeRuntimeArtifact(file)) {
      await appendDebug(`skip file event: opencode runtime artifact file=${debugFilePath(file)}`);
      return;
    }
    if (cfg.isExempt(repoRelLower(file))) {
      await appendDebug(`skip file event: exempt path file=${debugFilePath(file)}`);
      return;
    }
    await appendDebug(`marking file event file=${debugFilePath(file)}`);
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
            await appendDebug(
              `session changed old=${shortSessionID(lastSessionID)} new=${shortSessionID(sid)}; cleared file list`
            );
          }
          lastSessionID = sid;
          if (pendingMarkWithoutSession) {
            pendingMarkWithoutSession = false;
            await appendDebug(
              `backfilling unsuffixed mark into session=${shortSessionID(lastSessionID)}`
            );
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
      await appendDebug(`received event type=${event.type} file=${debugFilePath(file)}`);
      await handleFileChange(file);
    },
  };
};
