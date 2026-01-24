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

  // Toggle behavior:
  // - OPENCODE_MARK_REVIEW=0 disables everywhere
  // - OPENCODE_MARK_REVIEW=1 enables everywhere
  // - otherwise: enable only on Windows + WSL
  const env = (process.env.OPENCODE_MARK_REVIEW || "").toLowerCase();
  const forcedOff = env === "0" || env === "false" || env === "off";
  const forcedOn = env === "1" || env === "true" || env === "on";

  const isWin = process.platform === "win32";
  const isWSL =
    process.platform === "linux" &&
    !!(process.env.WSL_DISTRO_NAME || process.env.WSL_INTEROP);

  const enabled = forcedOn || (!forcedOff && (isWin || isWSL));
  if (!enabled) return { event: async () => {} };

  // If something already touched the sentinel very recently (eg your Claude hook on macOS),
  // skip writing again to avoid noise.
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

    // If absolute, ensure it starts with repo root (case-insensitive via normalize)
    if (path.isAbsolute(p)) return normalize(p).startsWith(baseNorm);

    // Relative paths assumed repo-relative
    return true;
  }

  function isOpencodeArtifact(pNorm) {
    // catches ".opencode/..." anywhere in the path, windows or unix style
    return pNorm.includes("/.opencode/") || pNorm.startsWith(".opencode/");
  }

  // Extract "which file" from different payload shapes
  function extractFileFromEvent(event) {
    // Your event-tap proved `event.path` exists for file.edited in your build,
    // but keep these fallbacks for portability.
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

      // 1) Mark on file-edits
      if (event.type === "file.edited") {
        const file = extractFileFromEvent(event);
        if (!isInsideRepo(file)) return;

        const pNorm = normalize(file);
        if (isOpencodeArtifact(pNorm)) return;

        if (await recentlyMarked()) return;
        await mark();
        return;
      }

      // 2) Optional safety net: if tool events occur on some platforms, mark on those too.
      // (Doesn’t hurt; dedupe prevents double-mark.)
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
