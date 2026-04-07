// .opencode/plugins/enforce-dotfiles-review.js
import { stat, readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

export default async (ctx = {}) => {
  const baseDir =
    ctx?.worktree ||
    ctx?.project?.worktree ||
    ctx?.directory ||
    process.cwd();

  const gateOpenCode = path.join(baseDir, ".opencode", ".needs_dotfiles_review");
  const gateClaude = path.join(baseDir, ".claude", ".needs_dotfiles_review"); // optional transitional
  const stateFile = path.join(baseDir, ".opencode", ".dotfiles_review_enforcer_state.json");

  // Disable with: OPENCODE_ENFORCE_REVIEW=0
  const env = String(process.env.OPENCODE_ENFORCE_REVIEW || "").toLowerCase();
  if (env === "0" || env === "false" || env === "off") return { event: async () => {} };

  const reviewerPrompt = [
    "Dotfiles review required.",
    "",
    "@dotfiles-reviewer",
    "Review ONLY the latest git changes (use git diff) and end with EXACTLY ONE of the following as the FINAL LINE ONLY:",
    "DOTFILES_REVIEWER_RESULT=PASS",
    "DOTFILES_REVIEWER_RESULT=FAIL",
    "",
    "If FAIL: fix Must-fix issues and rerun the agent.",
    "",
  ].join("\n");

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

  async function getGatePath() {
    if (await exists(gateOpenCode)) return gateOpenCode;
    if (await exists(gateClaude)) return gateClaude;
    return null;
  }

  async function gateMtimeMs(p) {
    const s = await stat(p);
    return s.mtimeMs;
  }

  async function loadState() {
    try {
      return JSON.parse(await readFile(stateFile, "utf8"));
    } catch {
      return {};
    }
  }

  async function saveState(next) {
    await mkdir(path.dirname(stateFile), { recursive: true });
    await writeFile(stateFile, JSON.stringify(next, null, 2) + "\n", "utf8");
  }

  // ---- SessionID cache (from your FULL_DUMP) ----
  // message.updated → properties.info.sessionID
  let lastSessionID = null;

  function extractSessionID(evt) {
    return (
      evt?.properties?.sessionID ||
      evt?.properties?.info?.sessionID ||
      evt?.properties?.info?.sessionId ||
      null
    );
  }

  // ---- Debounced enforcement (so we run after edits settle) ----
  const DEBOUNCE_MS = 1500;

  let debounceTimer = null;
  let inFlight = false;
  let lastHandledGateMtimeMsMem = 0;

  function scheduleEnforce(trigger) {
    if (debounceTimer) clearTimeout(debounceTimer);

    debounceTimer = setTimeout(() => {
      void enforceNow(trigger);
    }, DEBOUNCE_MS);

    // Don’t keep the process alive just because of our timer
    debounceTimer.unref?.();
  }

  async function enforceNow(trigger) {
    if (inFlight) return;

    const gatePath = await getGatePath();
    if (!gatePath) return;

    const mtime = await gateMtimeMs(gatePath);
    const state = await loadState();
    const lastDisk = Number(state.lastHandledGateMtimeMs || 0);

    // Only run once per “gate instance” (mtime)
    if (mtime <= lastHandledGateMtimeMsMem || mtime <= lastDisk) return;

    // Mark handled BEFORE running to avoid repeats from rapid file.edited events
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
      if (lastSessionID && ctx.client?.session?.prompt) {
        await toast("Dotfiles review required — running dotfiles-reviewer…", "warning");
        await ctx.client.session.prompt({
          path: { id: lastSessionID },
          body: { parts: [{ type: "text", text: reviewerPrompt }] },
        });
      } else {
        // Fallback: insert prompt for manual enter
        await toast(
          "Dotfiles review required — couldn’t detect session id. Prompt inserted; press Enter.",
          "warning"
        );
        try {
          await ctx.client?.tui?.clearPrompt?.();
          await ctx.client?.tui?.appendPrompt?.({ body: { text: reviewerPrompt } });
        } catch {}
      }
    } finally {
      setTimeout(() => (inFlight = false), 500);
    }
  }

  function normalize(p) {
    return String(p || "").replace(/\\/g, "/").toLowerCase();
  }
  function isOpencodeArtifact(p) {
    const n = normalize(p);
    return n.includes("/.opencode/") || n.startsWith(".opencode/");
  }

  function repoRelLower(p) {
    if (!p) return "";
    try {
      const rel = path.isAbsolute(p) ? path.relative(baseDir, p) : p;
      return normalize(rel).replace(/^\.\//, "");
    } catch {
      return normalize(p).replace(/^\.\//, "");
    }
  }

  function classifyPath(p) {
    const rel = repoRelLower(p);

    if (rel === "assets/readme.md") return "exempt-doc";
    if (rel.startsWith("docs/")) {
      if (rel.startsWith("docs/agents/")) return "reviewed-doc";
      return "exempt-doc";
    }

    if (
      rel === "readme.md" ||
      rel === "agents.md" ||
      rel === "dot_claude/agents.md"
    ) {
      return "reviewed-doc";
    }

    return "normal";
  }

  function extractFilePath(evt) {
    return (
      evt?.path ||
      evt?.properties?.path ||
      evt?.properties?.file ||
      evt?.file ||
      evt?.filePath ||
      ""
    );
  }

  return {
    event: async ({ event: evt }) => {
      if (!evt?.type) return;

      // Keep sessionID fresh from normal chat flow
      if (evt.type === "message.updated") {
        const sid = extractSessionID(evt);
        if (sid) lastSessionID = sid;
        return;
      }

      // Main trigger: edits
      if (evt.type === "file.edited") {
        const p = extractFilePath(evt);
        // ignore .opencode artifacts except the sentinel itself (we actually WANT that one)
        if (p && isOpencodeArtifact(p) && !normalize(p).endsWith("/.needs_dotfiles_review")) return;
        if (p && classifyPath(p) === "exempt-doc") return;

        // If no gate, nothing to do
        if (!(await getGatePath())) return;

        // Debounced: run reviewer after edits settle
        scheduleEnforce("file.edited");
        return;
      }

      // Optional: still useful sometimes, but not required.
      if (evt.type === "session.idle") {
        if (!(await getGatePath())) return;
        scheduleEnforce("session.idle");
      }
    },
  };
};
