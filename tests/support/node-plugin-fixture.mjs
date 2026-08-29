import { copyFile, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
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
