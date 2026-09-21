import assert from "node:assert/strict";
import fs, { watch } from "node:fs";
import promises from "node:fs/promises";
import { mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { EventEmitter } from "node:events";
import { syncBuiltinESMExports } from "node:module";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { createPluginFixture, startEditorPlugin, wait } from "./node-plugin-fixture.mjs";

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

async function assertFileContentEventually(filePath, expected, timeout = 2000) {
  const deadline = Date.now() + timeout;
  let actual;
  do {
    actual = await readFile(filePath, "utf8");
    if (actual === expected) return;
    await wait(20);
  } while (Date.now() < deadline);
  assert.equal(actual, expected);
}

async function assertFileContentRemains(filePath, expected, duration = 750) {
  const deadline = Date.now() + duration;
  do {
    assert.equal(await readFile(filePath, "utf8"), expected);
    await wait(20);
  } while (Date.now() < deadline);
  assert.equal(await readFile(filePath, "utf8"), expected);
}

async function runChild(source, env) {
  const child = spawn(process.execPath, ["--input-type=module", "--eval", source], {
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const closed = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  let timeoutHandle;
  let result;
  try {
    result = await Promise.race([
      closed,
      new Promise((resolve) => {
        timeoutHandle = setTimeout(() => resolve({ timeout: true }), 3000);
      }),
    ]);
  } finally {
    clearTimeout(timeoutHandle);
  }
  if (result.timeout) {
    child.kill("SIGKILL");
    await closed;
    assert.fail(`child process timed out\nstdout:\n${stdout}\nstderr:\n${stderr}`);
  }
  assert.equal(result.code, 0, `child exited with ${result.code} (${result.signal})\nstdout:\n${stdout}\nstderr:\n${stderr}`);
  return stdout;
}

test("editor startup restores watch bindings and closes watchers on failure", async (t) => {
  const watcher = { close: t.mock.fn() };
  t.mock.method(fs, "watch", () => watcher);
  const originalWatch = fs.watch;
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
  await assert.rejects(startEditorPlugin(async () => {
    watch(os.tmpdir());
    throw new Error("startup failed");
  }, {}), /startup failed/);
  assert.equal(fs.watch, originalWatch);
  assert.equal(watch, originalWatch);
  assert.equal(watcher.close.mock.callCount(), 1);
});

test("editor startup waits for an observed probe before returning", async (t) => {
  const watcher = new EventEmitter();
  watcher.close = t.mock.fn();
  t.mock.method(fs, "watch", () => watcher);
  const originalWatch = fs.watch;
  let attempts = 0;
  let probe;
  t.mock.method(promises, "writeFile", async (filePath) => {
    probe = filePath;
    assert.equal(path.extname(probe), ".txt");
    assert.equal(watch, originalWatch);
    if (++attempts === 2) watcher.emit("change", "change", path.basename(probe));
  });
  const remove = t.mock.method(promises, "rm", async () => {});
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
  const close = await startEditorPlugin(async () => {
    watch(fs.realpathSync(os.tmpdir()));
  }, {});
  if (process.platform === "linux") {
    assert.equal(attempts, 0);
    assert.equal(remove.mock.callCount(), 0);
  } else {
    assert.equal(attempts, 2);
    assert.deepEqual(remove.mock.calls[0].arguments, [probe, { force: true }]);
  }
  assert.equal(watcher.listenerCount("change"), 0);
  assert.equal(watcher.close.mock.callCount(), 0);
  close();
  assert.equal(watcher.close.mock.callCount(), 1);
});

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
  await assertFileContentRemains(disabled, "body\n");
  delete process.env.OPENCODE_EDITOR_MD_SUPPRESS;

  const seeded = path.join(os.tmpdir(), `seeded-${process.pid}.md`);
  await writeFile(seeded, "seeded\n");
  t.after(() => rm(seeded, { force: true }));

  const fixture = await createPluginFixture([editorName], { editorSuppressHeader: "<!-- custom -->" });
  t.after(() => fixture.cleanup());
  const { default: editor } = await importFresh(fixture, editorName);
  t.after(await startEditorPlugin(editor, { worktree: fixture.root }));
  const fresh = path.join(os.tmpdir(), `fresh-${process.pid}-${Date.now()}.md`);
  const ignored = path.join(os.tmpdir(), `fresh-${process.pid}-${Date.now()}.txt`);
  const suppressed = path.join(os.tmpdir(), `suppressed-${process.pid}-${Date.now()}.md`);
  t.after(() => Promise.all([fresh, ignored, suppressed].map((filePath) => rm(filePath, { force: true }))));
  await writeFile(fresh, "body\n");
  await writeFile(ignored, "plain\n");
  await writeFile(suppressed, "<!-- markdownlint-disable MD001 -->\nbody\n");
  await assertFileContentEventually(fresh, "<!-- custom -->\nbody\n");
  await assertFileContentRemains(fresh, "<!-- custom -->\nbody\n");
  assert.equal(await readFile(ignored, "utf8"), "plain\n");
  assert.equal(await readFile(suppressed, "utf8"), "<!-- markdownlint-disable MD001 -->\nbody\n");
  assert.equal(await readFile(seeded, "utf8"), "seeded\n");
});

test("editor polling injects and does not keep a child process alive", { skip: process.platform !== "linux" }, async (t) => {
  const fixture = await createPluginFixture([editorName], { editorSuppressHeader: undefined });
  t.after(() => fixture.cleanup());
  const pluginPath = fixture.path(".opencode", "plugins", editorName);
  const childTmp = fixture.path("child-tmp");
  const markdownPath = path.join(childTmp, "new.md");
  await mkdir(childTmp);
  const source = `
    import assert from "node:assert/strict";
    import { readFile, writeFile } from "node:fs/promises";
    import plugin from ${JSON.stringify(pathToFileURL(pluginPath).href)};
    const filePath = ${JSON.stringify(markdownPath)};
    const expected = "<!-- markdownlint-disable -->\\nbody\\n";
    await plugin({ worktree: ${JSON.stringify(fixture.root)} });
    await writeFile(filePath, "body\\n");
    const deadline = Date.now() + 2000;
    let actual;
    do {
      actual = await readFile(filePath, "utf8");
      if (actual === expected) break;
      await new Promise((resolve) => setTimeout(resolve, 20));
    } while (Date.now() < deadline);
    assert.equal(actual, expected);
    process.stdout.write(actual);
  `;
  const stdout = await runChild(source, {
    ...process.env,
    TMPDIR: childTmp,
    TMP: childTmp,
    TEMP: childTmp,
  });
  assert.equal(stdout, "<!-- markdownlint-disable -->\nbody\n");
});

test("editor watcher debounce does not keep a child process alive", { skip: process.platform === "linux" }, async (t) => {
  const fixture = await createPluginFixture([editorName], { editorSuppressHeader: undefined });
  t.after(() => fixture.cleanup());
  const pluginPath = fixture.path(".opencode", "plugins", editorName);
  const childTmp = fixture.path("child-tmp");
  const markdownPath = path.join(childTmp, "new.md");
  await mkdir(childTmp);
  const source = `
    import assert from "node:assert/strict";
    import { writeFile } from "node:fs/promises";
    import { startEditorPlugin } from ${JSON.stringify(new URL("./node-plugin-fixture.mjs", import.meta.url).href)};
    import plugin from ${JSON.stringify(pathToFileURL(pluginPath).href)};
    const originalSetTimeout = globalThis.setTimeout;
    let resolveDebounce;
    const debounceSeen = new Promise((resolve) => { resolveDebounce = resolve; });
    globalThis.setTimeout = (callback, delay, ...args) => {
      const timer = originalSetTimeout(callback, delay, ...args);
      if (delay === 250) resolveDebounce(timer);
      return timer;
    };
    await startEditorPlugin(plugin, { worktree: ${JSON.stringify(fixture.root)} });
    await writeFile(${JSON.stringify(markdownPath)}, "body\\n");
    let deadlineTimer;
    const debounceTimer = await Promise.race([
      debounceSeen,
      new Promise((_, reject) => {
        deadlineTimer = originalSetTimeout(() => reject(new Error("debounce timer was not observed")), 2000);
      }),
    ]);
    clearTimeout(deadlineTimer);
    assert.equal(debounceTimer.hasRef(), false);
    process.stdout.write("unrefed\\n");
  `;
  const stdout = await runChild(source, {
    ...process.env,
    TMPDIR: childTmp,
    TMP: childTmp,
    TEMP: childTmp,
  });
  assert.equal(stdout, "unrefed\n");
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
