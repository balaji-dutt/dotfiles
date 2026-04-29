// review-loop-gate.js
// Clears the review gate when a REAL PASS is observed in assistant/tool output.
//
// REAL PASS = the terminal meaningful line of some observed text is exactly:
//   <resultMarkerPrefix>=PASS
//
// Listens to both:
//   - tool.execute.after  (covers task/agent calls)
//   - message.updated / message.part.updated  (covers inline assistant output)
//
// Configured via .opencode/opencode-tooling.config.jsonc.
import fs from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";

// Resolve picomatch from ../node_modules/picomatch/ relative to this file.
// In the shared opencode-tooling repo: resolves to <repo-root>/node_modules/picomatch/.
// In a seeded target repo:            resolves to .opencode/node_modules/picomatch/.
const _require = createRequire(import.meta.url);
const picomatch = _require("../node_modules/picomatch/index.js");

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
    try { raw = await fs.readFile(cfgPath, "utf8"); }
    catch { raw = await fs.readFile(legacyCfgPath, "utf8"); }
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
      debugEnvVar: cfg.debugEnvVar || null,
    };
  } catch (err) {
    console.warn(
      `[review-loop-gate] Config load failed (${cfgPath}): ${err.message}. Plugin disabled.`
    );
    return null;
  }
}

// ── Marker detection utilities (config-independent) ───────────────────────────

const REASON_PRIORITY = [
  "mixed-pass-fail-markers",
  "marker-not-terminal",
  "multiple-markers",
  "no-marker-found",
  "no-meaningful-lines",
  "no-strings-found",
  "no-task-payload-candidates",
];

