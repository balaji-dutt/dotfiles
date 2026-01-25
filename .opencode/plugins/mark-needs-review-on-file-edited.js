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
  // - OPENCODE_MARK_REVIEW=1 forces enable (useful if you later add other gating)
  const env = (process.env.OPENCODE_MARK_REVIEW || "").toLowerCase();
  const disabled = env === "0" || env === "false" || env === "off";
  const enabled = !disabled; // default on
  if (!enabled) return { event: async () => {} };

  // Dedupe to avoid double-marking (e.g. if Claude hooks also mark on macOS)
  const DEDUPE_MS = 2000;
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
    return String(p || "").replace(/\\/g, "/").toLowerCase();
  }

  const baseNorm = normalize(baseDir);

  function isInsideRepo(p) {
    if (!p) return false;
    if (path.isAbsolute(p)) return normalize(p).startsWith(baseNorm);
    return true;
  }

  function isOpencodeArtifact(pNorm) {
    return pNorm.includes("/.opencode/") || pNorm.startsWith(".opencode/");
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

  return {
    event: async ({ event }) => {
      if (!event?.type) return;

      if (event.type === "file.edited") {
        const file = extractFileFromEvent(event);
        if (!isInsideRepo(file)) return;

        const pNorm = normalize(file);
        if (isOpencodeArtifact(pNorm)) return;

        if (await recentlyMarked()) return;
        await mark();
        return;
      }

      // Optional safety net for platforms that emit tool events
      if (event.type === "tool.execute.after") {
        const tool = String(event?.tool || event?.properties?.tool || "").toLowerCase();
        if (tool === "edit" || tool === "write" || tool === "multiedit" || tool === "apply_patch") {
          if (await recentlyMarked()) return;
          await mark();
        }
      }
    },
  };
};
