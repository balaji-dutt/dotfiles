import assert from "node:assert/strict";
import { mkdir, readFile, stat, utimes, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import { createPluginFixture, wait } from "./node-plugin-fixture.mjs";

const markerName = "review-loop-marker.js";
const enforcerName = "review-loop-enforcer.js";
const gateName = "review-loop-gate.js";

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
