import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import { handoff, participant, digest, portable } from '../../dot_claude/skills/agent-attestation/lib/shared/records.mjs';
import { recognize, inject } from '../../dot_claude/skills/agent-attestation/lib/shared/commands.mjs';
import { atomicJson, Ledger, readJson, stateRoot } from '../../dot_claude/skills/agent-attestation/lib/shared/storage.mjs';
import { sourceSnapshot, relativeSource } from '../../dot_claude/skills/agent-attestation/lib/shared/source.mjs';
import { response, reconcileTranscript } from '../../dot_claude/skills/agent-attestation/lib/transcript.mjs';
import { handleHook } from '../../dot_claude/skills/agent-attestation/lib/collector.mjs';
import { createCollector, responseRecord } from '../../private_dot_config/opencode/attestation/collector.mjs';
import { sourceResolver, parseJsonc } from '../../private_dot_config/opencode/attestation/sources.mjs';
import { createDiagnostics } from '../../private_dot_config/opencode/attestation/diagnostics.mjs';

const repository = fileURLToPath(new URL('../../', import.meta.url));
const windows = process.platform === 'win32';
const quiet = () => {};
const gitEnv = { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: windows ? 'NUL' : os.devNull, GIT_AUTHOR_NAME: 'Fixture', GIT_AUTHOR_EMAIL: 'fixture@example.invalid', GIT_COMMITTER_NAME: 'Fixture', GIT_COMMITTER_EMAIL: 'fixture@example.invalid' };
delete gitEnv.AI_ATTESTATION_JSON;

async function temporary(t) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'attestation ü '));
  t.after(() => fs.rm(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }));
  return dir;
}

function execute(command, args, options = {}) {
  return spawnSync(command, args, { encoding: 'utf8', timeout: 20000, shell: false, env: gitEnv, ...options });
}

function checked(command, args, options) {
  const result = execute(command, args, options);
  assert.equal(result.status, 0, `${result.error ?? ''}\n${result.stdout}\n${result.stderr}`);
  return result.stdout;
}

test('v1 serialization validates every field and preserves complete-record order', () => {
  const first = { tool: 'opencode', agent: 'build', model: 'provider/runtime' };
  const second = { ...first, model: 'provider/other' };
  const output = JSON.parse(handoff([first, first, second], 'opencode'));
  assert.deepEqual(output.participants, [first, second]);
  assert.equal(participant({ tool: ['opencode'] }), undefined);
  assert.deepEqual(participant({ tool: 'opencode', agent: 'bad\nvalue', role: 'Editor', model: 'x'.repeat(129), sourceDefinition: '../escape', sourceDigest: digest('x'), secret: 'private' }), { tool: 'opencode' });
  for (const value of ['../a', '/a', 'C:/a', 'a\\b', 'a/.b', 'a;z', 'ü/a', 'a b', '']) assert.equal(portable(value), false, value);
  assert.equal(portable('agents/reviewer.yaml'), true);
});

test('overflow preserves first eight and emits partial diagnostic', () => {
  const warnings = [];
  const records = Array.from({ length: 10 }, (_, i) => ({ tool: 'opencode', agent: `a${i}` }));
  const json = handoff(records, 'opencode', value => warnings.push(value));
  assert.deepEqual(JSON.parse(json).participants, records.slice(0, 8));
  assert.deepEqual(warnings, ['participant-limit']);
  assert.ok(Buffer.byteLength(json) <= 16384);
  assert.deepEqual(JSON.parse(handoff([], 'claude-code')).participants, [{ tool: 'claude-code' }]);
});

test('direct commands are distinguished from mentions, compounds and explicit payloads', () => {
  for (const command of ['cc-commit -m "one\ntwo"', "command cc-commit -m 'quote; $value'", "'/a path/cc-commit' -m 'message'"]) assert.ok(recognize(command, 'cc-commit'));
  for (const command of ['echo "cc-commit"', 'cd repo && cc-commit', 'cc-commit; other', 'cc-commit\nother', 'AI_ATTESTATION_JSON=x cc-commit', 'cc-commit -m "$(evil)"', 'cc-commit -m `evil`', 'cc-commit > output', 'cc-commit.cmd']) assert.equal(recognize(command, 'cc-commit'), undefined, command);
  assert.ok(recognize('& "C:\\a path\\cc-commit.ps1" -m \'one\ntwo\'', 'cc-commit', 'powershell'));
  assert.equal(recognize('& $wrapper -m x', 'cc-commit', 'powershell'), undefined);
  assert.equal(recognize('cc-commit.cmd -m x', 'cc-commit', 'powershell'), undefined);
});

