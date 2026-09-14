import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { copyFile, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

const repoRoot = process.env.DOTFILES_TEST_REPO;
if (!repoRoot) throw new Error("DOTFILES_TEST_REPO is required");

const managedPluginDir = path.join(repoRoot, "private_dot_config", "opencode", "plugins");
let importSequence = 0;

async function managedFixture(t, names) {
  const root = await mkdtemp(path.join(os.tmpdir(), "managed-ai-adapters-"));
  const pluginDir = path.join(root, "plugins");
  const packageDir = path.join(root, "node_modules", "@opencode-ai", "plugin");
  await mkdir(pluginDir, { recursive: true });
  await mkdir(packageDir, { recursive: true });
  await writeFile(path.join(packageDir, "package.json"), JSON.stringify({ type: "module", exports: "./index.js" }));
  await writeFile(
    path.join(packageDir, "index.js"),
    `const chain = () => new Proxy(() => {}, { get: () => chain(), apply: () => chain() });
export function tool(definition) { return definition; }
tool.schema = chain();
`
  );
  for (const name of names) {
    await copyFile(path.join(managedPluginDir, name), path.join(pluginDir, `${name}.mjs`));
  }
  t.after(() => rm(root, { recursive: true, force: true }));
  return {
    root,
    path: (...parts) => path.join(root, ...parts),
    importPlugin: (name) => import(`${pathToFileURL(path.join(pluginDir, `${name}.mjs`)).href}?fixture=${importSequence += 1}`),
  };
}

function saveEnvironment(t, names) {
  const original = new Map(names.map((name) => [name, process.env[name]]));
  t.after(() => {
    for (const [name, value] of original) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });
}

test("permission capture records redacted prompts and protects control tools", async (t) => {
  const names = [
    "HOME",
    "OPENCODE_PERMISSION_CAPTURE",
    "OPENCODE_PERMISSION_CAPTURE_FILE",
    "OPENCODE_PERMISSION_CAPTURE_MAX_TEXT_CHARS",
  ];
  saveEnvironment(t, names);
  const stateSymbol = Symbol.for("opencode-permission-capture.state");
  delete globalThis[stateSymbol];
  t.after(() => delete globalThis[stateSymbol]);
  const fixture = await managedFixture(t, ["opencode-permission-capture.js"]);
  const captureFile = fixture.path("state", "prompts.jsonl");
  process.env.HOME = fixture.root;
  process.env.OPENCODE_PERMISSION_CAPTURE_FILE = captureFile;
  process.env.OPENCODE_PERMISSION_CAPTURE_MAX_TEXT_CHARS = "160";
  delete process.env.OPENCODE_PERMISSION_CAPTURE;

  const { default: plugin } = await fixture.importPlugin("opencode-permission-capture.js");
  const hooks = await plugin({ directory: "/work/repo", worktree: "/work/repo" });
  const tools = hooks.tool;
  assert.match(await tools.permission_capture_start.execute({ confirm: "no" }), /not started/);

  await hooks["permission.ask"]({ permission: "bash" }, { status: "ask" });
  await assert.rejects(readFile(captureFile, "utf8"), { code: "ENOENT" });
  assert.match(
    await tools.permission_capture_start.execute({ confirm: "start", scope: "bash", file: captureFile }),
    /"enabled": true/
  );

  const secret = "fixture-secret-value-123456";
  await hooks["permission.ask"](
    {
      permission: "bash",
      sessionID: "session-1",
      title: `run TOKEN=${secret}`,
      metadata: { input: { command: `curl -H 'Authorization: Bearer ${secret}' --api-key ${secret}` } },
      patterns: [`API_KEY=${secret}`],
    },
    { status: "ask" }
  );
  await hooks["permission.ask"]({ permission: "edit" }, { status: "ask" });
  await hooks["permission.ask"]({ permission: "bash" }, { status: "allow" });

  const raw = await readFile(captureFile, "utf8");
  assert.equal(raw.trim().split("\n").length, 1);
  assert.doesNotMatch(raw, new RegExp(secret));
  assert.match(raw, /<redacted>/);
  if (process.platform !== "win32") assert.equal((await stat(captureFile)).mode & 0o777, 0o600);

  await writeFile(captureFile, `${raw}TOKEN=${secret}\n`, "utf8");
  const report = JSON.parse(await tools.permission_capture_read.execute({ limit: 2 }));
  assert.equal(report.returned, 2);
  assert.equal(report.records[1].parseError, true);
  assert.doesNotMatch(JSON.stringify(report), new RegExp(secret));
  assert.match(await tools.permission_capture_clear.execute({ confirm: "wrong" }), /not cleared/);
  assert.match(
    await tools.permission_capture_clear.execute({ confirm: "clear permission capture" }),
    /"writtenThisProcess": 0/
  );
  assert.equal(await readFile(captureFile, "utf8"), "");
  assert.match(await tools.permission_capture_stop.execute({ confirm: "stop" }), /"enabled": false/);
});

test("cited web search validates inputs and maps OpenAI requests", async (t) => {
  saveEnvironment(t, ["OPENCODE_AUTH_CONTENT"]);
  const fixture = await managedFixture(t, ["opencode-websearch-cited-compat.js"]);
  const { default: plugin } = await fixture.importPlugin("opencode-websearch-cited-compat.js");
  const unconfigured = await plugin();
  await assert.rejects(unconfigured.tool.websearch_cited.execute({ query: "x" }), /Missing web search model/);
  await assert.rejects(unconfigured.tool.websearch_cited.execute({ query: " " }), /cannot be empty/);
  await assert.rejects(unconfigured.tool.websearch_cited.execute({ query: "x", extra: true }), /Unknown argument/);

  const calls = [];
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  process.env.OPENCODE_AUTH_CONTENT = JSON.stringify({ openai: { type: "api", key: "dummy-openai-key" } });
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response(
      JSON.stringify({ output: [{ content: [{ type: "output_text", text: "Answer [1]\n\nSources:\n[1] https://example.test" }] }] }),
      { status: 200, headers: { "content-type": "application/json" } }
    );
  };

  const hooks = await plugin();
  await hooks.config({
    provider: {
      openai: {
        options: { websearch_cited: { model: "gpt-test" }, reasoningEffort: "medium" },
      },
    },
  });
  const result = await hooks.tool.websearch_cited.execute({ query: "test query" });
  assert.match(result, /Answer \[1\]/);
  assert.match(result, /Sources:/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.openai.com/v1/responses");
  assert.equal(calls[0].init.headers.Authorization, "Bearer dummy-openai-key");
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.model, "gpt-test");
  assert.equal(body.reasoning.effort, "medium");
  assert.match(body.input[0].content[0].text, /test query/);
});

