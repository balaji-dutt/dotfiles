// .opencode/plugins/dotfiles-review-gate.js
import fs from "node:fs/promises";
import path from "node:path";

// Match PASS as a standalone final marker, not "PASS or FAIL" in prompts.
const PASS_RE = /(?:^|\n)DOTFILES_REVIEWER_RESULT=PASS\s*(?:$|\n)/;

async function exists(p) {
  try { await fs.stat(p); return true; } catch { return false; }
}

async function clearGate(worktree) {
  await fs.rm(path.join(worktree, ".opencode", ".needs_dotfiles_review"), { force: true });
  // transitional safety if the Claude sentinel gets created.ß
  await fs.rm(path.join(worktree, ".claude", ".needs_dotfiles_review"), { force: true });
}

// Pull likely text fields out of arbitrary event payloads.
function collectStrings(x, out = []) {
  if (!x) return out;
  if (typeof x === "string") { out.push(x); return out; }
  if (Array.isArray(x)) { for (const v of x) collectStrings(v, out); return out; }
  if (typeof x === "object") {
    // prioritize common fields used by OpenCode parts
    for (const k of ["output", "text", "content", "message", "prompt"]) {
      if (k in x) collectStrings(x[k], out);
    }
    for (const v of Object.values(x)) collectStrings(v, out);
  }
  return out;
}

export const DotfilesReviewGate = async ({ worktree }) => {
  const gate = path.join(worktree, ".opencode", ".needs_dotfiles_review");
  const gateClaude = path.join(worktree, ".claude", ".needs_dotfiles_review");

  async function maybeClear(ev) {
    if (!(await exists(gate)) && !(await exists(gateClaude))) return;

    const strings = collectStrings(ev, []);
    for (const s of strings) {
      if (!s) continue;
      // extra safety: require PASS to be near the end of the text
      const tail = s.slice(-800);
      if (PASS_RE.test(tail)) {
        await clearGate(worktree);
        return;
      }
    }
  }

  return {
    // This is the key one for your case (PASS is in parts)
    "message.part.updated": async (ev) => { await maybeClear(ev); },
    // Safety nets
    "message.updated": async (ev) => { await maybeClear(ev); },
    "message.complete": async (ev) => { await maybeClear(ev); },
  };
};

export default DotfilesReviewGate;