test('parallel immutable records preserve all participants and isolate sessions', async t => {
  const root = await temporary(t);
  const ledger = new Ledger('opencode', 'CON:session', 'worktree', { root });
  await Promise.all(Array.from({ length: 16 }, (_, i) => ledger.record(`event${i}`, { tool: 'opencode', agent: `a${i}` }, i)));
  await ledger.record('event0', { tool: 'opencode', agent: 'replacement' }, 30);
  assert.deepEqual((await ledger.records()).map(record => record.agent), Array.from({ length: 16 }, (_, i) => `a${i}`));
  assert.deepEqual(await new Ledger('opencode', 'other', 'worktree', { root }).records(), []);
  assert.match(path.basename(ledger.directory), /^[a-f0-9]{64}$/);
  const entries = await fs.readdir(ledger.directory);
  assert.ok(entries.every(name => /^[a-f0-9]{64}\.json$/.test(name)));
});

test('Bash PowerShell bridge accepts only literal wrapper invocations', () => {
  const script = "& 'C:\\a path\\cc-commit.ps1' -m 'message'";
  const call = `pwsh -NoProfile -Command "${script}"`;
  for (const prefix of ['', 'cd "C:/a path" && ', "cd -- '/a path' && "]) {
    const command = prefix + call;
    const match = recognize(command, 'cc-commit');
    assert.ok(match, command);
    assert.equal(match.offset, prefix.length);
    assert.equal(inject(command, '{"value":"a\'b"}', 'bash', match), `${prefix}AI_ATTESTATION_JSON='{"value":"a'\\''b"}' ${call}`);
  }
  assert.ok(recognize(`'C:/Program Files/PowerShell/7/pwsh.exe' -NoLogo -NonInteractive -Command "${script}"`, 'cc-commit'));
  assert.ok(recognize(`'C:\\Program Files\\PowerShell\\7\\pwsh.exe' -NoProfile -Command "${script}"`, 'cc-commit'));
  for (const command of [
    `echo ${call}`, `cd "$HOME" && ${call}`, `cd /a* && ${call}`, `cd - && ${call}`,
    `${call} && other`, `${call}; other`, `${call} > output`, `cd /a || ${call}`,
    `${call} extra`, `env ${call}`, `AI_ATTESTATION_JSON=caller ${call}`,
    'pwsh -EncodedCommand abc', `pwsh -ExecutionPolicy Bypass -Command "${script}"`,
    'pwsh -Command "& $wrapper -m x"', 'pwsh -Command "cc-commit.ps1; other"',
    'pwsh -Command "Write-Output cc-commit.ps1"', 'pwsh -Command "cc-commit.ps1 -m $(evil)"',
    'pwsh -Command "cc-commit.ps1 -m `evil`"', 'pwsh -Command "cc-commit.ps1 | other"',
    'pwsh -Command "cc-commit.ps1 @extra"',
  ]) assert.equal(recognize(command, 'cc-commit'), undefined, command);
});

test('atomic sharing failures are bounded and retain the previous record', async t => {
  const root = await temporary(t), file = path.join(root, 'state.json');
  await atomicJson(file, { version: 1 });
  let attempts = 0;
  await assert.rejects(atomicJson(file, { version: 2 }, {
    rename: async () => { attempts++; throw Object.assign(new Error('locked'), { code: 'EPERM' }); }, sleep: async () => {},
  }));
  assert.equal(attempts, 4);
  assert.deepEqual(await readJson(file), { version: 1 });
  assert.deepEqual(await fs.readdir(root), ['state.json']);
});

test('session storage stops accepting new sessions without evicting active ledgers', async t => {
  const root = await temporary(t), warnings = [];
  const active = new Ledger('opencode', 'active', root, { root });
  await active.record('first', { tool: 'opencode', model: 'first' }, 1);
  await Promise.all(Array.from({ length: 255 }, (_, i) => fs.mkdir(new Ledger('opencode', `s${i}`, root, { root }).directory)));
  const overflow = new Ledger('opencode', 'overflow', root, { root, warn: code => warnings.push(code) });
  await overflow.record('new', { tool: 'opencode', model: 'overflow' });
  await active.record('second', { tool: 'opencode', model: 'second' }, 2);
  assert.deepEqual(await overflow.records(), []);
  assert.deepEqual(warnings, ['session-storage-limit']);
  assert.deepEqual((await active.records()).map(record => record.model), ['first', 'second']);
});

