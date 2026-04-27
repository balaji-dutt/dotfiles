// .opencode/plugins/enforce-dotfiles-review.js
import { stat, readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

export default async (ctx = {}) => {
  const baseDir =
    ctx?.worktree ||
    ctx?.project?.worktree ||
    ctx?.directory ||
    process.cwd();

  const sentinelDir = path.join(baseDir, ".opencode");
  const sentinelBase = ".needs_dotfiles_review";
  const gateOpenCode = path.join(sentinelDir, sentinelBase);
  const gateClaude = path.join(baseDir, ".claude", sentinelBase); // optional transitional
  // Disable with: OPENCODE_ENFORCE_REVIEW=0
  const env = String(process.env.OPENCODE_ENFORCE_REVIEW || "").toLowerCase();
  if (env === "0" || env === "false" || env === "off") return { event: async () => {} };

  const reviewerPromptBase = [
    "Dotfiles review required.",
    "",
    "@dotfiles-reviewer",
  ];

  // Shell-safe single-quote escaping for use in git diff / git status args.
  // Any single quote in the path is replaced with '\'' (close-quote, literal
  // single quote, reopen-quote), which is the standard POSIX approach.
  function shellQuote(s) {
    return "'" + String(s).replace(/'/g, "'\\''") + "'";
  }

  function buildReviewerPrompt(files) {
    const diffLines =
      files?.length
        ? [
            "Scope the diff to only the files this session edited. Run both:",
            `  git diff -- ${files.map(shellQuote).join(" ")}`,
            `  git diff --cached -- ${files.map(shellQuote).join(" ")}`,
            "Also check for untracked new files:",
            `  git status --short -- ${files.map(shellQuote).join(" ")}`,
          ]
        : ["Review ONLY the latest git changes (use git diff and git diff --cached)."];

    return [
      ...reviewerPromptBase,
      ...diffLines,
      "End with EXACTLY ONE of the following as the FINAL LINE ONLY:",
      "DOTFILES_REVIEWER_RESULT=PASS",
      "DOTFILES_REVIEWER_RESULT=FAIL",
      "",
      "If FAIL: fix Must-fix issues and rerun the agent.",
      "",
    ].join("\n");
  }

  // Read the file list from a JSON-format sentinel, if present.
  // Returns an array of repo-relative paths, or null if unavailable
  // (plain-text sentinel, parse error, or empty list).
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

  // ---- SessionID cache ----
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
    const sid = String(sessionID || "").trim();
    return sid.replace(/[^A-Za-z0-9._-]/g, "_");
  }

  // Return the state file path, scoped to the current session when known.
  function stateFilePath() {
    const base = ".dotfiles_review_enforcer_state";
    const sid = sanitizeSessionID(lastSessionID);
    if (sid) {
      return path.join(sentinelDir, `${base}.${sid}.json`);
    }
    return path.join(sentinelDir, `${base}.json`);
  }

  async function getGatePath() {
    // Once session ID is known, ONLY the session-scoped sentinel is checked.
    // The unsuffixed sentinel is never a fallback at this point — it belongs
    // to a cold-start write or a different session and must not be consumed here.
    if (lastSessionID) {
      const sid = sanitizeSessionID(lastSessionID);
      if (!sid) return null;
      const scoped = path.join(sentinelDir, `${sentinelBase}.${sid}`);
      if (await exists(scoped)) return scoped;
      return null;
    }
    // No session ID yet — cold-start / backward compat only.
    if (await exists(gateOpenCode)) return gateOpenCode;
    if (await exists(gateClaude)) return gateClaude;
    return null;
  }

  // ---- Session metadata cache (for parentID check) ----
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
      const response = await ctx.client?.session?.get?.({ path: { id: sessionID } });
      const session = response?.data || response;
      rememberSession(sessionCache, session);
      return sessionCache.get(sessionID) || null;
    } catch {
      return cached || null;
    }
  }

  // Returns false for sub-sessions (parentID set) or when the dotfiles-reviewer
  // agent itself is running — avoids injecting the review prompt into the reviewer.
  async function shouldPromptSession(sessionID) {
    const info = await sessionMeta(sessionID);
    if (!info) return false;
    if (info.parentID) return false;
    if (info.agent === "dotfiles-reviewer") return false;
    return true;
  }

  // ---- Debounced enforcement ----
  // session.idle is the sole trigger. The debounce here only absorbs
  // rapid idle/un-idle flaps — it does NOT need to span tool-call gaps.
  const DEBOUNCE_MS = 500;

  let debounceTimer = null;
  let inFlight = false;
  let lastHandledGateMtimeMsMem = 0;

  function scheduleEnforce(trigger) {
    if (debounceTimer) clearTimeout(debounceTimer);

    debounceTimer = setTimeout(() => {
      void enforceNow(trigger);
    }, DEBOUNCE_MS);

    // Don't keep the process alive just because of our timer
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

    // Only run once per "gate instance" (mtime)
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
      const files = await readSentinelFiles(gatePath);
      const prompt = buildReviewerPrompt(files);
      if (lastSessionID && ctx.client?.session?.prompt) {
        await toast("Dotfiles review required — running dotfiles-reviewer…", "warning");
        await ctx.client.session.prompt({
          path: { id: lastSessionID },
          body: { parts: [{ type: "text", text: prompt }] },
        });
      } else {
        // Fallback: insert prompt for manual enter
        await toast(
          "Dotfiles review required — couldn't detect session id. Prompt inserted; press Enter.",
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

      // Keep sessionID and session metadata fresh from normal chat flow
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

      // file.edited: sentinel is written by mark-needs-review-on-file-edited.js.
      // Enforcement is triggered by session.idle only — do not schedule here.
      // Triggering on file.edited races against mid-turn tool calls (cz-audit,
      // chezmoi apply, etc.) which consume any debounce window.
      if (evt.type === "file.edited") {
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
