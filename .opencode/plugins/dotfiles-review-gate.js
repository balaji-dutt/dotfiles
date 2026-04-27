// .opencode/plugins/dotfiles-review-gate.js
// Clears the dotfiles review gate when a REAL PASS is observed.
//
// REAL PASS = the terminal meaningful line of some observed assistant/tool text is exactly:
//   DOTFILES_REVIEWER_RESULT=PASS
//
// This listens to BOTH:
// - tool.execute.after (covers background_output / call_omo_agent / etc)
// - message.updated / message.part.updated (via the generic `event` hook)

import fs from "node:fs/promises";
import path from "node:path";

const PASS = "DOTFILES_REVIEWER_RESULT=PASS";
const FAIL = "DOTFILES_REVIEWER_RESULT=FAIL";

const REASON_PRIORITY = [
  "mixed-pass-fail-markers",
  "marker-not-terminal",
  "multiple-markers",
  "no-marker-found",
  "no-meaningful-lines",
  "no-strings-found",
  "no-task-payload-candidates",
];

const DEBUG =
  process.env.DOTFILES_REVIEW_GATE_DEBUG === "1" ||
  process.env.DOTFILES_REVIEW_GATE_DEBUG === "true";

async function exists(p) {
  try {
    await fs.stat(p);
    return true;
  } catch {
    return false;
  }
}

async function appendDebug(baseDir, line) {
  if (!DEBUG) return;
  const logPath = path.join(baseDir, ".opencode", ".dotfiles-review-gate.log");
  const ts = new Date().toISOString();
  try {
    await fs.mkdir(path.dirname(logPath), { recursive: true });
    await fs.appendFile(logPath, `${ts} ${line}\n`, "utf8");
  } catch {
    // ignore logging failures
  }
}