test('source snapshots require exact bytes and use sourceDigest rather than rendered digest', async t => {
  const root = await temporary(t), file = path.join(root, 'agent.md');
  const bytes = Buffer.from('prompt\r\n');
  await fs.writeFile(file, bytes);
  assert.deepEqual(await sourceSnapshot({ sourceRoot: root, sourceFile: file, loadedBytes: bytes }), { sourceDefinition: 'agent.md', sourceDigest: digest(bytes) });
  assert.deepEqual(await sourceSnapshot({ sourceRoot: root, sourceFile: file, loadedBytes: Buffer.from('prompt\n') }), {});
  await atomicJson(path.join(root, '.agentic-tooling', 'generated-manifest.json'), { files: [{ path: 'agent.md', kind: 'agent', source: 'agents/a.yaml', sourceDigest: digest('canonical'), digest: digest('rendered') }] });
  assert.deepEqual(await sourceSnapshot({ sourceRoot: root, sourceFile: file, loadedBytes: bytes, managedPath: 'agent.md' }), { sourceDefinition: 'agents/a.yaml', sourceDigest: digest('canonical') });
  await fs.writeFile(file, 'changed');
  assert.deepEqual(await sourceSnapshot({ sourceRoot: root, sourceFile: file, loadedBytes: bytes, managedPath: 'agent.md' }), {});
});

test('source roots through symlinks or junctions preserve canonical paths', async t => {
  const root = await temporary(t), source = path.join(root, 'source'), alias = path.join(root, 'alias');
  await fs.mkdir(source);
  await fs.symlink(source, alias, windows ? 'junction' : 'dir');
  const bytes = Buffer.from('definition\r\n');
  await fs.writeFile(path.join(source, 'agent.md'), bytes);
  await atomicJson(path.join(source, '.agentic-tooling', 'generated-manifest.json'), { files: [{ path: 'agent.md', kind: 'agent', source: 'agents/custom.yaml', sourceDigest: digest('upstream') }] });
  assert.deepEqual(await sourceSnapshot({ sourceRoot: alias, sourceFile: path.join(alias, 'agent.md'), loadedBytes: bytes }), { sourceDefinition: 'agents/custom.yaml', sourceDigest: digest('upstream') });
});

test('each deployed plugin loads without the other harness directory', async t => {
  const root = await temporary(t), oc = path.join(root, 'opencode'), cc = path.join(root, 'claude');
  await fs.mkdir(path.join(oc, 'plugins'), { recursive: true });
  await fs.cp(path.join(repository, 'private_dot_config/opencode/attestation'), path.join(oc, 'attestation'), { recursive: true });
  await fs.copyFile(path.join(repository, 'private_dot_config/opencode/plugins/opencode-agent-attestation.js'), path.join(oc, 'plugins/attest.mjs'));
  const plugin = (await import(pathToFileURL(path.join(oc, 'plugins/attest.mjs')).href)).default;
  const logs = [];
  const client = { ...fakeClient(), app: { log: async ({ body }) => logs.push(body) } };
  const hooks = await plugin({ client, directory: root });
  assert.equal(typeof hooks['shell.env'], 'function');
  assert.equal(typeof hooks.config, 'function');
  await hooks.event({ event: { type: 'message.updated' } });
  await hooks.event({ event: { type: 'message.updated' } });
  assert.deepEqual(logs, [{ service: 'agent-attestation', level: 'warn', message: 'partial: collection-unavailable' }]);
  await hooks.dispose();
  await fs.rm(oc, { recursive: true });
  await fs.cp(path.join(repository, 'dot_claude/skills/agent-attestation'), cc, { recursive: true });
  await fs.rename(path.join(cc, 'dot_claude-plugin'), path.join(cc, '.claude-plugin'));
  const manifest = await readJson(path.join(cc, '.claude-plugin/plugin.json'));
  assert.equal(manifest.name, 'agent-attestation');
  const configuration = await readJson(path.join(cc, 'hooks/hooks.json'));
  for (const groups of Object.values(configuration.hooks)) for (const group of groups) for (const hook of group.hooks) {
    assert.equal(hook.command, 'node');
    assert.deepEqual(hook.args, ['${CLAUDE_PLUGIN_ROOT}/scripts/attest.mjs']);
  }
  const result = execute(process.execPath, [path.join(cc, 'scripts/attest.mjs')], { input: '{bad-json' });
  assert.equal(result.status, 0);
  assert.deepEqual(JSON.parse(result.stdout), {});
  assert.match(result.stderr, /hook-unavailable/);
});

