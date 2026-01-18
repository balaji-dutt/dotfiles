import fs from "node:fs/promises";
import path from "node:path";

const PASS_LINE = "DOTFILES_REVIEWER_RESULT=PASS";
const FAIL_LINE = "DOTFILES_REVIEWER_RESULT=FAIL";

// Debug logging (opt-in)
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

async function clearGate(baseDir) {
  await fs.rm(path.join(baseDir, ".opencode", ".needs_dotfiles_review"), { force: true });
  // transitional safety
  await fs.rm(path.join(baseDir, ".claude", ".needs_dotfiles_review"), { force: true });
}

// Strip known trailing metadata blocks that OpenCode/agents sometimes append.
function stripTrailingMetadata(text) {
  if (typeof text !== "string") return "";
  let t = text;

  // Remove trailing <task_metadata>...</task_metadata> or <task_metadata>... EOF
  t = t.replace(/\r?\n<task_metadata>[\s\S]*$/i, "");

  // Trim trailing whitespace
  t = t.replace(/\s+$/g, "");

  return t;
}

// PASS must appear as a standalone line at the end,
// allowing only whitespace and/or task_metadata after it.
function looksLikeRealPass(text) {
  if (typeof text !== "string") return false;
  if (!text.includes("DOTFILES_REVIEWER_RESULT=")) return false;

  // If FAIL exists anywhere, do not clear.
  if (text.includes(FAIL_LINE)) return false;

  // Reject obvious instruction text (the common false-positive source)
  const instructionPatterns = [
    /End your response with exactly one of/i,
    /Your response MUST end with exactly ONE of/i,
    /Ensure the.*FINAL line/i,
  ];
  for (const re of instructionPatterns) {
    if (re.test(text)) return false;
  }

  const stripped = stripTrailingMetadata(text);
  const lines = stripped.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  if (lines.length === 0) return false;

  const last = lines[lines.length - 1];
  return last === PASS_LINE;
}

// Recursively collect strings, but avoid prompt-ish fields.
// This is broader than the “output-only” approach, but still avoids the common prompt keys.
function collectCandidateStrings(x, out = [], keyPath = []) {
  if (!x) return out;

  if (typeof x === "string") {
    out.push(x);
    return out;
  }

  if (Array.isArray(x)) {
    for (const v of x) collectCandidateStrings(v, out, keyPath);
    return out;
  }

  if (typeof x === "object") {
    for (const [k, v] of Object.entries(x)) {
      const lower = k.toLowerCase();
      // skip prompt/instruction/reason fields (common false positives)
      if (lower.includes("prompt") || lower.includes("instruction") || lower === "reason") continue;
      collectCandidateStrings(v, out, keyPath.concat(k));
    }
    return out;
  }

  return out;
}

export default async ({ project, directory, worktree }) => {
  const baseDir = worktree || project?.worktree || directory || process.cwd();

  const gateO = path.join(baseDir, ".opencode", ".needs_dotfiles_review");
  const gateC = path.join(baseDir, ".claude", ".needs_dotfiles_review");

  await appendDebug(baseDir, `initialized baseDir=${baseDir}`);

  async function gated() {
    return (await exists(gateO)) || (await exists(gateC));
  }

  async function maybeClear(strings, why) {
    if (!(await gated())) return;

    for (const s of strings) {
      if (looksLikeRealPass(s)) {
        await appendDebug(baseDir, `PASS detected via ${why} -> clearing gate`);
        await clearGate(baseDir);
        return true;
      }
    }
    return false;
  }

  return {
    "tool.execute.after": async (input, output) => {
      const toolName = input?.tool || "unknown";
      await appendDebug(baseDir, `tool.execute.after tool=${toolName}`);

      // Only act on background_output to avoid scanning unrelated tool outputs.
      if (toolName !== "background_output") return;

      const strings = collectCandidateStrings(output, []);

      // As a fallback, also consider a stringified version, but don't require final-line on it.
      // (We only use it to help diagnose shapes when DEBUG is on.)
      if (DEBUG && strings.length === 0) {
        await appendDebug(baseDir, `background_output had 0 extracted strings; output keys=${Object.keys(output || {}).join(",")}`);
      }

      await maybeClear(strings, "tool.execute.after:background_output");
    },
  };
};
