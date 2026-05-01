// review-loop-enforcer.js
// On session.idle, checks for a review sentinel and injects a prompt to run
// the configured reviewer agent. Configured via .opencode/opencode-tooling.config.jsonc.
import { stat, readFile, writeFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

const PLUGIN_VERSION = "1.0.0";

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
function parseJsonc(src) {
  let out = "";
  let i = 0;
  const len = src.length;
  while (i < len) {
    if (src[i] === '"') {
      out += src[i++];
      while (i < len) {
        if (src[i] === "\\" && i + 1 < len) { out += src[i++]; out += src[i++]; continue; }
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
    const isExempt =
      patterns.length > 0 ? picomatch(patterns, { dot: true }) : () => false;
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
      `[review-loop-enforcer] Config load failed (${cfgPath}): ${err.message}. Plugin disabled.`
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

  // Disable with: OPENCODE_ENFORCE_REVIEW=0
  const env = String(process.env.OPENCODE_ENFORCE_REVIEW || "").toLowerCase();
  if (env === "0" || env === "false" || env === "off") {
    return { event: async () => {} };
  }

  const sentinelDir = path.join(baseDir, ".opencode");
  const {
    sentinelBase,
    enforcerStateBase,
    reviewerAgent,
    resultMarkerPrefix,
    reviewLabel,
    legacyGatePaths,
  } = cfg;

  const gateUnsuffixed = path.join(sentinelDir, sentinelBase);

  // Shell-safe single-quote escaping for use in git diff / git status args.
  function shellQuote(s) {
    return "'" + String(s).replace(/'/g, "'\\''") + "'";
  }

  function buildReviewerPrompt(files) {
    const diffLines = files?.length
      ? [
          "Scope the diff to only the files this session edited. Run both:",
          `  git diff -- ${files.map(shellQuote).join(" ")}`,
          `  git diff --cached -- ${files.map(shellQuote).join(" ")}`,
          "Also check for untracked new files:",
          `  git status --short -- ${files.map(shellQuote).join(" ")}`,
        ]
      : [
          "Review ONLY the latest git changes (use git diff and git diff --cached).",
        ];

    return [
      `@${reviewerAgent}`,
      `${reviewLabel} review required.`,
      ...diffLines,
      "End with EXACTLY ONE of the following as the FINAL LINE ONLY:",
      `${resultMarkerPrefix}=PASS`,
      `${resultMarkerPrefix}=FAIL`,
      "",
      "If FAIL: fix Must-fix issues and rerun the agent.",
      "",
    ].join("\n");
  }

  async function readSentinelFiles(gatePath) {
    try {
      const raw = await readFile(gatePath, "utf8");
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed?.files) && parsed.files.length > 0) {
        return parsed.files;
      }
    } catch {}
    return null;
  }

  async function toast(message, variant = "warning") {
    try {
      await ctx.client?.tui?.showToast?.({ body: { message, variant } });
    } catch {}
  }

  async function exists(p) {
    try {
      await stat(p);
      return true;
    } catch {
      return false;
    }
  }

  async function gateMtimeMs(p) {
    const s = await stat(p);
    return s.mtimeMs;
  }

  // ── Session-scoped enforcer state ────────────────────────────────────────
  let lastSessionID = null;

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

  function stateFilePath() {
    const sid = sanitizeSessionID(lastSessionID);
    if (sid) return path.join(sentinelDir, `${enforcerStateBase}.${sid}.json`);
    return path.join(sentinelDir, `${enforcerStateBase}.json`);
  }

  async function loadState() {
    try {
      return JSON.parse(await readFile(stateFilePath(), "utf8"));
    } catch {
      return {};
    }
  }

  async function saveState(next) {
    const sf = stateFilePath();
    await mkdir(path.dirname(sf), { recursive: true });
    await writeFile(sf, JSON.stringify(next, null, 2) + "\n", "utf8");
  }

  async function getGatePath() {
    // Once session ID is known, ONLY the session-scoped sentinel is checked.
    if (lastSessionID) {
      const sid = sanitizeSessionID(lastSessionID);
      if (!sid) return null;
      const scoped = path.join(sentinelDir, `${sentinelBase}.${sid}`);
      if (await exists(scoped)) return scoped;
      return null;
    }
    // No session ID yet — cold-start / backward compat.
    if (await exists(gateUnsuffixed)) return gateUnsuffixed;
    // Also check any legacy gate paths specified in config.
    for (const rel of legacyGatePaths) {
      const abs = path.isAbsolute(rel) ? rel : path.join(baseDir, rel);
      if (await exists(abs)) return abs;
    }
    return null;
  }

  // ── Session metadata cache (for parentID / sub-session guard) ────────────
  const sessionCache = new Map();

  function rememberSession(cache, session) {
    if (!session?.id) return;
    const current = cache.get(session.id) || {};
    cache.set(session.id, {
      ...current,
      id: session.id,
      parentID: session.parentID || null,
      title: session.title || current.title || "",
    });
  }

  function rememberMessage(cache, message) {
    const sessionID = message?.sessionID;
    if (!sessionID) return;
    // NOTE: do NOT seed parentID here — that would trick sessionMeta() into
    // short-circuiting before session.get() confirms the real parentID.
    // Only rememberSession() (called from session.created/session.updated or
    // a successful session.get response) is allowed to write parentID.
    const current = cache.get(sessionID) || { id: sessionID, title: "" };
    if (message?.role === "user" && typeof message?.agent === "string") {
      current.agent = message.agent;
    }
    cache.set(sessionID, current);
  }

  async function sessionMeta(sessionID) {
    if (!sessionID) return null;
    const cached = sessionCache.get(sessionID);
    if (cached && Object.prototype.hasOwnProperty.call(cached, "parentID")) {
      return cached;
    }
    try {
      const response = await ctx.client?.session?.get?.({
        path: { id: sessionID },
      });
      const session = response?.data || response;
      rememberSession(sessionCache, session);
      return sessionCache.get(sessionID) || null;
    } catch {
      return cached || null;
    }
  }

  // Returns false for sub-sessions (parentID set) or when the reviewer agent
  // itself is running — avoids injecting the review prompt into the reviewer.
  async function shouldPromptSession(sessionID) {
    const info = await sessionMeta(sessionID);
    if (!info) return false;
    if (info.parentID) return false;
    if (info.agent === reviewerAgent) return false;
    return true;
  }

  // ── Debounced enforcement ─────────────────────────────────────────────────
  // session.idle is the sole trigger. The debounce absorbs rapid idle/un-idle
  // flaps — it does NOT need to span tool-call gaps.
  const DEBOUNCE_MS = 500;

  let debounceTimer = null;
  let inFlight = false;
  let lastHandledGateMtimeMsMem = 0;

  function scheduleEnforce(trigger) {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      void enforceNow(trigger);
    }, DEBOUNCE_MS);
    debounceTimer.unref?.();
  }

  async function enforceNow(trigger) {
    if (inFlight) return;

    const gatePath = await getGatePath();
    if (!gatePath) return;

    if (!(await shouldPromptSession(lastSessionID))) return;

    const mtime = await gateMtimeMs(gatePath);
    const state = await loadState();
    const lastDisk = Number(state.lastHandledGateMtimeMs || 0);

    // Only run once per gate instance (identified by mtime)
    if (mtime <= lastHandledGateMtimeMsMem || mtime <= lastDisk) return;

    lastHandledGateMtimeMsMem = mtime;
    await saveState({
      ...state,
      lastHandledGateMtimeMs: mtime,
      lastTriggeredAtMs: Date.now(),
      lastTrigger: trigger,
      lastGatePath: gatePath,
      lastSessionID,
    });

    inFlight = true;
    try {
      const files = await readSentinelFiles(gatePath);
      const prompt = buildReviewerPrompt(files);
      if (lastSessionID && ctx.client?.session?.prompt) {
        await toast(
          `${reviewLabel} review required — running ${reviewerAgent}…`,
          "warning"
        );
        await ctx.client.session.prompt({
          path: { id: lastSessionID },
          body: { parts: [{ type: "text", text: prompt }] },
        });
      } else {
        // Fallback: insert prompt for manual enter
        await toast(
          `${reviewLabel} review required — couldn't detect session id. Prompt inserted; press Enter.`,
          "warning"
        );
        try {
          await ctx.client?.tui?.clearPrompt?.();
          await ctx.client?.tui?.appendPrompt?.({ body: { text: prompt } });
        } catch {}
      }
    } finally {
      setTimeout(() => (inFlight = false), 500);
    }
  }

  return {
    event: async ({ event: evt }) => {
      if (!evt?.type) return;

      // Keep sessionID and session metadata fresh from normal chat flow.
      if (evt.type === "message.updated") {
        const sid = extractSessionID(evt);
        if (sid) lastSessionID = sid;
        rememberMessage(sessionCache, evt.properties?.info);
        return;
      }

      if (evt.type === "session.created" || evt.type === "session.updated") {
        rememberSession(sessionCache, evt.properties?.info);
        return;
      }

      // file.edited: sentinel is written by review-loop-marker.js.
      // Enforcement is triggered by session.idle only — triggering on file.edited
      // races against mid-turn tool calls which consume the debounce window.
      if (evt.type === "file.edited") return;

      if (evt.type === "session.idle") {
        if (!(await getGatePath())) return;
        scheduleEnforce("session.idle");
      }
    },
  };
};