test('filesystem paths and file URLs stay separate from portable paths', async t => {
  const root = await temporary(t);
  assert.equal(fileURLToPath(pathToFileURL(root)), root);
  assert.equal(relativeSource('C:\\source', 'D:\\other\\a.md', path.win32), undefined);
  assert.equal(relativeSource('C:\\source', 'C:\\source-other\\a.md', path.win32), undefined);
  assert.equal(relativeSource('C:\\source', 'C:\\source\\agents\\a.md', path.win32), 'agents/a.md');
  assert.equal(relativeSource('\\\\server\\share\\source', '\\\\server\\share\\source\\a.md', path.win32), 'a.md');
  assert.equal(relativeSource('\\\\server\\share\\source', '\\\\elsewhere\\share\\a.md', path.win32), undefined);
  assert.ok(stateRoot({ LOCALAPPDATA: root }, 'win32').startsWith(root));
  assert.ok(stateRoot({ XDG_STATE_HOME: root }, 'linux').startsWith(root));
});

test('OpenCode source references must match both loaded config and effective agent prompt', async t => {
  const root = await temporary(t), configDirectory = path.join(root, 'config'), directory = path.join(root, 'project');
  await fs.mkdir(configDirectory); await fs.mkdir(directory);
  const prompt = 'Exact prompt\r\n';
  await fs.writeFile(path.join(configDirectory, 'prompt.md'), prompt);
  await fs.writeFile(path.join(configDirectory, 'opencode.jsonc'), '{// fixture\n"agent":{"custom":{"prompt":"{file:./prompt.md}"}},}');
  let effective = prompt.trim();
  const resolver = sourceResolver({ directory, worktree: directory, client: { app: { agents: async () => ({ data: [{ name: 'custom', prompt: effective }] }) } } }, {
    configDirectory, env: {}, execute: async (_exe, args) => args.length === 1 ? root : path.join(configDirectory, 'prompt.md'),
  });
  await resolver.config({ agent: { custom: { prompt: prompt.trim() } } });
  assert.equal((await resolver.source({ agent: 'custom' })).sourceDigest, digest(prompt));
  effective = 'Override';
  assert.deepEqual(await resolver.source({ agent: 'custom' }), {});
  assert.deepEqual(await resolver.source({ agent: 'different' }), {});
  await fs.writeFile(path.join(directory, 'opencode.json'), JSON.stringify({ agent: { custom: { prompt: 'inline' } } }));
  effective = prompt.trim();
  await resolver.config({ agent: { custom: { prompt: prompt.trim() } } });
  assert.deepEqual(await resolver.source({ agent: 'custom' }), {});
  assert.deepEqual(parseJsonc('{"url":"https://example.test/",/* comment */"comma":",}",}'), { url: 'https://example.test/', comma: ',}' });
});

test('OpenCode file expansion trims boundaries but source digests retain every byte', async t => {
  const prompts = ['Definition\n', ' \tDefinition\r\n', '\ufeff\u00a0Definition\nline two\u2003', 'Definition\r\nline two'];
  for (const [index, prompt] of prompts.entries()) await t.test(`expansion ${index}`, async subtest => {
    const root = await temporary(subtest), directory = path.join(root, 'project'), configDirectory = path.join(root, 'config');
    await fs.mkdir(directory); await fs.mkdir(configDirectory);
    const target = path.join(configDirectory, 'prompt.md');
    await fs.writeFile(target, prompt);
    await fs.writeFile(path.join(configDirectory, 'opencode.json'), JSON.stringify({ agent: { custom: { prompt: '{file:./prompt.md}' } } }));
    const expanded = prompt.trim();
    let effective = expanded;
    const resolver = sourceResolver({ directory, worktree: directory, client: { app: { agents: async () => ({ data: [{ name: 'custom', prompt: effective }] }) } } }, {
      configDirectory, env: {}, execute: async (_exe, args) => args.length === 1 ? root : target,
    });
    await resolver.config({ agent: { custom: { prompt: expanded } } });
    const expected = { sourceDefinition: 'config/prompt.md', sourceDigest: digest(prompt) };
    assert.deepEqual(await resolver.source({ agent: 'custom' }), expected);
    if (prompt !== expanded) assert.notEqual(expected.sourceDigest, digest(expanded));
    effective = expanded + '\n';
    assert.deepEqual(await resolver.source({ agent: 'custom' }), {});
    effective = expanded.replace('Definition', 'Different');
    assert.deepEqual(await resolver.source({ agent: 'custom' }), {});
    effective = expanded;
    await resolver.config({ agent: { custom: { prompt: expanded + '\n' } } });
    assert.deepEqual(await resolver.source({ agent: 'custom' }), {});
    await resolver.config({ agent: { custom: { prompt: expanded.replace('Definition', 'Different') } } });
    assert.deepEqual(await resolver.source({ agent: 'custom' }), {});
  });
});