// Return all meaningful lines from a text block (reverse-scan, bottom-up).
// Skips blank lines, <task_metadata> blocks, <task_result> tags, and common
// OpenCode footer noise.
function meaningfulLines(text) {
  if (typeof text !== "string") return [];

  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  let inTaskMetaBlock = false;
  const meaningful = [];

  for (let i = lines.length - 1; i >= 0; i--) {
    const t = lines[i].trim();
    if (!t) continue;

    if (t === "</task_metadata>") { inTaskMetaBlock = true; continue; }
    if (inTaskMetaBlock) {
      if (t === "<task_metadata>") inTaskMetaBlock = false;
      continue;
    }

    if (t === "<task_metadata>" || t === "<task_result>" || t === "</task_result>") continue;
    if (/^task_id:/i.test(t)) continue;
    if (/^to resume:/i.test(t)) continue;
    if (/delegate_task\(/i.test(t)) continue;
    if (t.startsWith("▣")) continue;

    meaningful.push(t);
  }

  return meaningful.reverse();
}

// Recursively collect candidate strings from an event/tool payload.
// Skips keys that commonly contain instruction text (false-positive source).
function collectStrings(x, out = [], depth = 0) {
  if (x == null || depth > 8) return out;

  if (typeof x === "string") { out.push(x); return out; }

  if (Array.isArray(x)) {
    for (const v of x) collectStrings(v, out, depth + 1);
    return out;
  }

  if (typeof x === "object") {
    for (const [k, v] of Object.entries(x)) {
      const lk = String(k).toLowerCase();
      // Don't scan prompt/reason/decision/description/instructions —
      // they frequently quote PASS/FAIL markers and are false-positive sources.
      if (
        lk.includes("prompt") ||
        lk.includes("instruction") ||
        lk === "reason" ||
        lk === "decision" ||
        lk === "description"
      ) continue;
      collectStrings(v, out, depth + 1);
    }
  }

  return out;
}

// Analyse a single text block for a PASS/FAIL marker.
// Returns { marker: "PASS"|"FAIL"|"", reason: string }
function markerFromText(PASS, FAIL, text) {
  if (typeof text !== "string") return { marker: "", reason: "not-a-string" };

  const lines = meaningfulLines(text);
  if (lines.length === 0) return { marker: "", reason: "no-meaningful-lines" };

  const markerLines = lines.filter((l) => l === PASS || l === FAIL);
  if (markerLines.length === 0) return { marker: "", reason: "no-marker-found" };
  if (markerLines.length > 1) return { marker: "", reason: "multiple-markers" };

  const terminal = lines[lines.length - 1];
  if (terminal !== markerLines[0]) return { marker: "", reason: "marker-not-terminal" };
  if (terminal === PASS) return { marker: "PASS", reason: "" };
  if (terminal === FAIL) return { marker: "FAIL", reason: "" };

  return { marker: "", reason: "no-marker-found" };
}

function rejectionReason(reasons) {
  for (const r of REASON_PRIORITY) {
    if (reasons.includes(r)) return r;
  }
  return reasons[0] || "unknown";
}

// Scan all strings in a value for a PASS/FAIL marker, requiring agreement.
function reviewDecision(PASS, FAIL, value) {
  const strings = collectStrings(value, []);
  if (strings.length === 0) return { marker: "", reason: "no-strings-found" };

  const markers = [];
  const reasons = [];

  for (const text of strings) {
    const analysis = markerFromText(PASS, FAIL, text);
    if (analysis.marker) { markers.push(analysis.marker); continue; }
    reasons.push(analysis.reason);
  }

  if (markers.length === 0) return { marker: "", reason: rejectionReason(reasons) };
  if (new Set(markers).size !== 1) return { marker: "", reason: "mixed-pass-fail-markers" };

  return { marker: markers[0], reason: "" };
}

function failureForReason(failures) {
  const reason = rejectionReason(failures.map(({ reason: r }) => r));
  return failures.find((f) => f.reason === reason) || { reason, source: "" };
}

// Scan a tool input+output payload for a PASS/FAIL decision.
function reviewDecisionFromTaskPayload(PASS, FAIL, input, output) {
  const candidates = [
    { source: "output", value: output },
    { source: "input.output", value: input?.output },
    { source: "input.result", value: input?.result },
    { source: "input.response", value: input?.response },
  ];

  const failures = [];

  for (const candidate of candidates) {
    if (candidate.value == null) continue;
    const analysis = reviewDecision(PASS, FAIL, candidate.value);
    if (analysis.marker) {
      return { decision: analysis.marker, reason: "", source: candidate.source };
    }
    failures.push({ reason: analysis.reason, source: candidate.source });
  }

  if (failures.length === 0) {
    return { decision: "", reason: "no-task-payload-candidates", source: "" };
  }

  return { decision: "", ...failureForReason(failures) };
}

// ── Plugin factory ────────────────────────────────────────────────────────────
export default async (ctx) => {
  const baseDir =
    ctx?.worktree || ctx?.project?.worktree || ctx?.directory || process.cwd();

  const cfg = await loadConfig(baseDir);
  if (!cfg) return { event: async () => {}, "tool.execute.after": async () => {} };

  const sentinelDir = path.join(baseDir, ".opencode");
  const { sentinelBase, enforcerStateBase, reviewerAgent, resultMarkerPrefix, reviewLabel, legacyGatePaths, debugEnvVar } = cfg;

  const PASS = `${resultMarkerPrefix}=PASS`;
  const FAIL = `${resultMarkerPrefix}=FAIL`;

  const DEBUG = debugEnvVar
    ? process.env[debugEnvVar] === "1" || process.env[debugEnvVar] === "true"
    : false;

  const logFile = path.join(
    sentinelDir,
    `.${reviewLabel.toLowerCase()}-review-gate.log`
  );

  async function appendDebug(line) {
    if (!DEBUG) return;
    const ts = new Date().toISOString();
    try {
      await fs.mkdir(path.dirname(logFile), { recursive: true });
      await fs.appendFile(logFile, `${ts} ${line}\n`, "utf8");
    } catch {}
  }

  async function exists(p) {
    try {
      await fs.stat(p);
      return true;
    } catch {
      return false;
    }
  }

  // Track session ID so we can clean up session-scoped files on PASS.
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

  async function anyGateExists() {
    // Check unsuffixed sentinel
    if (await exists(path.join(sentinelDir, sentinelBase))) return true;
    // Check session-scoped sentinel
    const sid = sanitizeSessionID(lastSessionID);
    if (sid && await exists(path.join(sentinelDir, `${sentinelBase}.${sid}`))) return true;
    // Check legacy paths
    for (const rel of legacyGatePaths) {
      const abs = path.isAbsolute(rel) ? rel : path.join(baseDir, rel);
      if (await exists(abs)) return true;
    }
    return false;
  }

  async function clearGate(where) {
    await appendDebug(`PASS detected via ${where} -> clearing gate`);

    // Clear unsuffixed sentinel
    await fs.rm(path.join(sentinelDir, sentinelBase), { force: true });

    // Clear session-scoped sentinel and enforcer state file
    const sid = sanitizeSessionID(lastSessionID);
    if (sid) {
      await fs.rm(path.join(sentinelDir, `${sentinelBase}.${sid}`), { force: true });
      await fs.rm(path.join(sentinelDir, `${enforcerStateBase}.${sid}.json`), { force: true });
    }

    // Clear legacy paths
    for (const rel of legacyGatePaths) {
      const abs = path.isAbsolute(rel) ? rel : path.join(baseDir, rel);
      await fs.rm(abs, { force: true });
    }
  }

  // Scan an event object for a PASS/FAIL decision and clear gate if PASS.
  async function scanEventAndClear(where, obj) {
    if (!(await anyGateExists())) return;

    const { marker, reason } = reviewDecision(PASS, FAIL, obj);

    if (marker === "PASS") {
      await clearGate(where);
      return;
    }

    if (DEBUG) {
      const strings = collectStrings(obj, []);
      const sawMarker = strings.some(
        (s) => typeof s === "string" && s.includes(`${resultMarkerPrefix}=`)
      );
      if (sawMarker) {
        const sample =
          strings.find(
            (s) => typeof s === "string" && s.includes(`${resultMarkerPrefix}=`)
          ) || "";
        await appendDebug(
          `marker seen but not REAL PASS via ${where}; marker=${marker}; reason=${reason}; lastLine=${JSON.stringify(
            meaningfulLines(sample).at(-1) ?? ""
          )}`
        );
      }
    }
  }

  // Scan a tool input+output payload for a PASS/FAIL decision.
  async function scanToolAndClear(where, input, output) {
    if (!(await anyGateExists())) return;

    const reviewResult = reviewDecisionFromTaskPayload(PASS, FAIL, input, output);

    if (reviewResult.decision === "PASS") {
      await clearGate(where);
    } else if (DEBUG) {
      const strings = collectStrings(output, []);
      const sawMarker = strings.some(
        (s) => typeof s === "string" && s.includes(`${resultMarkerPrefix}=`)
      );
      if (sawMarker) {
        await appendDebug(
          `marker seen but not REAL PASS via ${where}; decision=${reviewResult.decision}; reason=${reviewResult.reason}`
        );
      }
    }
  }

  await appendDebug(`initialized baseDir=${baseDir}`);

  return {
    event: async ({ event: evt }) => {
      if (!evt?.type) return;
      if (evt.type === "message.updated" || evt.type === "message.part.updated") {
        const sid = extractSessionID(evt);
        if (sid) lastSessionID = sid;
        await scanEventAndClear(`event:${evt.type}`, evt);
      }
    },

    "tool.execute.after": async (input, output) => {
      // Normalize: input.tool may be an object {name, arguments} or a string.
      const tool =
        typeof input?.tool === "string"
          ? input.tool
          : input?.tool?.name || input?.name || "unknown";

      await appendDebug(`tool.execute.after tool=${tool}`);

      // Scope heavy scanning to the reviewer task only — reduces false positives.
      const subtype =
        input?.tool?.arguments?.subagent_type ||
        input?.tool?.args?.subagent_type ||
        input?.arguments?.subagent_type ||
        input?.args?.subagent_type ||
        "";

      if (tool === "task" && subtype && subtype !== reviewerAgent) return;

      await scanToolAndClear(`tool.execute.after:${tool}`, input, output);
    },
  };
};
