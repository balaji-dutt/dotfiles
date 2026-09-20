import { handoff } from './shared/records.mjs';
import { Ledger } from './shared/storage.mjs';
import { recognize } from './shared/commands.mjs';
import { createDiagnostics } from './diagnostics.mjs';

const housekeeping = new Set(['title', 'summary', 'compaction']);
const callKey = input => JSON.stringify([input.sessionID, input.callID]);

export function responseRecord(info) {
  const agent = info.agent ?? info.mode;
  if (info.role !== 'assistant' || info.summary || housekeeping.has(agent)) return undefined;
  if (!(info.tokens?.output > 0 || info.tokens?.reasoning > 0)) return undefined;
  return {
    tool: 'opencode', agent,
    model: info.providerID && info.modelID ? `${info.providerID}/${info.modelID}` : undefined,
  };
}

export function createCollector({ client, directory, worktree }, {
  root, warn = createDiagnostics(client),
  source = async () => ({}), env = process.env,
} = {}) {
  const pending = new Map();
  const snapshots = new Map();
  const safe = fn => async (...args) => {
    try { return await fn(...args); } catch { warn('collection-unavailable'); }
  };
  const request = (method, id, query = {}) => method({
    path: { id }, query: { directory, ...query }, signal: AbortSignal.timeout(2000),
  });

  async function rootSession(id) {
    const seen = new Set();
    for (let depth = 0; depth < 16; depth++) {
      if (seen.has(id)) throw new Error('session-cycle');
      seen.add(id);
      const { data } = await request(client.session.get.bind(client.session), id);
      if (data?.id !== id) throw new Error('session-unavailable');
      if (!data.parentID) return id;
      id = data.parentID;
    }
    throw new Error('session-depth');
  }

  async function observe(info, ledger, historical = false) {
    const record = responseRecord(info);
    if (!record || !info.id || !info.sessionID) return;
    const identity = JSON.stringify([info.sessionID, info.id]);
    let snapshot = snapshots.get(identity);
    if (!snapshot) {
      snapshot = { ...record, ...(historical ? {} : await source(info)) };
      if (snapshots.size >= 2048) snapshots.delete(snapshots.keys().next().value);
      snapshots.set(identity, snapshot);
    }
    await ledger.record(identity, snapshot, Number.isFinite(info.time?.created) ? info.time.created : Date.now());
  }

  async function reconcile(id, ledger) {
    const queue = [id], seen = new Set();
    const deadline = Date.now() + 4000;
    let budget = 20;
    while (queue.length && seen.size < 32 && budget > 0) {
      if (Date.now() >= deadline) { warn('collection-time-limit'); break; }
      const session = queue.shift();
      if (seen.has(session)) continue;
      seen.add(session);
      let before;
      for (;;) {
        if (Date.now() >= deadline) { warn('collection-time-limit'); break; }
        if (--budget < 0) { warn('history-limit'); break; }
        const { data } = await request(client.session.messages.bind(client.session), session, { limit: 100, before });
        if (!Array.isArray(data)) throw new Error('history-unavailable');
        for (const item of data) if (item.info?.sessionID === session) await observe(item.info, ledger, true);
        if (data.length < 100) break;
        const next = data.map(item => item.info?.id).filter(Boolean).sort()[0];
        if (!next || next === before) { warn('history-pagination'); break; }
        before = next;
      }
      const { data: children } = await request(client.session.children.bind(client.session), session);
      if (!Array.isArray(children)) { warn('children-unavailable'); continue; }
      for (const child of children) if (child.parentID === session) queue.push(child.id);
    }
    if (queue.length) warn('session-tree-limit');
  }

  return {
    event: safe(async ({ event }) => {
      if (event.type !== 'message.updated') return;
      const info = event.properties.info;
      if (!responseRecord(info)) return;
      const session = await rootSession(info.sessionID);
      await observe(info, new Ledger('opencode', session, worktree || directory, { root, warn }));
    }),
    'tool.execute.before': safe(async (input, output) => {
      if (!['bash', 'shell', 'powershell'].includes(input.tool) || !input.callID || !input.sessionID) return;
      const command = output.args?.command;
      const shell = input.tool === 'powershell' || process.platform === 'win32' ? 'powershell' : 'bash';
      if (!recognize(command, 'oc-commit', shell)) {
        if (typeof command === 'string' && /\boc-commit\b/.test(command)) warn('unsupported-or-explicit-command');
        return;
      }
      if (pending.size >= 128) pending.delete(pending.keys().next().value);
      pending.set(callKey(input), Date.now());
    }),
    'shell.env': safe(async (input, output) => {
      const started = pending.get(callKey(input));
      pending.delete(callKey(input));
      if (!started || Date.now() - started > 600000) return;
      if (Object.hasOwn(env, 'AI_ATTESTATION_JSON') || Object.hasOwn(output.env, 'AI_ATTESTATION_JSON')) return;
      const session = await rootSession(input.sessionID);
      const ledger = new Ledger('opencode', session, worktree || directory, { root, warn });
      try { await reconcile(session, ledger); } catch { warn('history-unavailable'); }
      const records = await ledger.records();
      if (records.some(record => !record.sourceDefinition)) warn('source-evidence-unavailable');
      output.env.AI_ATTESTATION_JSON = handoff(records, 'opencode', warn);
    }),
    'tool.execute.after': async input => { pending.delete(callKey(input)); },
    dispose: async () => { pending.clear(); snapshots.clear(); },
  };
}