const claudeRecord = (id, model = 'claude-observed', agentId) => ({
  type: 'assistant', sessionId: 'session', uuid: id, agentId, isSidechain: Boolean(agentId), timestamp: '2026-09-20T12:00:00Z',
  message: { id, role: 'assistant', model, content: [{ type: 'text', text: 'PRIVATE RESPONSE' }] },
});

test('Claude extracts runtime responses, not selected models or synthetic messages', () => {
  assert.equal(response(claudeRecord('id', '<synthetic>'), { session: 'session' }), undefined);
  assert.equal(response(claudeRecord('id'), { session: 'other' }), undefined);
  assert.equal(response(claudeRecord('id', 'model', 'child'), { session: 'session' }), undefined);
  assert.deepEqual(response(claudeRecord('id'), { session: 'session' }).participant, { tool: 'claude-code', agent: undefined, model: 'claude-observed' });
});

test('incremental transcripts tolerate partial tails, truncation and duplicate messages', async t => {
  const root = await temporary(t), file = path.join(root, 'transcript.jsonl');
  const ledger = new Ledger('claude-code', 'session', root, { root });
  const context = { session: 'session' };
  const first = JSON.stringify(claudeRecord('id'));
  await fs.writeFile(file, `${first}\n{"type":`);
  await reconcileTranscript(file, context, ledger, root, quiet);
  await fs.writeFile(file, `${first}\n${JSON.stringify(claudeRecord('id2', 'fallback-model'))}\n`);
  await reconcileTranscript(file, context, ledger, root, quiet);
  await reconcileTranscript(file, context, ledger, root, quiet);
  assert.equal((await ledger.records()).length, 2);
  const persisted = await Promise.all((await fs.readdir(ledger.directory)).map(name => fs.readFile(path.join(ledger.directory, name), 'utf8')));
  assert.ok(persisted.every(value => !value.includes('PRIVATE RESPONSE')));
});

test('Claude plugin preserves inputs and permission neutrality and links only completed children', async t => {
  const root = await temporary(t), main = path.join(root, 'main.jsonl'), child = path.join(root, 'child.jsonl');
  await fs.writeFile(main, `${JSON.stringify(claudeRecord('main'))}\n`);
  await fs.writeFile(child, `${JSON.stringify(claudeRecord('child', 'child-model', 'child-id'))}\n`);
  const base = { session_id: 'session', cwd: root, transcript_path: main };
  const options = { root: path.join(root, 'state'), warn: quiet, env: {}, locateWorktree: async () => root };
  await handleHook({ ...base, hook_event_name: 'SessionStart', model: 'MISLEADING-DEFAULT' }, options);
  await handleHook({ ...base, hook_event_name: 'SubagentStart', agent_id: 'child-id', agent_type: 'reviewer' }, options);
  await handleHook({ ...base, hook_event_name: 'SubagentStop', agent_id: 'child-id', agent_type: 'reviewer', agent_transcript_path: child }, options);
  const input = { ...base, hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_input: { command: 'cc-commit -m test', timeout: 3000, description: 'Keep this' } };
  const result = await handleHook(input, options);
  assert.equal(result.hookSpecificOutput.updatedInput.timeout, 3000);
  assert.equal(result.hookSpecificOutput.updatedInput.description, 'Keep this');
  assert.equal(result.hookSpecificOutput.permissionDecision, undefined);
  const payload = JSON.parse(result.hookSpecificOutput.updatedInput.command.match(/^AI_ATTESTATION_JSON='([^']+)'/)[1]);
  assert.equal(payload.participants.length, 2);
  assert.ok(payload.participants.some(item => item.agent === 'reviewer' && item.model === 'child-model'));
  assert.ok(!JSON.stringify(payload).includes('MISLEADING'));
  assert.deepEqual(await handleHook(input, { ...options, env: { AI_ATTESTATION_JSON: '' } }), {});
  assert.deepEqual(await handleHook({ ...input, tool_input: { command: 'echo cc-commit' } }, options), {});
});

const ocInfo = (sessionID = 'root', modelID = 'runtime', id = 'msg1') => ({
  id, sessionID, role: 'assistant', agent: 'build', providerID: 'provider', modelID, tokens: { output: 4, reasoning: 0 }, time: { created: 100 },
});

function fakeClient(messages = [ocInfo()]) {
  return { session: {
    get: async ({ path: { id } }) => ({ data: { id, parentID: id === 'child' ? 'root' : undefined } }),
    messages: async ({ path: { id } }) => ({ data: messages.filter(info => info.sessionID === id).map(info => ({ info, parts: [] })) }),
    children: async ({ path: { id } }) => ({ data: id === 'root' ? [{ id: 'child', parentID: 'root' }, { id: 'unrelated', parentID: 'elsewhere' }] : [] }),
  } };
}

