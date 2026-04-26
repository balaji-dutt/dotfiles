// .opencode/plugins/mark-needs-review-on-file-edited.js
import { mkdir, writeFile, unlink } from "node:fs/promises";
import path from "node:path";

export default async (ctx) => {
  const baseDir =
    ctx?.worktree ||
    ctx?.project?.worktree ||
    ctx?.directory ||
    process.cwd();

  const sentinelDir = path.join(baseDir, ".opencode");
  const sentinelBase = ".needs_dotfiles_review";

  // Session ID tracking — populated from message.updated events.
  // When known, we write a session-scoped sentinel so only the session
  // that triggered the edit gets the review prompt injected.
  let lastSessionID = null;

  // Accumulates repo-relative paths of files edited this session.
  // Written into the sentinel so the enforcer can scope git diff.
  const editedFiles = new Set();

  // True when we wrote an unsuffixed sentinel before learning the session ID.
  // On the next message.updated that provides a session ID, we re-write as a
  // scoped sentinel so the enforcer picks it up correctly.
  let pendingMarkWithoutSession = false;

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

  function sentinelPath(sessionID) {
    const sid = sanitizeSessionID(sessionID);
    if (sid) {
      return path.join(sentinelDir, `${sentinelBase}.${sid}`);
    }
    return path.join(sentinelDir, sentinelBase);
  }

  // Universal by default.
  // - OPENCODE_MARK_REVIEW=0 disables
  const env = (process.env.OPENCODE_MARK_REVIEW || "").toLowerCase();
  const disabled = env === "0" || env === "false" || env === "off";
  const enabled = !disabled;
  if (!enabled) return { event: async () => {} };

  // Toast toggle:
  // - OPENCODE_MARK_REVIEW_TOAST=0 disables toast
  const toastEnv = (process.env.OPENCODE_MARK_REVIEW_TOAST || "").toLowerCase();
  const toastDisabled = toastEnv === "0" || toastEnv === "false" || toastEnv === "off";
  const toastEnabled = !toastDisabled;

  // Toast throttle (avoid spam)
  const TOAST_THROTTLE_MS = 10_000;
  let lastToastAtMs = 0;

  async function mark(file) {
    if (file) {
      const rel = relPath(file);
      if (rel && rel !== "unknown file") editedFiles.add(rel);
    }
    const sid = sanitizeSessionID(lastSessionID);
    const p = sentinelPath(lastSessionID);
    pendingMarkWithoutSession = !sid;
    await mkdir(path.dirname(p), { recursive: true });
    const payload = {
      timestamp: Math.floor(Date.now() / 1000),
      sessionID: lastSessionID || null,
      files: [...editedFiles],
    };
    await writeFile(p, JSON.stringify(payload, null, 2) + "\n", "utf8");
    // If we just wrote a scoped sentinel, remove the unsuffixed fallback so
    // cold-start sessions in other windows cannot accidentally consume it.
    if (sid) {
      try { await unlink(path.join(sentinelDir, sentinelBase)); } catch {}
    }
  }

  function normalize(p) {
    return String(p || "").replace(/\\/g, "/");
  }
  function normalizeLower(p) {
    return normalize(p).toLowerCase();
  }

  const baseNorm = normalize(baseDir);
  const baseNormLower = normalizeLower(baseDir);
  const isWin = process.platform === "win32";

  function isInsideRepo(p) {
    if (!p) return false;
    if (path.isAbsolute(p)) {
      const nl = normalizeLower(p);
      return isWin ? nl.startsWith(baseNormLower) : normalize(p).startsWith(baseNorm);
    }
    return true;
  }

  function isOpencodeRuntimeArtifact(p) {
    const n = normalizeLower(p);
    if (!n.includes("/.opencode/") && !n.startsWith(".opencode/")) return false;
    // Only exclude runtime artifacts that should never trigger a review mark:
    // sentinel files, enforcer state files, node_modules, and OS cruft.
    // Source files under .opencode/ (plugins/, skills/, commands/, etc.) are
    // committed dotfiles and should be tracked and reviewed normally.
    const basename = path.basename(p).toLowerCase();
    return (
      basename.startsWith(".needs_dotfiles_review") ||
      basename.startsWith(".dotfiles_review_enforcer_state") ||
      basename === ".ds_store" ||
      n.startsWith(".opencode/node_modules/") ||
      n.includes("/.opencode/node_modules/")
    );
  }

  function extractFileFromEvent(event) {
    return (
      event?.path ||
      event?.properties?.file ||
      event?.properties?.path ||
      event?.file ||
      event?.filePath ||
      ""
    );
  }

  function relPath(p) {
    if (!p) return "unknown file";
    if (!path.isAbsolute(p)) return normalize(p);

    // Use path.relative for nice repo-relative display
    try {
      const rel = path.relative(baseDir, p);
      return rel && rel !== "" ? normalize(rel) : normalize(p);
    } catch {
      return normalize(p);
    }
  }

  function repoRelLower(p) {
    const rel = relPath(p).replace(/^\.\//, "");
    return normalizeLower(rel);
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

  async function maybeToast(message) {
    if (!toastEnabled) return;
    const now = Date.now();
    if (now - lastToastAtMs < TOAST_THROTTLE_MS) return;
    lastToastAtMs = now;

    try {
      await ctx.client?.tui?.showToast?.({
        body: { message, variant: "info" },
      });
    } catch {
      // no-op if not supported
    }
  }

  async function markAndToast(file) {
    await mark(file);
    const fileDisplay = relPath(file);
    await maybeToast(`Marked for review: ${fileDisplay}`);
  }

  return {
    event: async ({ event }) => {
      if (!event?.type) return;

      // Track session ID so sentinel files are session-scoped.
      if (event.type === "message.updated") {
        const sid = extractSessionID(event);
        if (sid) {
          lastSessionID = sid;
          if (pendingMarkWithoutSession) {
            pendingMarkWithoutSession = false;
            await mark();
            await maybeToast("Marked for review: pending edit in current session");
          }
        }
        return;
      }

      if (event.type === "file.edited") {
        const file = extractFileFromEvent(event);
        if (!isInsideRepo(file)) return;
        if (isOpencodeRuntimeArtifact(file)) return;
        if (classifyPath(file) === "exempt-doc") return;

        await markAndToast(file);
        return;
      }

      // Optional safety net for platforms that emit tool events
      if (event.type === "tool.execute.after") {
        const tool = String(event?.tool || event?.properties?.tool || "").toLowerCase();
        if (tool === "edit" || tool === "write" || tool === "multiedit" || tool === "apply_patch") {
          const file = extractFileFromEvent(event);
          if (file && isInsideRepo(file) && !isOpencodeRuntimeArtifact(file) && classifyPath(file) === "exempt-doc") {
            return;
          }
          if (!file) return;

          await markAndToast(file);
        }
      }
    },
  };
};
