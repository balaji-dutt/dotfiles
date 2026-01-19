import fs from "node:fs/promises";
import path from "node:path";

const PASS_LINE = "DOTFILES_REVIEWER_RESULT=PASS";
const FAIL_LINE = "DOTFILES_REVIEWER_RESULT=FAIL";

const DEBUG = process.env.DOTFILES_REVIEW_GATE_DEBUG === "1";
const RECENT_WINDOW_MS = 10 * 60 * 1000; // only clear within 10 min of a reviewer run

async function exists(p) {
  try { await fs.stat(p); return true; } catch { return false; }
}

async function appendDebug(baseDir, line) {
  if (!DEBUG) return;
  const logPath = path.join(baseDir, ".opencode", ".dotfiles-review-gate.log");
  const ts = new Date().toISOString();
  await fs.mkdir(path.dirname(logPath), { recursive: true });
  await fs.appendFile(logPath, `${ts} ${line}\n`, "utf8");
}

async function clearGate(baseDir) {
  await fs.rm(path.join(baseDir, ".opencode", ".needs_dotfiles_review"), { force: true });
  await fs.rm(path.join(baseDir, ".claude", ".needs_dotfiles_review"), { force: true }); // transitional
}

function stripTrailingMetadata(text) {
  if (typeof text !== "string") return "";
  let t = text;
  // remove trailing <task_metadata> blocks if present
  t = t.replace(/\r?\n<task_metadata>[\s\S]*$/i, "");
  // trim trailing whitespace/newlines
  t = t.replace(/\s+$/g, "");
  return t;
}

function isFinalLinePass(text) {
  if (typeof text !== "string") return false;
  if (!text.includes("DOTFILES_REVIEWER_RESULT=")) return false;
  if (text.includes(FAIL_LINE)) return false;

  // Avoid clearing on enforcement/instruction text (which often mentions PASS/FAIL choices)
  const instructionPatterns = [
    /End your response with exactly one of/i,
    /Your response MUST end with exactly ONE of/i,
    /Ensure the .*FINAL line/i,
    /Do this next \(invoke the agent explicitly\)/i,
  ];
  for (const re of instructionPatterns) {
    if (re.test(text)) return false;
  }

  const stripped = stripTrailingMetadata(text);
  const lines = stripped.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  if (!lines.length) return false;

  return lines[lines.length - 1] === PASS_LINE;
}

// Collect strings from an event/object while avoiding prompt-ish keys
function collectStrings(x, out = []) {
  if (!x) return out;
  if (typeof x === "string") { out.push(x); return out; }
  if (Array.isArray(x)) { for (const v of x) collectStrings(v, out); return out; }
  if (typeof x === "object") {
    for (const [k, v] of Object.entries(x)) {
      const lower = k.toLowerCase();
      if (
        lower.includes("prompt") ||
        lower.includes("instruction") ||
        lower === "reason" ||
        lower === "description"
      ) continue;
      collectStrings(v, out);
    }
  }
  return out;
}

export default async ({ project, directory, worktree }) => {
  const baseDir = worktree || project?.worktree || directory || process.cwd();
  const gateO = path.join(baseDir, ".opencode", ".needs_dotfiles_review");
  const gateC = path.join(baseDir, ".claude", ".needs_dotfiles_review");

  let lastReviewerRunAt = 0;

  await appendDebug(baseDir, `initialized baseDir=${baseDir}`);

  async function gated() {
    return (await exists(gateO)) || (await exists(gateC));
  }

  function markReviewerRun(why) {
    lastReviewerRunAt = Date.now();
    appendDebug(baseDir, `marked reviewer run (${why})`).catch(() => {});
  }

  function isRecentReviewerRun() {
    return (Date.now() - lastReviewerRunAt) <= RECENT_WINDOW_MS;
  }

  async function maybeClearFromStrings(strings, why) {
    if (!(await gated())) return;

    // If we *haven’t* recently invoked the reviewer, be conservative and do nothing.
    // (This avoids clearing from random old text containing PASS.)
    if (!isRecentReviewerRun()) {
      await appendDebug(baseDir, `PASS check skipped (no recent reviewer run) via ${why}`);
      return;
    }

    for (const s of strings) {
      if (isFinalLinePass(s)) {
        await appendDebug(baseDir, `PASS(final-line) via ${why} -> clearing gate`);
        await clearGate(baseDir);
        return;
      }
    }
  }

  return {
    "tool.execute.after": async (input, output) => {
      const toolName = input?.tool || "unknown";
      await appendDebug(baseDir, `tool.execute.after tool=${toolName}`);

      // When we see the reviewer background task being launched, mark it.
      if (toolName === "background_task") {
        const blob = JSON.stringify({ input, output });
        if (blob.includes("agent=dotfiles-reviewer") || blob.includes('"agent":"dotfiles-reviewer"')) {
          markReviewerRun("background_task(dotfiles-reviewer)");
        }
        return;
      }

      // Primary path: background_output sometimes contains the final reviewer text.
      if (toolName === "background_output") {
        const strings = collectStrings(output, []);
        await maybeClearFromStrings(strings, "tool.execute.after:background_output");
      }
    },

    // Secondary path: sometimes the parent agent prints the reviewer result as a normal message.
    "message.updated": async (ev) => {
      const strings = collectStrings(ev, []);
      await maybeClearFromStrings(strings, "message.updated");
    },
    "message.part.updated": async (ev) => {
      const strings = collectStrings(ev, []);
      await maybeClearFromStrings(strings, "message.part.updated");
    },
  };
};
