// .opencode/plugins/dotfiles-review-gate.js
// Clears the dotfiles review gate when a REAL PASS is observed.
//
// REAL PASS = the final non-empty line of some observed assistant/tool text is exactly:
//   DOTFILES_REVIEWER_RESULT=PASS
//
// This listens to BOTH:
// - tool.execute.after (covers background_output / call_omo_agent / etc)
// - message.updated / message.part.updated (via the generic `event` hook)

import fs from "node:fs/promises";
import path from "node:path";

const PASS = "DOTFILES_REVIEWER_RESULT=PASS";

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

function isIgnorableTrailingLine(t) {
  if (!t) return true;

  // OpenCode task footer (your logs show this exact shape)
  if (/^to resume:/i.test(t)) return true;
  if (/delegate_task\(/i.test(t)) return true;

  // Optional: UI-ish footer lines that may appear in some payloads
  if (/^▣\s/.test(t)) return true;

  return false;
}

function lastMeaningfulLine(text) {
  if (typeof text !== "string") return "";

  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");

  // OpenCode sometimes appends:
  //   <task_metadata> ... </task_metadata>
  // after the model output. We want to ignore that whole block.
  let inTaskMetaBlock = false;

  for (let i = lines.length - 1; i >= 0; i--) {
    const t = lines[i].trim();
    if (!t) continue;

    // Ignore the entire <task_metadata>...</task_metadata> trailer if present
    if (t === "</task_metadata>") {
      inTaskMetaBlock = true;
      continue;
    }
    if (inTaskMetaBlock) {
      if (t === "<task_metadata>") inTaskMetaBlock = false;
      continue;
    }
    if (t === "<task_metadata>") continue;

    // Ignore common OpenCode task trailers
    if (/^to resume:/i.test(t)) continue;
    if (/delegate_task\(/i.test(t)) continue;

    // Ignore UI-ish footer noise if it leaks into payloads
    if (t.startsWith("▣")) continue;

    return t;
  }

  return "";
}

function isRealPass(text) {
  if (typeof text !== "string") return false;
  return lastMeaningfulLine(text) === PASS;
}

// Recursively collect candidate strings.
// Skip keys that commonly contain instruction text (the biggest false-positive source).
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

      // Don’t scan prompt/reason/decision/description/instructions (they frequently quote PASS/FAIL)
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

export default async (ctx) => {
  const baseDir =
    ctx?.worktree ||
    ctx?.project?.worktree ||
    ctx?.directory ||
    process.cwd();

  const gateOpenCode = path.join(baseDir, ".opencode", ".needs_dotfiles_review");
  const gateClaude = path.join(baseDir, ".claude", ".needs_dotfiles_review"); // transitional

  await appendDebug(baseDir, `initialized baseDir=${baseDir}`);

  async function clearGate(where) {
    if (!(await exists(gateOpenCode)) && !(await exists(gateClaude))) return;

    await appendDebug(baseDir, `PASS detected via ${where} -> clearing gate`);
    await fs.rm(gateOpenCode, { force: true });
    await fs.rm(gateClaude, { force: true });
  }

  async function scanAndClear(where, obj) {
    if (!(await exists(gateOpenCode)) && !(await exists(gateClaude))) return;

    const strings = collectStrings(obj, []);
    for (const s of strings) {
      if (isRealPass(s)) {
        await clearGate(where);
        return;
      }
    }

    if (DEBUG) {
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
          `marker seen but not REAL PASS via ${where}; lastLine=${JSON.stringify(
            lastMeaningfulLine(sample)
          )}`
        );
      }
    }
  }

  return {
    event: async ({ event: evt }) => {
      if (!evt?.type) return;
      if (evt.type === "message.updated" || evt.type === "message.part.updated") {
        await scanAndClear(`event:${evt.type}`, evt);
      }
    },

    "tool.execute.after": async (input, output) => {
      const tool = input?.tool || input?.name || "unknown";
      await appendDebug(baseDir, `tool.execute.after tool=${tool}`);
      await scanAndClear(`tool.execute.after:${tool}`, output);
    },
  };
};