test('OpenCode diagnostics use deduplicated structured logs without terminal fallbacks', async t => {
  t.mock.method(console, 'error', () => assert.fail('Unexpected terminal diagnostic'));
  t.mock.method(console, 'warn', () => assert.fail('Unexpected terminal diagnostic'));
  t.mock.method(console, 'log', () => assert.fail('Unexpected terminal diagnostic'));
  const logs = [];
  const warn = createDiagnostics({ app: { log: async input => { logs.push(input); } } });
  for (let i = 0; i < 3; i++) {
    warn('source-evidence-unavailable');
    warn('participant-limit');
  }
  assert.deepEqual(logs.map(input => input.body), [
    { service: 'agent-attestation', level: 'debug', message: 'partial: source-evidence-unavailable' },
    { service: 'agent-attestation', level: 'warn', message: 'partial: participant-limit' },
  ]);
  assert.ok(logs.every(input => input.signal instanceof AbortSignal));
  for (const client of [undefined, {}, { app: { log: () => { throw new Error('PRIVATE'); } } },
    { app: { log: async () => { throw new Error('PRIVATE'); } } }]) {
    assert.doesNotThrow(() => createDiagnostics(client)('collection-unavailable'));
  }
  await new Promise(resolve => setImmediate(resolve));
});

test('OpenCode collector and source defaults never write diagnostics to the terminal', async t => {
  t.mock.method(console, 'error', () => assert.fail('Unexpected terminal diagnostic'));
  const root = await temporary(t), logs = [];
  const client = { ...fakeClient(), app: { log: async ({ body }) => logs.push(body) } };
  const hooks = createCollector({ client, directory: root, worktree: root }, { root, env: {} });
  const input = { tool: 'bash', sessionID: 'root', callID: 'logging' };
  const command = windows ? 'oc-commit.ps1 -m test' : 'oc-commit -m test';
  for (let i = 0; i < 2; i++) {
    await hooks['tool.execute.before'](input, { args: { command } });
    const output = { env: {} };
    await hooks['shell.env'](input, output);
    assert.deepEqual(JSON.parse(output.env.AI_ATTESTATION_JSON).participants,
      [{ tool: 'opencode', agent: 'build', model: 'provider/runtime' }]);
  }
  await fs.writeFile(path.join(root, 'opencode.json'), '{invalid');
  const sources = sourceResolver({ client, directory: root, worktree: root }, { configDirectory: root, env: {} });
  await sources.config({});
  assert.deepEqual(logs, [
    { service: 'agent-attestation', level: 'debug', message: 'partial: source-evidence-unavailable' },
    { service: 'agent-attestation', level: 'warn', message: 'partial: source-config-unreadable' },
  ]);
  await hooks.dispose();
});

test('OpenCode excludes synthetic and housekeeping responses', () => {
  assert.equal(responseRecord({ ...ocInfo(), tokens: { output: 0, reasoning: 0 } }), undefined);
  assert.equal(responseRecord({ ...ocInfo(), agent: 'title' }), undefined);
  assert.equal(responseRecord({ ...ocInfo(), summary: true }), undefined);
  assert.equal(responseRecord(ocInfo()).model, 'provider/runtime');
});

test('OpenCode per-call environment uses output.args and isolates calls, retries and session trees', async t => {
  const root = await temporary(t);
  const hooks = createCollector({ client: fakeClient([ocInfo(), { ...ocInfo('child', 'child-model', 'msg2'), agent: 'reviewer' }]), directory: root, worktree: root }, { root, warn: quiet, env: {} });
  const input = { tool: 'bash', sessionID: 'root', callID: 'call1' };
  const command = windows ? 'oc-commit.ps1 -m test' : 'oc-commit -m test';
  await hooks['tool.execute.before'](input, { args: { command } });
  const wrong = { env: {} };
  await hooks['shell.env']({ ...input, callID: 'other' }, wrong);
  assert.deepEqual(wrong.env, {});
  const output = { env: { OTHER: 'unchanged' } };
  await hooks['shell.env'](input, output);
  assert.equal(JSON.parse(output.env.AI_ATTESTATION_JSON).participants.length, 2);
  assert.equal(output.env.OTHER, 'unchanged');
  const consumed = { env: {} };
  await hooks['shell.env'](input, consumed);
  assert.deepEqual(consumed.env, {});
  await hooks['tool.execute.before'](input, { args: { command } });
  const explicit = { env: { AI_ATTESTATION_JSON: 'caller' } };
  await hooks['shell.env'](input, explicit);
  assert.equal(explicit.env.AI_ATTESTATION_JSON, 'caller');
});