// Return all meaningful lines from a text block (reverse-scan, bottom-up).
// Skips blank lines, <task_metadata> blocks, <task_result> tags, common
// OpenCode footer noise, and task_id: prefixed lines.
function meaningfulLines(text) {
  if (typeof text !== "string") return [];

  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  let inTaskMetaBlock = false;
  const meaningful = [];

  for (let i = lines.length - 1; i >= 0; i--) {
    const t = lines[i].trim();
    if (!t) continue;

    if (t === "</task_metadata>") {
      inTaskMetaBlock = true;
      continue;
    }
    if (inTaskMetaBlock) {
      if (t === "<task_metadata>") inTaskMetaBlock = false;
      continue;
    }

    // Skip standalone metadata/result tags
    if (t === "<task_metadata>" || t === "<task_result>" || t === "</task_result>") continue;

    // Skip common OpenCode task trailers
    if (/^task_id:/i.test(t)) continue;
    if (/^to resume:/i.test(t)) continue;
    if (/delegate_task\(/i.test(t)) continue;
    if (t.startsWith("▣")) continue;

    meaningful.push(t);
  }

  return meaningful.reverse();
}

// Recursively collect candidate strings.
// Skip keys that commonly contain instruction/metadata text (false-positive source).
function collectStrings(x, out = [], depth = 0, key = "") {
  if (x == null || depth > 8) return out;

  if (typeof x === "string") {
    out.push(x);
    return out;
  }

  if (Array.isArray(x)) {
    for (const v of x) collectStrings(v, out, depth + 1, key);
    return out;
  }

  if (typeof x === "object") {
    for (const [k, v] of Object.entries(x)) {
      const lk = String(k).toLowerCase();

      // Don't scan prompt/reason/decision/description/instructions (they frequently quote PASS/FAIL)
      if (
        lk.includes("prompt") ||
        lk.includes("instruction") ||
        lk === "reason" ||
        lk === "decision" ||
        lk === "description"
      ) {
        continue;
      }

      collectStrings(v, out, depth + 1, lk);
    }
  }

  return out;
}

// Analyse a single text block for a PASS/FAIL marker.
// Returns { marker: "PASS"|"FAIL"|"", reason: string }
function markerFromText(text) {
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

// Scan all strings in a value for a PASS/FAIL marker with agreement requirement.
function reviewDecision(value) {
  const strings = collectStrings(value, []);
  if (strings.length === 0) return { marker: "", reason: "no-strings-found" };

  const markers = [];
  const reasons = [];

  for (const text of strings) {
    const analysis = markerFromText(text);
    if (analysis.marker) {
      markers.push(analysis.marker);
      continue;
    }
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

// Scan tool payload (input + output) for a review decision.
function reviewDecisionFromTaskPayload(input, output) {
  const candidates = [
    { source: "output", value: output },
    { source: "input.output", value: input?.output },
    { source: "input.result", value: input?.result },
    { source: "input.response", value: input?.response },
  ];

  const failures = [];

  for (const candidate of candidates) {
    if (candidate.value == null) continue;

    const analysis = reviewDecision(candidate.value);
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

function sanitizeSessionID(sessionID) {
  const sid = String(sessionID || "").trim();
  return sid.replace(/[^A-Za-z0-9._-]/g, "_");
}

function extractSessionID(evt) {
  return (
    evt?.properties?.sessionID ||
    evt?.properties?.info?.sessionID ||
    evt?.properties?.info?.sessionId ||
    null
  );
}

export default async (ctx) => {
  const baseDir =
    ctx?.worktree ||
    ctx?.project?.worktree ||
    ctx?.directory ||
    process.cwd();

  const sentinelDir = path.join(baseDir, ".opencode");
  const gateOpenCode = path.join(sentinelDir, ".needs_dotfiles_review");
  const gateClaude = path.join(baseDir, ".claude", ".needs_dotfiles_review"); // transitional

  // Track session ID so we can clean up session-scoped files on PASS.
  let lastSessionID = null;

  await appendDebug(baseDir, `initialized baseDir=${baseDir}`);

  async function clearGate(where) {
    await appendDebug(baseDir, `PASS detected via ${where} -> clearing gate`);

    // Clear unsuffixed sentinels (legacy / cold-start)
    await fs.rm(gateOpenCode, { force: true });
    await fs.rm(gateClaude, { force: true });

    // Clear session-scoped sentinel and enforcer state file
    const sid = sanitizeSessionID(lastSessionID);
    if (sid) {
      await fs.rm(
        path.join(sentinelDir, `.needs_dotfiles_review.${sid}`),
        { force: true }
      );
      await fs.rm(
        path.join(sentinelDir, `.dotfiles_review_enforcer_state.${sid}.json`),
        { force: true }
      );
    }
  }

  async function anyGateExists() {
    if (await exists(gateOpenCode)) return true;
    if (await exists(gateClaude)) return true;
    const sid = sanitizeSessionID(lastSessionID);
    if (sid) {
      if (await exists(path.join(sentinelDir, `.needs_dotfiles_review.${sid}`))) return true;
    }
    return false;
  }

  // Scan an arbitrary value (event object) for a PASS/FAIL decision and clear gate if found.
  async function scanEventAndClear(where, obj) {
    if (!(await anyGateExists())) return;

    const { marker, reason } = reviewDecision(obj);

    if (marker === "PASS") {
      await clearGate(where);
      return;
    }

    if (DEBUG) {
      const strings = collectStrings(obj, []);
      const sawMarker = strings.some(
        (s) => typeof s === "string" && s.includes("DOTFILES_REVIEWER_RESULT=")
      );
      if (sawMarker) {
        const sample =
          strings.find(
            (s) => typeof s === "string" && s.includes("DOTFILES_REVIEWER_RESULT=")
          ) || "";
        await appendDebug(
          baseDir,
          `marker seen but not REAL PASS via ${where}; marker=${marker}; reason=${reason}; lastLine=${JSON.stringify(
            meaningfulLines(sample).at(-1) ?? ""
          )}`
        );
      }
    }
  }

  // Scan a tool input+output payload for a PASS/FAIL decision and clear gate if found.
  async function scanToolAndClear(where, input, output) {
    if (!(await anyGateExists())) return;

    const reviewResult = reviewDecisionFromTaskPayload(input, output);

    if (reviewResult.decision === "PASS") {
      await clearGate(where);
    } else if (DEBUG) {
      const strings = collectStrings(output, []);
      const sawMarker = strings.some(
        (s) => typeof s === "string" && s.includes("DOTFILES_REVIEWER_RESULT=")
      );
      if (sawMarker) {
        await appendDebug(
          baseDir,
          `marker seen but not REAL PASS via ${where}; decision=${reviewResult.decision}; reason=${reviewResult.reason}`
        );
      }
    }
  }

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
          : (input?.tool?.name || input?.name || "unknown");
      await appendDebug(baseDir, `tool.execute.after tool=${tool}`);

      // Scope heavy scanning to the dotfiles-reviewer task to reduce false positives.
      const subtype =
        input?.tool?.arguments?.subagent_type ||
        input?.tool?.args?.subagent_type ||
        input?.arguments?.subagent_type ||
        input?.args?.subagent_type ||
        "";

      if (tool === "task" && subtype && subtype !== "dotfiles-reviewer") {
        return;
      }

      await scanToolAndClear(`tool.execute.after:${tool}`, input, output);
    },
  };
};
