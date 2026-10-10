import assert from "node:assert/strict";
import { chmod, mkdir, open, readFile, rename, rm, stat, symlink, utimes, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { createPluginFixture, wait } from "./node-plugin-fixture.mjs";

const markerName = "review-loop-marker.js";
const enforcerName = "review-loop-enforcer.js";
const gateName = "review-loop-gate.js";
process.env.GIT_CONFIG_GLOBAL = process.platform === "win32" ? "NUL" : os.devNull;
process.env.GIT_CONFIG_NOSYSTEM = "1";
for (const key of ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"]) {
  delete process.env[key];
}

async function exists(filePath) {
  try {
    await stat(filePath);
    return true;
  } catch {
    return false;
  }
}

async function writeGate(filePath, files = ["src/a.js"]) {
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, JSON.stringify({ timestamp: 1, files }) + "\n");
}

function git(root, ...args) {
  return execFileSync("git", ["-C", root, ...args], { encoding: "utf8" });
}

async function bashFixture(t) {
  const fixture = await createPluginFixture([markerName]);
  t.after(() => fixture.cleanup());
  git(fixture.root, "-c", "init.templateDir=", "-c", "init.defaultBranch=main", "init", "-q");
  await mkdir(fixture.path("src"));
  await writeFile(fixture.path("src", "tracked.js"), "original\n");
  await writeFile(fixture.path("src", "second.js"), "second\n");
  await writeFile(fixture.path("src", "mode.sh"), "#!/bin/sh\n");
  await symlink("tracked.js", fixture.path("src", "link.js"));
  git(fixture.root, "add", ".");
  git(fixture.root, "-c", "commit.gpgsign=false", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture");
  const { default: marker } = await fixture.importPlugin(markerName);
  const hooks = await marker({ worktree: fixture.root });
  const invoke = (sessionID, callID) => ({ tool: "bash", sessionID, callID });
  const gate = (sessionID) => fixture.path(".opencode", `.needs_dotfiles_review.${sessionID}`);
  const files = async (sessionID) => JSON.parse(await readFile(gate(sessionID), "utf8")).files;
  return { fixture, hooks, invoke, gate, files };
}

test("Bash hooks mark only newly changed reviewable dirty content", async (t) => {
  const { fixture, hooks, invoke, gate, files } = await bashFixture(t);
  const run = async (callID, mutate, output = {}) => {
    const input = invoke("bash-session", callID);
    await hooks["tool.execute.before"](input, { args: {} });
    await mutate();
    await hooks["tool.execute.after"](input, output);
  };
  await run("read-only", async () => { git(fixture.root, "status", "--short"); });
  assert.equal(await exists(gate("bash-session")), false);
  await run("touch", async () => {
    const now = new Date(Date.now() + 1000);
    await utimes(fixture.path("src", "tracked.js"), now, now);
  });
  assert.equal(await exists(gate("bash-session")), false);
  await writeFile(fixture.path("src", "tracked.js"), "previous dirty\n");
  await run("unchanged-dirty", async () => {});
  assert.equal(await exists(gate("bash-session")), false);
  await run("changed-dirty", async () => {
    await writeFile(fixture.path("src", "tracked.js"), "changed dirty\n");
  }, { metadata: { exit: 1 } });
  assert.deepEqual(await files("bash-session"), ["src/tracked.js"]);
  await rm(gate("bash-session"));
  await run("restore", async () => {
    await writeFile(fixture.path("src", "tracked.js"), "original\n");
  });
  assert.equal(await exists(gate("bash-session")), false);
  await run("clean-edit", async () => {
    await writeFile(fixture.path("src", "second.js"), "changed\n");
  });
  assert.deepEqual(await files("bash-session"), ["src/second.js"]);
});

test("Bash hooks handle creation, deletion, rename, symlink, and mode", async (t) => {
  const { fixture, hooks, invoke, gate, files } = await bashFixture(t);
  const run = async (callID, mutate) => {
    const input = invoke("kinds", callID);
    await hooks["tool.execute.before"](input);
    await mutate();
    await hooks["tool.execute.after"](input);
  };
  await run("create-delete", async () => {
    await writeFile(fixture.path("src", "transient"), "temporary");
    await rm(fixture.path("src", "transient"));
  });
  assert.equal(await exists(gate("kinds")), false);
  await run("create", async () => { await writeFile(fixture.path("src", "new.js"), "new"); });
  assert.deepEqual(await files("kinds"), ["src/new.js"]);
  await rm(gate("kinds"));
  await run("delete", async () => { await rm(fixture.path("src", "second.js")); });
  assert.deepEqual(await files("kinds"), ["src/second.js"]);
  await rm(gate("kinds"));
  await run("rename", async () => {
    await rename(fixture.path("src", "tracked.js"), fixture.path("src", "renamed.js"));
  });
  assert.deepEqual(await files("kinds"), ["src/renamed.js", "src/tracked.js"]);
  await rm(gate("kinds"));
  await run("mode-link", async () => {
    await chmod(fixture.path("src", "mode.sh"), 0o755);
    if (process.platform === "win32") git(fixture.root, "update-index", "--chmod=+x", "src/mode.sh");
    await rm(fixture.path("src", "link.js"));
    await symlink("renamed.js", fixture.path("src", "link.js"));
  });
  assert.deepEqual(await files("kinds"), ["src/link.js", "src/mode.sh"]);
});

test("Bash hooks filter exempt paths and retain independent concurrent sessions", async (t) => {
  const { fixture, hooks, invoke, gate, files } = await bashFixture(t);
  const a = invoke("session-a", "shared-call");
  const b = invoke("session-b", "shared-call");
  await hooks["tool.execute.before"](a);
  await writeFile(fixture.path("src", "tracked.js"), "changed by a\n");
  await hooks["tool.execute.before"](b);
  await writeFile(fixture.path("src", "second.js"), "changed by b\n");
  await hooks.event({ event: { type: "message.updated", properties: { sessionID: "session-c" } } });
  await hooks["tool.execute.after"](b);
  assert.deepEqual(await files("session-b"), ["src/second.js"]);
  await hooks["tool.execute.after"](a);
  assert.deepEqual(await files("session-a"), ["src/second.js", "src/tracked.js"]);
  assert.equal(await exists(gate("session-c")), false);

  await rm(gate("session-a"));
  const c = invoke("session-a", "exempt");
  await hooks["tool.execute.before"](c);
  await mkdir(fixture.path("docs"));
  await writeFile(fixture.path("docs", "guide.md"), "exempt");
  await writeFile(fixture.path(".opencode", ".needs_dotfiles_review.noise"), "runtime");
  await hooks["tool.execute.after"](c);
  assert.equal(await exists(gate("session-a")), false);
  await hooks["tool.execute.before"](invoke("session-a", "ignored-tool"));
  await hooks["tool.execute.after"]({ tool: "other", sessionID: "session-a", callID: "ignored-tool" });
  assert.equal(await exists(gate("session-a")), false);
});

test("parallel Bash calls in one session merge their scoped file lists", async (t) => {
  const { fixture, hooks, invoke, files } = await bashFixture(t);
  const first = invoke("same", "one");
  const second = invoke("same", "two");
  await Promise.all([
    hooks["tool.execute.before"](first),
    hooks["tool.execute.before"](second),
  ]);
  await writeFile(fixture.path("src", "tracked.js"), "changed\n");
  await writeFile(fixture.path("src", "second.js"), "changed\n");
  await Promise.all([
    hooks["tool.execute.after"](first),
    hooks["tool.execute.after"](second),
  ]);
  assert.deepEqual(await files("same"), ["src/second.js", "src/tracked.js"]);
});

test("Bash snapshots do not invent gates when Git is unavailable or absent", async (t) => {
  const { fixture, hooks, invoke, gate } = await bashFixture(t);
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (message) => warnings.push(message);
  t.after(() => { console.warn = originalWarn; });
  const beforeFailure = invoke("missing-git", "before");
  const gitDir = fixture.path(".git");
  const parked = fixture.path("git-parked");
  await rename(gitDir, parked);
  await hooks["tool.execute.before"](beforeFailure);
  await rename(parked, gitDir);
  await writeFile(fixture.path("src", "tracked.js"), "changed\n");
  await hooks["tool.execute.after"](beforeFailure);
  assert.equal(await exists(gate("missing-git")), false);

  const afterFailure = invoke("missing-git", "after");
  await hooks["tool.execute.before"](afterFailure);
  await rename(gitDir, parked);
  await hooks["tool.execute.after"](afterFailure);
  await rename(parked, gitDir);
  assert.equal(await exists(gate("missing-git")), false);
  await hooks["tool.execute.after"](invoke("missing-git", "unpaired"));
  assert.equal(await exists(gate("missing-git")), false);
  assert.equal(warnings.length, 2);
  assert.match(warnings[0], /Bash before snapshot failed/);
  assert.match(warnings[1], /Bash after snapshot failed/);
  const control = invoke("missing-git", "working");
  await hooks["tool.execute.before"](control);
  await writeFile(fixture.path("src", "second.js"), "control\n");
  await hooks["tool.execute.after"](control);
  assert.equal(await exists(gate("missing-git")), true);
});

test("oversize dirty files do not become proven edits", async (t) => {
  const { fixture, hooks, invoke, files } = await bashFixture(t);
  const input = invoke("large", "one");
  await hooks["tool.execute.before"](input);
  const handle = await open(fixture.path("src", "huge.bin"), "w");
  try {
    await handle.truncate(65 * 1024 * 1024);
  } finally {
    await handle.close();
  }
  await writeFile(fixture.path("src", "tracked.js"), "control\n");
  await hooks["tool.execute.after"](input);
  assert.deepEqual(await files("large"), ["src/tracked.js"]);
});

test("session IDs with no safe suffix keep the unsuffixed direct-event gate", async (t) => {
  const fixture = await createPluginFixture([markerName]);
  t.after(() => fixture.cleanup());
  const { default: marker } = await fixture.importPlugin(markerName);
  const hooks = await marker({ worktree: fixture.root });
  await hooks.event({ event: { type: "message.updated", properties: { sessionID: "   " } } });
  await hooks.event({ event: { type: "file.edited", path: "src/edited.js" } });
  assert.equal(await exists(fixture.path(".opencode", ".needs_dotfiles_review")), true);
});

test("marker scopes relevant edits and enforces repository boundaries", async (t) => {
  const fixture = await createPluginFixture([markerName]);
  t.after(() => fixture.cleanup());
  const toasts = [];
  const { default: marker } = await fixture.importPlugin(markerName);
  const hooks = await marker({
    worktree: fixture.root,
    client: { tui: { showToast: async (value) => toasts.push(value) } },
  });
  const unsuffixed = fixture.path(".opencode", ".needs_dotfiles_review");
  const scoped = fixture.path(".opencode", ".needs_dotfiles_review.session_one");

  await hooks.event({ event: { type: "file.edited", properties: { file: "src/first.js" } } });
  assert.equal(await exists(unsuffixed), true);
  await hooks.event({ event: { type: "message.updated", properties: { sessionID: "session one" } } });
  assert.equal(await exists(unsuffixed), false);
  assert.deepEqual(JSON.parse(await readFile(scoped, "utf8")).files, ["src/first.js"]);

  await hooks.event({ event: { type: "file.deleted", path: "docs/guide.md" } });
  await hooks.event({ event: { type: "file.renamed", properties: { newPath: "docs/agents/rules.md" } } });
  await hooks.event({ event: { type: "file.moved", properties: { oldPath: "src/old.js" } } });
  await hooks.event({ event: { type: "unknown", path: "src/ignored.js" } });
  await hooks.event({ event: null });
  const files = JSON.parse(await readFile(scoped, "utf8")).files;
  assert.deepEqual(files, ["src/first.js", "docs/agents/rules.md", "src/old.js"]);

  const sibling = `${fixture.root}-sibling`;
  await hooks.event({ event: { type: "file.edited", path: path.join(sibling, "outside.js") } });
  await hooks.event({ event: { type: "file.edited", path: fixture.path(".opencode", ".needs_dotfiles_review.noise") } });
  assert.deepEqual(JSON.parse(await readFile(scoped, "utf8")).files, files);
  assert.equal(toasts.length > 0, true);
});

test("marker resets the file list when the session changes", async (t) => {
  const fixture = await createPluginFixture([markerName]);
  t.after(() => fixture.cleanup());
  const { default: marker } = await fixture.importPlugin(markerName);
  const hooks = await marker({ worktree: fixture.root });
  await hooks.event({ event: { type: "message.updated", properties: { info: { sessionId: "one" } } } });
  await hooks.event({ event: { type: "file.edited", filePath: "src/one.js" } });
  await hooks.event({ event: { type: "message.updated", properties: { info: { sessionID: "two" } } } });
  await hooks.event({ event: { type: "file.edited", file: "src/two.js" } });
  const gate = fixture.path(".opencode", ".needs_dotfiles_review.two");
  assert.deepEqual(JSON.parse(await readFile(gate, "utf8")).files, ["src/two.js"]);
});

test("enforcer prompts once for each scoped gate version", async (t) => {
  const fixture = await createPluginFixture([enforcerName]);
  t.after(() => fixture.cleanup());
  const prompts = [];
  const { default: enforcer } = await fixture.importPlugin(enforcerName);
  const hooks = await enforcer({
    worktree: fixture.root,
    client: {
      session: {
        get: async ({ path: requestPath }) => ({ data: { id: requestPath.id, parentID: null } }),
        prompt: async (request) => prompts.push(request),
      },
      tui: { showToast: async () => {} },
    },
  });
  await hooks.event({ event: { type: "message.updated", properties: { sessionID: "session-1", info: { sessionID: "session-1", role: "user", agent: "build" } } } });
  const gate = fixture.path(".opencode", ".needs_dotfiles_review.session-1");
  await writeGate(gate, ["space name.js", "quote'name.js"]);
  await hooks.event({ event: { type: "file.edited" } });
  await hooks.event({ event: { type: "session.idle" } });
  await wait(650);
  assert.equal(prompts.length, 1);
  const text = prompts[0].body.parts[0].text;
  assert.match(text, /'space name\.js'/);
  assert.match(text, /'quote'\\''name\.js'/);
  const statePath = fixture.path(".opencode", ".dotfiles_review_enforcer_state.session-1.json");
  assert.equal(JSON.parse(await readFile(statePath, "utf8")).lastGatePath, gate);

  await hooks.event({ event: { type: "session.idle" } });
  await wait(650);
  assert.equal(prompts.length, 1);
  const future = new Date(Date.now() + 2000);
  await utimes(gate, future, future);
  await hooks.event({ event: { type: "session.idle" } });
  await wait(650);
  assert.equal(prompts.length, 2);
});

test("enforcer handles legacy gates, malformed state, and prompt failures", async (t) => {
  const fixture = await createPluginFixture([enforcerName]);
  t.after(() => fixture.cleanup());
  const inserted = [];
  const { default: enforcer } = await fixture.importPlugin(enforcerName);
  const hooks = await enforcer({
    worktree: fixture.root,
    client: {
      tui: {
        showToast: async () => {},
        clearPrompt: async () => {},
        appendPrompt: async (request) => inserted.push(request),
      },
    },
  });
  const legacy = fixture.path(".claude", ".needs_dotfiles_review");
  await writeGate(legacy);
  await writeFile(fixture.path(".opencode", ".dotfiles_review_enforcer_state.json"), "not json");
  await hooks.event({ event: { type: "session.idle" } });
  await wait(650);
  assert.equal(inserted.length, 1);
  assert.equal(await exists(fixture.path(".opencode", ".dotfiles_review_enforcer_state.json")), true);

  const failingFixture = await createPluginFixture([enforcerName]);
  t.after(() => failingFixture.cleanup());
  const { default: failingEnforcer } = await failingFixture.importPlugin(enforcerName);
  const failingHooks = await failingEnforcer({
    worktree: failingFixture.root,
    client: {
      session: {
        get: async ({ path: requestPath }) => ({ id: requestPath.id, parentID: null }),
        prompt: async () => { throw new Error("prompt failed"); },
      },
    },
  });
  await failingHooks.event({ event: { type: "message.updated", properties: { sessionID: "top", info: { sessionID: "top", role: "user", agent: "build" } } } });
  await writeGate(failingFixture.path(".opencode", ".needs_dotfiles_review.top"));
  await failingHooks.event({ event: { type: "session.idle" } });
  await wait(650);
  assert.equal(await exists(failingFixture.path(".opencode", ".dotfiles_review_enforcer_state.top.json")), true);

  const tuiFixture = await createPluginFixture([enforcerName]);
  t.after(() => tuiFixture.cleanup());
  const { default: tuiEnforcer } = await tuiFixture.importPlugin(enforcerName);
  const tuiHooks = await tuiEnforcer({
    worktree: tuiFixture.root,
    client: { tui: {
      showToast: async () => { throw new Error("toast failed"); },
      clearPrompt: async () => { throw new Error("clear failed"); },
      appendPrompt: async () => { throw new Error("append failed"); },
    } },
  });
  await writeGate(tuiFixture.path(".claude", ".needs_dotfiles_review"));
  await tuiHooks.event({ event: { type: "session.idle" } });
  await wait(650);
  assert.equal(await exists(tuiFixture.path(".opencode", ".dotfiles_review_enforcer_state.json")), true);
});

test("enforcer skips reviewer and child sessions", async (t) => {
  for (const [name, session] of [
    ["reviewer", { id: "reviewer", parentID: null, agent: "dotfiles-reviewer" }],
    ["child", { id: "child", parentID: "parent", agent: "build" }],
  ]) {
    const fixture = await createPluginFixture([enforcerName]);
    t.after(() => fixture.cleanup());
    const prompts = [];
    const { default: enforcer } = await fixture.importPlugin(enforcerName);
    const hooks = await enforcer({ worktree: fixture.root, client: { session: { prompt: async (value) => prompts.push(value) } } });
    await hooks.event({ event: { type: "session.created", properties: { info: session } } });
    await hooks.event({ event: { type: "message.updated", properties: { sessionID: name, info: { sessionID: name, role: "user", agent: session.agent } } } });
    await writeGate(fixture.path(".opencode", `.needs_dotfiles_review.${name}`));
    await hooks.event({ event: { type: "session.idle" } });
    await wait(600);
    assert.equal(prompts.length, 0, name);
  }
});

test("gate clears scoped, unsuffixed, legacy, and state files only on strict PASS", async (t) => {
  const fixture = await createPluginFixture([gateName]);
  t.after(() => fixture.cleanup());
  const { default: gatePlugin } = await fixture.importPlugin(gateName);
  const hooks = await gatePlugin({ worktree: fixture.root });
  const paths = [
    fixture.path(".opencode", ".needs_dotfiles_review"),
    fixture.path(".opencode", ".needs_dotfiles_review.session_one"),
    fixture.path(".opencode", ".dotfiles_review_enforcer_state.session_one.json"),
    fixture.path(".claude", ".needs_dotfiles_review"),
  ];
  for (const filePath of paths) await writeGate(filePath);
  const prefix = "DOTFILES_REVIEWER_RESULT";
  for (const text of [
    `${prefix}=PASS\nmore`,
    `quoted ${prefix}=PASS`,
    `${prefix}=PASS\n${prefix}=PASS`,
    `${prefix}=PASS\n${prefix}=FAIL`,
    `${prefix}=FAIL`,
  ]) {
    await hooks.event({ event: { type: "message.updated", properties: { sessionID: "session one", info: { role: "assistant", text } } } });
    assert.equal(await exists(paths[1]), true, text);
  }
  await hooks.event({ event: { type: "message.part.updated", properties: { sessionID: "session one", part: { prompt: `${prefix}=PASS`, text: `review complete\n${prefix}=PASS\n<task_metadata>\ntask_id: 1\n</task_metadata>` } } } });
  for (const filePath of paths) assert.equal(await exists(filePath), false, filePath);
});

test("gate honors reviewer task filtering and output precedence", async (t) => {
  const fixture = await createPluginFixture([gateName]);
  t.after(() => fixture.cleanup());
  const { default: gatePlugin } = await fixture.importPlugin(gateName);
  const hooks = await gatePlugin({ worktree: fixture.root });
  const sentinel = fixture.path(".opencode", ".needs_dotfiles_review");
  await writeGate(sentinel);
  await hooks["tool.execute.after"](
    { tool: { name: "task", arguments: { subagent_type: "other", output: "DOTFILES_REVIEWER_RESULT=PASS" } } },
    "DOTFILES_REVIEWER_RESULT=PASS"
  );
  assert.equal(await exists(sentinel), true);
  await hooks["tool.execute.after"](
    { tool: { name: "task", arguments: { subagent_type: "dotfiles-reviewer" } }, result: "DOTFILES_REVIEWER_RESULT=FAIL" },
    { text: "done\nDOTFILES_REVIEWER_RESULT=PASS" }
  );
  assert.equal(await exists(sentinel), false);
  await hooks.event({ event: null });
});
