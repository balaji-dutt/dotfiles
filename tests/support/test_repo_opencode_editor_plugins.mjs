import assert from "node:assert/strict";
import { mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { createPluginFixture, wait } from "./node-plugin-fixture.mjs";

const editorName = "editor-markdownlint-suppress.js";
const tapName = "event-tap.js";

async function exists(filePath) {
  try {
    await stat(filePath);
    return true;
  } catch {
    return false;
  }
}

async function importFresh(fixture, name) {
  return fixture.importPlugin(name);
}

test("editor suppression handles new, existing, configured, and disabled files", async (t) => {
  t.after(() => { delete process.env.OPENCODE_EDITOR_MD_SUPPRESS; });
  process.env.OPENCODE_EDITOR_MD_SUPPRESS = "0";
  const disabledFixture = await createPluginFixture([editorName]);
  t.after(() => disabledFixture.cleanup());
  const { default: disabledEditor } = await importFresh(disabledFixture, editorName);
  await disabledEditor({ worktree: disabledFixture.root });
  const disabled = path.join(os.tmpdir(), `disabled-${process.pid}-${Date.now()}.md`);
  t.after(() => rm(disabled, { force: true }));
  await writeFile(disabled, "body\n");
  await wait(100);
  assert.equal(await readFile(disabled, "utf8"), "body\n");
  delete process.env.OPENCODE_EDITOR_MD_SUPPRESS;

  const seeded = path.join(os.tmpdir(), `seeded-${process.pid}.md`);
  await writeFile(seeded, "seeded\n");
  t.after(() => rm(seeded, { force: true }));

  const fixture = await createPluginFixture([editorName], { editorSuppressHeader: "<!-- custom -->" });
  t.after(() => fixture.cleanup());
  const { default: editor } = await importFresh(fixture, editorName);
  await editor({ worktree: fixture.root });
  const fresh = path.join(os.tmpdir(), `fresh-${process.pid}-${Date.now()}.md`);
  const ignored = path.join(os.tmpdir(), `fresh-${process.pid}-${Date.now()}.txt`);
  const suppressed = path.join(os.tmpdir(), `suppressed-${process.pid}-${Date.now()}.md`);
  t.after(() => Promise.all([fresh, ignored, suppressed].map((filePath) => rm(filePath, { force: true }))));
  await writeFile(fresh, "body\n");
  await writeFile(ignored, "plain\n");
  await writeFile(suppressed, "<!-- markdownlint-disable MD001 -->\nbody\n");
  await wait(180);
  assert.equal(await readFile(fresh, "utf8"), "<!-- custom -->\nbody\n");
  assert.equal(await readFile(ignored, "utf8"), "plain\n");
  assert.equal(await readFile(suppressed, "utf8"), "<!-- markdownlint-disable MD001 -->\nbody\n");
  assert.equal(await readFile(seeded, "utf8"), "seeded\n");
});

test("editor polling does not keep a child process alive", async (t) => {
  const fixture = await createPluginFixture([editorName], { editorSuppressHeader: undefined });
  t.after(() => fixture.cleanup());
  const pluginPath = fixture.path(".opencode", "plugins", editorName);
  const childTmp = fixture.path("child-tmp");
  const markdownPath = path.join(childTmp, "new.md");
  await mkdir(childTmp);
  const source = `import {readFile,writeFile} from "node:fs/promises"; import plugin from ${JSON.stringify(pathToFileURL(pluginPath).href)}; await plugin({worktree:${JSON.stringify(fixture.root)}}); await writeFile(${JSON.stringify(markdownPath)},"body\\n"); await new Promise((resolve)=>setTimeout(resolve,180)); process.stdout.write(await readFile(${JSON.stringify(markdownPath)},"utf8"));`;
  const child = spawn(process.execPath, ["--input-type=module", "--eval", source], {
    env: { ...process.env, TMPDIR: childTmp, TMP: childTmp, TEMP: childTmp },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  const result = await Promise.race([
    new Promise((resolve) => child.on("exit", (code) => resolve({ code }))),
    wait(1500).then(() => ({ timeout: true })),
  ]);
  if (result.timeout) child.kill("SIGKILL");
  assert.deepEqual(result, { code: 0 });
  assert.equal(stdout, "<!-- markdownlint-disable -->\nbody\n");
});

test("event tap summarizes, throttles, redacts, and truncates", async (t) => {
  t.after(() => {
    delete process.env.OPENCODE_EVENT_TAP;
    delete process.env.OPENCODE_EVENT_TAP_FILE;
    delete process.env.OPENCODE_EVENT_TAP_DUMP;
  });
  const fixture = await createPluginFixture([tapName]);
  t.after(() => fixture.cleanup());
  const output = fixture.path("tap.log");
  process.env.OPENCODE_EVENT_TAP = "1";
  process.env.OPENCODE_EVENT_TAP_FILE = output;
  process.env.OPENCODE_EVENT_TAP_DUMP = "message.updated";
  const { default: tap } = await importFresh(fixture, tapName);
  const hooks = await tap();
  await wait(20);
  await hooks.event({ event: { type: "irrelevant", secret: "sk-secret-value-123456" } });
  const deep = { value: "safe" };
  let cursor = deep;
  for (let index = 0; index < 8; index++) cursor = cursor.next = {};
  await hooks.event({ event: {
    type: "message.updated",
    properties: { info: { sessionID: "s1", role: "assistant", agent: "build" } },
    secret: "Bearer secret-value-123456",
    array: Array.from({ length: 55 }, (_, index) => index),
    object: Object.fromEntries(Array.from({ length: 82 }, (_, index) => [`key${index}`, index])),
    deep,
  } });
  await hooks.event({ event: { type: "file.edited", path: "suppressed-by-throttle.js" } });
  await wait(130);
  await hooks.event({ event: { type: "tool.execute.after", tool: "bash", input: { command: "printf ok" } } });
  await wait(80);
  const text = await readFile(output, "utf8");
  const records = text.trim().split("\n").map((line) => JSON.parse(line.slice(line.indexOf("{"))));
  assert.equal(records.filter((record) => record.kind === "FULL_DUMP").length, 1);
  assert.equal(records.some((record) => record.type === "file.edited"), false);
  assert.equal(records.some((record) => record.type === "tool.execute.after" && record.command === "printf ok"), true);
  assert.equal(text.includes("secret-value-123456"), false);
  assert.equal(text.includes("[REDACTED]"), true);
  assert.equal(text.includes("[TRUNCATED_ARRAY]"), true);
  assert.equal(text.includes("[TRUNCATED_DEPTH]"), true);
  assert.equal(text.includes("__truncated_keys"), true);
});

test("event tap stays inert when disabled and swallows output errors", async (t) => {
  t.after(() => {
    delete process.env.OPENCODE_EVENT_TAP;
    delete process.env.OPENCODE_EVENT_TAP_FILE;
  });
  const fixture = await createPluginFixture([tapName]);
  t.after(() => fixture.cleanup());
  const output = fixture.path("disabled.log");
  delete process.env.OPENCODE_EVENT_TAP;
  process.env.OPENCODE_EVENT_TAP_FILE = output;
  const { default: tap } = await importFresh(fixture, tapName);
  const hooks = await tap();
  await hooks.event({ event: { type: "file.edited", path: "a.js" } });
  await wait(30);
  assert.equal(await exists(output), false);

  process.env.OPENCODE_EVENT_TAP = "1";
  process.env.OPENCODE_EVENT_TAP_FILE = fixture.path("missing", "tap.log");
  const failingFixture = await createPluginFixture([tapName]);
  t.after(() => failingFixture.cleanup());
  const { default: failingTap } = await importFresh(failingFixture, tapName);
  const failingHooks = await failingTap();
  await assert.doesNotReject(failingHooks.event({ event: { type: "message.updated", properties: {} } }));
  await wait(30);
});
