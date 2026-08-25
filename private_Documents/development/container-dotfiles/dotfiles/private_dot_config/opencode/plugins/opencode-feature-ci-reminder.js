import { execFile } from "node:child_process";
import { access, readFile } from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const states = new Map();
const POLICY_SCHEMA = "./schemas/gitlab-pipeline-guard.v1.schema.json";
const REMOTE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const REF_PATTERN = /^refs\/heads\/[A-Za-z0-9._/-]+$/;
const JOB_PATTERN = /^[A-Za-z0-9._/-]+$/;
const BRANCH_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._/-]*$/;
const SHA_PATTERN = /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/;
const WRAPPER_PATTERN =
  /(?:^|[;&|()]\s*)(?:command\s+)?(?:env\s+(?:[A-Za-z_][A-Za-z0-9_]*=[^\s;&|()]+\s+)*)?(?:"[^"]*\/oc-commit"|'[^']*\/oc-commit'|[^\s;&|()]*\/oc-commit|oc-commit)(?=\s|$)/;

function hookKey(input) {
  const sessionID = String(input?.sessionID || "");
  const callID = String(input?.callID || "");
  return sessionID && callID ? `${sessionID}:${callID}` : "";
}

function toolName(input) {
  return typeof input?.tool === "string"
    ? input.tool
    : input?.tool?.name || input?.name || "";
}

function toolArgs(input) {
  return input?.args || input?.arguments || input?.tool?.args || input?.tool?.arguments || {};
}

function commandCwd(input, context) {
  const args = toolArgs(input);
  const configured = args.workdir || args.cwd;
  const base = context.directory || context.worktree || process.cwd();
  return configured ? path.resolve(base, configured) : path.resolve(base);
}

function gitEnvironment() {
  return Object.fromEntries(
    Object.entries(process.env).filter(([name]) => !name.toUpperCase().startsWith("GIT_"))
  );
}

async function git(cwd, args) {
  const result = await execFileAsync("git", args, {
    cwd,
    env: gitEnvironment(),
    encoding: "utf8",
    timeout: 2000,
  });
  return result.stdout.trim();
}

async function usableHelper(root) {
  for (const relative of ["assets/agent-wt-merge", ".opencode/bin/agent-wt-merge"]) {
    const candidate = path.join(root, relative);
    try {
      await access(candidate, process.platform === "win32" ? fsConstants.F_OK : fsConstants.X_OK);
      return candidate;
    } catch {}
  }
  return "";
}

async function requiredJob(root) {
  const raw = await readFile(
    path.join(root, "configs", "gitlab-pipeline-guard.json"),
    "utf8"
  );
  const policy = JSON.parse(raw);
  let api;
  try {
    api = new URL(policy?.api_url);
  } catch {
    return "";
  }
  if (
    !policy ||
    policy.$schema !== POLICY_SCHEMA ||
    policy.schema_version !== 1 ||
    !["http:", "https:"].includes(api.protocol) ||
    !api.hostname ||
    !Number.isInteger(policy.project_id) ||
    policy.project_id <= 0 ||
    typeof policy.guarded_remote !== "string" ||
    !REMOTE_PATTERN.test(policy.guarded_remote) ||
    typeof policy.guarded_ref !== "string" ||
    !REF_PATTERN.test(policy.guarded_ref) ||
    typeof policy.required_job !== "string" ||
    !JOB_PATTERN.test(policy.required_job) ||
    !Number.isInteger(policy.timeout_seconds) ||
    policy.timeout_seconds < 1 ||
    policy.timeout_seconds > 30
  ) {
    return "";
  }
  return policy.required_job;
}

async function inspectEligibleRepo(cwd) {
  const root = await git(cwd, ["rev-parse", "--show-toplevel"]);
  const branch = await git(root, ["symbolic-ref", "--quiet", "--short", "HEAD"]);
  if (
    !branch ||
    branch === "main" ||
    branch === "master" ||
    !BRANCH_PATTERN.test(branch) ||
    CONTROL_CHARACTER_PATTERN.test(root)
  ) return null;
  const helper = await usableHelper(root);
  if (!helper || CONTROL_CHARACTER_PATTERN.test(helper)) return null;
  const required_job = await requiredJob(root);
  if (!required_job) return null;
  const sha = await git(root, ["rev-parse", "HEAD"]);
  if (!SHA_PATTERN.test(sha)) return null;
  return {
    root: path.resolve(root),
    branch,
    sha,
    helper: path.resolve(helper),
    required_job,
  };
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", `'"'"'`)}'`;
}

function instruction(state, sha) {
  return (
    `You are the active coding agent. Post-commit CI gate for feature ${state.branch} at ${sha}: ` +
    "commit approval did not authorize network publication. Your next action is to request permission " +
    `to run ${shellQuote(state.helper)} prepare-ci from ${shellQuote(state.root)}. Use only that helper; ` +
    "do not run raw git push, and never push main or tags. If permission is denied or prepare-ci reports " +
    "failure or timeout, stop and report the outcome; do not merge. Continue to agent-wt-merge inspect " +
    `and the selected ff/no-ff only after prepare-ci reports exact-SHA ${state.required_job} success or an explicit ` +
    "override bypass. Treat bypass as not CI success; the merge helper must retain the remote feature branch."
  );
}

export default async function OpenCodeFeatureCiReminder(context) {
  return {
    name: "opencode-feature-ci-reminder@1.0.0",

    "tool.execute.before": async (input) => {
      const key = hookKey(input);
      if (!key) return;
      states.delete(key);
      const args = toolArgs(input);
      if (toolName(input) !== "bash" || !WRAPPER_PATTERN.test(String(args.command || ""))) return;
      try {
        const state = await inspectEligibleRepo(commandCwd(input, context));
        if (state) states.set(key, state);
      } catch {}
    },

    "tool.execute.after": async (input, output) => {
      const key = hookKey(input);
      if (!key) return;
      const state = states.get(key);
      states.delete(key);
      if (!state || Number(output?.metadata?.exit) !== 0) return;
      try {
        const current = await inspectEligibleRepo(state.root);
        if (
          !current ||
          current.root !== state.root ||
          current.branch !== state.branch ||
          current.helper !== state.helper ||
          current.required_job !== state.required_job ||
          current.sha === state.sha
        ) {
          return;
        }
        const reminder = instruction(state, current.sha);
        const existing = typeof output.output === "string" ? output.output : "";
        output.output = existing ? `${existing}\n\n${reminder}` : reminder;
      } catch {}
    },
  };
}