test('OpenCode history pagination is bounded and detects APIs ignoring before', async t => {
  const root = await temporary(t), warnings = [];
  const client = fakeClient(Array.from({ length: 100 }, (_, i) => ocInfo('root', 'runtime', `msg${String(i).padStart(3, '0')}`)));
  const hooks = createCollector({ client, directory: root }, { root, env: {}, warn: code => warnings.push(code) });
  const input = { tool: 'bash', sessionID: 'root', callID: 'id' };
  await hooks['tool.execute.before'](input, { args: { command: windows ? 'oc-commit.ps1 -m x' : 'oc-commit -m x' } });
  await hooks['shell.env'](input, { env: {} });
  assert.ok(warnings.includes('history-pagination'));
});

test('actual commit wrappers consume invocation-local provenance and preserve failure status', async t => {
  const root = await temporary(t), repo = path.join(root, 'repository'), bin = path.join(root, 'bin');
  await fs.mkdir(repo); await fs.mkdir(bin);
  checked('git', ['init', '-q', repo]);
  const hook = path.join(repo, '.git', 'hooks', 'pre-commit');
  await fs.writeFile(hook, '#!/bin/sh\ntest -z "${AI_ATTESTATION_JSON+x}" || exit 91\n');
  if (!windows) await fs.chmod(hook, 0o700);
  for (const tool of ['cc', 'oc']) {
    const wrapper = path.join(bin, `${tool}-commit${windows ? '.ps1' : ''}`);
    await fs.copyFile(path.join(repository, windows ? `dot_local/executable_${tool}-commit.ps1` : `bin/executable_${tool}-commit`), wrapper);
    if (!windows) await fs.chmod(wrapper, 0o700);
    const record = { tool: tool === 'cc' ? 'claude-code' : 'opencode', agent: 'test-agent', model: 'provider/actual-model', sourceDefinition: 'agents/test.yaml', sourceDigest: digest('definition') };
    const json = handoff([record], record.tool);
    const state = path.join(repo, '.git', `ai-attestation-${record.tool}.json`);
    await fs.writeFile(state, 'stale state');
    const shell = windows ? 'powershell' : 'bash';
    const quote = value => `'${value.replaceAll("'", windows ? "''" : "'\\''")}'`;
    const command = `${windows ? '& ' : ''}${quote(wrapper)} --allow-empty -m ${quote('Quoted "message"\n\nSecond line; $literal')}`;
    const executable = windows ? 'pwsh' : 'bash';
    const args = windows ? ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command'] : ['-c'];
    checked(executable, [...args, inject(command, json, shell)], { cwd: repo });
    await assert.rejects(fs.stat(state), { code: 'ENOENT' });
    const message = checked('git', ['log', '-1', '--format=%B'], { cwd: repo });
    const trailers = checked('git', ['interpret-trailers', '--parse'], { cwd: repo, input: message });
    assert.ok(trailers.includes(`AI-Participant: tool=${record.tool}; agent=test-agent; model=provider/actual-model`));
    assert.ok(trailers.includes('Source-Definition: agents/test.yaml'));
    assert.ok(trailers.includes(`Source-Digest: ${record.sourceDigest}`));
    assert.ok(message.includes('Second line; $literal'));
    const failed = execute(executable, [...args, inject(`${windows ? '& ' : ''}${quote(wrapper)} -m empty`, json, shell)], { cwd: repo });
    assert.notEqual(failed.status, 0, failed.stdout);
  }
});

