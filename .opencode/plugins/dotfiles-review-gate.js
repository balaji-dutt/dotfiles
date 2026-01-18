// .opencode/plugins/dotfiles-review-gate.js
import fs from "node:fs/promises";
import path from "node:path";

const PASS_LINE = "DOTFILES_REVIEWER_RESULT=PASS";
const FAIL_LINE = "DOTFILES_REVIEWER_RESULT=FAIL";

// Debug logging: opt-in only (prevents untracked noise by default)
const DEBUG = process.env.DOTFILES_REVIEW_GATE_DEBUG === "1";

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

// Strict: PASS must be the final non-empty line.
function isFinalLinePass(text) {
  if (typeof text !== "string") return false;

  // Trim trailing whitespace/newlines, then look at the last line
  const trimmed = text.replace(/\s+$/g, "");
  const lines = trimmed.split(/\r?\n/);
  const last = (lines[lines.length - 1] || "").trim();

  if (last !== PASS_LINE) return false;

  // Extra safety: if FAIL appears anywhere, do not clear.
  if (trimmed.includes(FAIL_LINE)) return false;

  // Extra safety: reject “instruction blocks” that mention both PASS/FAIL choices
  // (they usually contain these words even if last line happens to be PASS for some reason)
  if (/End your response with exactly one of/i.test(trimmed)) return false;
  if (/Your response MUST end with exactly ONE of/i.test(trimmed)) return false;

  return true;
}

// Extract likely output strings from tool outputs, without ingesting prompts/system messages.
// Keep this narrow on purpose.
function collectOutputStrings(x, out = []) {
  if (!x) return out;
  if (typeof x === "string") { out.push(x); return out; }
  if (Array.isArray(x)) { for (const v of x) collectOutputStrings(v, out); return out; }
  if (typeof x === "object") {
    // Prefer tool output-ish keys only
    for (const k of ["output", "text", "result", "stdout", "stderr"]) {
      if (k in x) collectOutputStrings(x[k], out);
    }
    return out;
  }
  return out;
}

async function clearGate(baseDir) {
  await fs.rm(path.join(baseDir, ".opencode", ".needs_dotfiles_review"), { force: true });
  await fs.rm(path.join(baseDir, ".claude", ".needs_dotfiles_review"), { force: true }); // transitional
}

export default async ({ project, directory, worktree }) => {
  const baseDir = worktree || project?.worktree || directory || process.cwd();

  const gateO = path.join(baseDir, ".opencode", ".needs_dotfiles_review");
  const gateC = path.join(baseDir, ".claude", ".needs_dotfiles_review");

  // Track reviewer background task_ids so we only clear from that task's output
  const reviewerTaskIds = new Set();

  await appendDebug(baseDir, `initialized baseDir=${baseDir}`);

  async function gated() {
    return (await exists(gateO)) || (await exists(gateC));
  }

  function findTaskId(obj) {
    // best-effort: different tools name it differently
    return obj?.task_id || obj?.taskId || obj?.id || null;
  }

  function findAgent(obj) {
    return obj?.agent || obj?.args?.agent || null;
  }

  async function maybeClearFromText(text, why) {
    if (!(await gated())) return;
    if (!isFinalLinePass(text)) return;

    await appendDebug(baseDir, `PASS(final-line) via ${why} -> clearing gate`);
    await clearGate(baseDir);
  }

  return {
    "tool.execute.after": async (input, output) => {
      const toolName = input?.tool || "unknown";
      await appendDebug(baseDir, `tool.execute.after tool=${toolName}`);

      // 1) Record background_task task_id if it is for dotfiles-reviewer
      if (toolName === "background_task") {
        const tid = findTaskId(output) || findTaskId(input?.args) || null;

        // Prefer structured agent fields; fall back to searching the serialized input
        const agent = findAgent(output) || findAgent(input) || null;
        const serialized = JSON.stringify({ input, output });

        const isReviewer =
          agent === "dotfiles-reviewer" ||
          serialized.includes('"agent":"dotfiles-reviewer"') ||
          serialized.includes("agent=dotfiles-reviewer");

        if (tid && isReviewer) {
          reviewerTaskIds.add(tid);
          await appendDebug(baseDir, `registered reviewer task_id=${tid}`);
        }
        return;
      }

      // 2) On background_output, only consider it if it's from the reviewer task (when we can tell)
      if (toolName === "background_output") {
        const tid = findTaskId(output) || findTaskId(input?.args) || null;
        if (tid && reviewerTaskIds.size > 0 && !reviewerTaskIds.has(tid)) {
          await appendDebug(baseDir, `background_output task_id=${tid} not registered as reviewer; ignore`);
          return;
        }

        // Extract candidate output strings (narrow)
        const strings = collectOutputStrings(output, []);
        if (strings.length === 0) {
          // last-resort: stringify output and apply final-line PASS check
          await maybeClearFromText(JSON.stringify(output), "background_output:stringify");
          return;
        }

        for (const s of strings) {
          await maybeClearFromText(s, "background_output");
        }
        return;
      }

      // Ignore other tools to reduce false positives
    },
  };
};
