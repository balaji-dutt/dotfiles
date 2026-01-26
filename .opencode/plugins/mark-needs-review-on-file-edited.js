// .opencode/plugins/mark-needs-review-on-file-edited.js
import { mkdir, writeFile, stat } from "node:fs/promises";
import path from "node:path";

export default async (ctx) => {
  const baseDir =
    ctx?.worktree ||
    ctx?.project?.worktree ||
    ctx?.directory ||
    process.cwd();

  const sentinel = path.join(baseDir, ".opencode", ".needs_dotfiles_review");

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

  // Dedupe (avoid double-marking)
  const DEDUPE_MS = 2000;

  // Toast throttle (avoid spam)
  const TOAST_THROTTLE_MS = 10_000;
  let lastToastAtMs = 0;

  async function recentlyMarked() {
    try {
      const s = await stat(sentinel);
      return Date.now() - s.mtimeMs < DEDUPE_MS;
    } catch {
      return false;
    }
  }

  async function mark() {
    await mkdir(path.dirname(sentinel), { recursive: true });
    await writeFile(sentinel, `${Math.floor(Date.now() / 1000)}\n`, "utf8");
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

  function isOpencodeArtifact(p) {
    const n = normalizeLower(p);
    return n.includes("/.opencode/") || n.startsWith(".opencode/");
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

  async function markAndToast(file, reasonLabel) {
    await mark();
    const fileDisplay = relPath(file);
    await maybeToast(`Marked for review: ${fileDisplay}`);
  }

  return {
    event: async ({ event }) => {
      if (!event?.type) return;

      if (event.type === "file.edited") {
        const file = extractFileFromEvent(event);
        if (!isInsideRepo(file)) return;
        if (isOpencodeArtifact(file)) return;

        if (await recentlyMarked()) return;
        await markAndToast(file, "file.edited");
        return;
      }

      // Optional safety net for platforms that emit tool events
      if (event.type === "tool.execute.after") {
        const tool = String(event?.tool || event?.properties?.tool || "").toLowerCase();
        if (tool === "edit" || tool === "write" || tool === "multiedit" || tool === "apply_patch") {
          if (await recentlyMarked()) return;
          await markAndToast("", `tool.execute.after:${tool}`);
        }
      }
    },
  };
};