test('PowerShell restores existing environment and preserves terminating failure', { skip: !windows }, async t => {
  const root = await temporary(t), file = path.join(root, 'cc-commit.ps1');
  await fs.writeFile(file, "[Console]::WriteLine($env:AI_ATTESTATION_JSON); exit 7\r\n");
  const command = `& '${file.replaceAll("'", "''")}'`;
  const generated = inject(command, '{"schemaVersion":1}', 'powershell');
  const result = execute('pwsh', ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', generated], { env: { ...gitEnv, AI_ATTESTATION_JSON: 'previous' } });
  assert.equal(result.status, 7, result.stderr);
  assert.equal(result.stdout.trim(), '{"schemaVersion":1}');
  const inspect = generated.replace(/; exit \$LASTEXITCODE$/, '; [Console]::WriteLine($env:AI_ATTESTATION_JSON); [Console]::WriteLine($LASTEXITCODE)');
  const restored = checked('pwsh', ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', inspect], { env: { ...gitEnv, AI_ATTESTATION_JSON: 'previous' } });
  assert.match(restored, /previous\r?\n7/);
  await fs.writeFile(file, "throw 'fixture failure'\r\n");
  assert.notEqual(execute('pwsh', ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', generated]).status, 0);
  await fs.writeFile(file, "Write-Error 'fixture nonterminating failure'\r\n");
  const original = execute('pwsh', ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command]);
  assert.equal(execute('pwsh', ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', generated]).status, original.status);
});

test('native Git Bash bridge carries hook participants through the real PowerShell wrapper', { skip: !windows }, async t => {
  const root = await temporary(t), repo = path.join(root, 'repository');
  await fs.mkdir(repo);
  checked('git', ['init', '-q', repo]);
  const wrapper = path.join(repo, 'cc-commit.ps1');
  await fs.copyFile(path.join(repository, 'dot_local/executable_cc-commit.ps1'), wrapper);
  await fs.writeFile(path.join(repo, '.git/hooks/pre-commit'), '#!/bin/sh\ntest -z "${AI_ATTESTATION_JSON+x}" || exit 91\n');
  const bash = path.resolve(checked('git', ['--exec-path']).trim(), '../../..', 'bin/bash.exe');
  const main = path.join(root, 'main.jsonl'), child = path.join(root, 'child.jsonl');
  await fs.writeFile(main, `${JSON.stringify(claudeRecord('main'))}\n`);
  await fs.writeFile(child, `${JSON.stringify(claudeRecord('child', 'child-model', 'child-id'))}\n`);
  const base = { session_id: 'session', cwd: repo, transcript_path: main };
  const options = { root: path.join(root, 'state'), warn: quiet, env: {}, locateWorktree: async () => repo };
  await handleHook({ ...base, hook_event_name: 'SubagentStop', agent_id: 'child-id', agent_type: 'general-purpose', agent_transcript_path: child }, options);
  const command = `cd "${repo.replaceAll('\\', '/')}" && pwsh -NoProfile -Command "& '${wrapper}' --allow-empty -m 'Quoted \\"message\\"'"`;
  const input = { ...base, hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_input: { command, description: 'Keep this', timeout: 20000 } };
  const result = await handleHook(input, options);
  assert.equal(result.hookSpecificOutput.permissionDecision, undefined);
  assert.equal(result.hookSpecificOutput.updatedInput.timeout, 20000);
  const rewritten = result.hookSpecificOutput.updatedInput.command;
  assert.match(rewritten, /&& AI_ATTESTATION_JSON=/);
  checked(bash, ['--noprofile', '--norc', '-c', rewritten], { cwd: repo });
  const message = checked('git', ['log', '-1', '--format=%B'], { cwd: repo });
  assert.ok(message.includes('Quoted "message"'));
  assert.ok(message.includes('AI-Participant: tool=claude-code; model=claude-observed'));
  assert.ok(message.includes('AI-Participant: tool=claude-code; agent=general-purpose; model=child-model'));
  const empty = rewritten.replace(' --allow-empty', '');
  assert.equal(execute(bash, ['--noprofile', '--norc', '-c', empty], { cwd: repo }).status, 1);
  const missing = rewritten.replace(`cd "${repo.replaceAll('\\', '/')}"`, `cd "${repo.replaceAll('\\', '/')}/missing"`);
  const before = checked('git', ['rev-parse', 'HEAD'], { cwd: repo });
  assert.notEqual(execute(bash, ['--noprofile', '--norc', '-c', missing], { cwd: repo }).status, 0);
  assert.equal(checked('git', ['rev-parse', 'HEAD'], { cwd: repo }), before);
  checked(bash, ['--noprofile', '--norc', '-c', `${rewritten}; test -z "\${AI_ATTESTATION_JSON+x}"`], { cwd: repo });
  const pwshHome = checked('pwsh', ['-NoProfile', '-Command', '[Console]::Write($PSHOME)']);
  const absolute = command.replace('pwsh -NoProfile', `'${path.join(pwshHome, 'pwsh.exe')}' -NoProfile`);
  const match = recognize(absolute, 'cc-commit');
  assert.ok(match);
  checked(bash, ['--noprofile', '--norc', '-c', inject(absolute, handoff([{ tool: 'claude-code', model: 'actual' }], 'claude-code'), 'bash', match)], { cwd: repo });
  assert.deepEqual(await handleHook(input, { ...options, env: { AI_ATTESTATION_JSON: 'caller' } }), {});
});
