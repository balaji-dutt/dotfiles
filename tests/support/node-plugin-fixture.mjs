import { copyFile, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import fs from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const repoRoot = process.env.DOTFILES_TEST_REPO;
if (!repoRoot) throw new Error("DOTFILES_TEST_REPO is required");

const defaultConfig = {
  reviewerAgent: "dotfiles-reviewer",
  resultMarkerPrefix: "DOTFILES_REVIEWER_RESULT",
  sentinelBase: ".needs_dotfiles_review",
  enforcerStateBase: ".dotfiles_review_enforcer_state",
  reviewLabel: "Dotfiles",
  watchEvents: ["file.edited", "file.deleted", "file.renamed", "file.moved"],
  exemptPaths: ["docs/**", "!docs/agents/**", "assets/README.md", ".beads/**"],
  legacyGatePaths: [".claude/.needs_dotfiles_review"],
  editorSuppressHeader: "<!-- markdownlint-disable -->",
};

const picomatchStub = String.raw`
function globRegex(pattern) {
  let out = "^";
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i];
    if (ch === "*" && pattern[i + 1] === "*") {
      i++;
      if (pattern[i + 1] === "/") { i++; out += "(?:.*/)?"; }
      else out += ".*";
    } else if (ch === "*") out += "[^/]*";
    else if (ch === "?") out += "[^/]";
    else out += ch.replace(/[\\^$+?.()|{}[\]]/g, "\\$&");
  }
  return new RegExp(out + "$");
}
module.exports = function picomatch(patterns) {
  const list = Array.isArray(patterns) ? patterns : [patterns];
  const positive = list.filter((value) => !value.startsWith("!"));
  const negative = list.filter((value) => value.startsWith("!")).map((value) => value.slice(1));
  return (value) => positive.some((pattern) => globRegex(pattern).test(value)) &&
    !negative.some((pattern) => globRegex(pattern).test(value));
};
`;

export async function createPluginFixture(names, config = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), "repo-opencode-plugins-"));
  const pluginDir = path.join(root, ".opencode", "plugins");
  const picomatchDir = path.join(root, ".opencode", "node_modules", "picomatch");
  await mkdir(pluginDir, { recursive: true });
  await mkdir(picomatchDir, { recursive: true });
  await writeFile(path.join(picomatchDir, "index.js"), picomatchStub);
  await writeFile(
    path.join(root, ".opencode", "opencode-tooling.config.jsonc"),
    JSON.stringify({ ...defaultConfig, ...config }, null, 2) + "\n"
  );
  for (const name of names) {
    await copyFile(path.join(repoRoot, ".opencode", "plugins", name), path.join(pluginDir, name));
  }
  return {
    root,
    path: (...parts) => path.join(root, ...parts),
    async importPlugin(name) {
      return import(`${pathToFileURL(path.join(pluginDir, name)).href}?fixture=${Date.now()}-${Math.random()}`);
    },
    async cleanup() {
      await rm(root, { recursive: true, force: true });
    },
  };
}

export async function wait(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export async function startEditorPlugin(editor, context) {
  const watchers = [];
  const originalWatch = fs.watch;
  const close = () => watchers.forEach(({ watcher }) => watcher.close());
  try {
    try {
      fs.watch = (dir, ...args) => {
        const watcher = originalWatch(dir, ...args);
        watchers.push({ dir, watcher });
        return watcher;
      };
      syncBuiltinESMExports();
      await editor(context);
    } finally {
      fs.watch = originalWatch;
      syncBuiltinESMExports();
    }
    if (process.platform === "linux") return close;

    const dir = fs.realpathSync(os.tmpdir());
    const watcher = watchers.find((entry) => entry.dir === dir)?.watcher;
    if (!watcher) throw new Error("editor did not watch the fixture temp directory");
    const name = `editor-ready-${process.pid}-${Date.now()}-${Math.random()}.txt`;
    const probe = path.join(dir, name);
    let ready = false;
    const onChange = (_event, filename) => { if (filename === name) ready = true; };
    watcher.on("change", onChange);
    try {
      // fs.watch has no readiness event: https://github.com/nodejs/node/issues/52601
      const deadline = Date.now() + 2000;
      while (!ready && Date.now() < deadline) {
        await writeFile(probe, "ready\n");
        await wait(20);
      }
      if (!ready) throw new Error("editor watcher did not observe the readiness probe");
    } finally {
      watcher.off("change", onChange);
      await rm(probe, { force: true });
    }
    return close;
  } catch (error) {
    close();
    throw error;
  }
}