test("cited web search falls back and deduplicates Anthropic citations", async (t) => {
  saveEnvironment(t, ["OPENCODE_AUTH_CONTENT"]);
  const fixture = await managedFixture(t, ["opencode-websearch-cited-compat.js"]);
  const { default: plugin } = await fixture.importPlugin("opencode-websearch-cited-compat.js");
  process.env.OPENCODE_AUTH_CONTENT = JSON.stringify({
    openai: { type: "api", key: "dummy-openai-key" },
    anthropic: { type: "api", key: "dummy-anthropic-key" },
  });
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    if (String(url).includes("openai.com")) return new Response("unavailable", { status: 503 });
    return Response.json({
      content: [
        { type: "text", text: "First", citations: [{ url: "https://source.test", title: "Source" }] },
        { type: "text", text: " second", citations: [{ url: "https://source.test", title: "Source" }] },
      ],
    });
  };
  const hooks = await plugin();
  await hooks.config({
    provider: {
      openai: { options: { websearch_cited: { model: "gpt-test" } } },
      anthropic: { options: { websearch_cited: { model: "claude-test" } } },
    },
  });
  const result = await hooks.tool.websearch_cited.execute({ query: "fallback" });
  assert.equal(calls.length, 2);
  assert.match(calls[1].url, /api\.anthropic\.com/);
  assert.equal(calls[1].init.headers["x-api-key"], "dummy-anthropic-key");
  assert.equal(result, "First[1] second[1]\n\nSources:\n[1] Source (https://source.test)");
});

test("quota compatibility caches usage without persisting bearer tokens", async (t) => {
  const envNames = [
    "HOME",
    "OPENCODE_QUOTA_ANTHROPIC_AUTH_REFRESH",
    "OPENCODE_QUOTA_ANTHROPIC_CACHE_TTL_MS",
  ];
  saveEnvironment(t, envNames);
  const wrappedSymbol = Symbol.for("opencode-quota-anthropic-compat.wrappedFetch");
  const stateSymbol = Symbol.for("opencode-quota-anthropic-compat.state");
  delete globalThis[wrappedSymbol];
  delete globalThis[stateSymbol];
  t.after(() => {
    delete globalThis[wrappedSymbol];
    delete globalThis[stateSymbol];
  });
  const fixture = await managedFixture(t, ["opencode-quota-anthropic-compat.js"]);
  process.env.HOME = fixture.root;
  process.env.OPENCODE_QUOTA_ANTHROPIC_AUTH_REFRESH = "0";
  process.env.OPENCODE_QUOTA_ANTHROPIC_CACHE_TTL_MS = "60000";
  const originalFetch = globalThis.fetch;
  let liveCalls = 0;
  globalThis.fetch = async (url) => {
    liveCalls += 1;
    return Response.json({ source: String(url), call: liveCalls });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const { default: plugin } = await fixture.importPlugin("opencode-quota-anthropic-compat.js");
  assert.equal((await plugin({})).name, "opencode-quota-anthropic-compat");
  const endpoint = "https://api.anthropic.com/api/oauth/usage";
  const token = "fixture-bearer-token";
  const first = await globalThis.fetch(endpoint, { headers: { Authorization: `Bearer ${token}` } });
  assert.equal((await first.json()).call, 1);
  const second = await globalThis.fetch(endpoint, { headers: { Authorization: `Bearer ${token}` } });
  assert.equal((await second.json()).call, 1);
  assert.equal(liveCalls, 1);

  await globalThis.fetch(endpoint, { method: "POST", headers: { Authorization: `Bearer ${token}` } });
  await globalThis.fetch("https://example.test/other");
  assert.equal(liveCalls, 3);

  const cachePath = fixture.path(".local", "state", "opencode", "opencode-quota-anthropic-compat.json");
  const cacheRaw = await readFile(cachePath, "utf8");
  assert.doesNotMatch(cacheRaw, new RegExp(token));
  assert.equal(JSON.parse(cacheRaw).authFingerprint, createHash("sha256").update(token).digest("hex"));

  const third = await globalThis.fetch(endpoint, { headers: { Authorization: "Bearer different-token" } });
  assert.equal((await third.json()).call, 4);
});
