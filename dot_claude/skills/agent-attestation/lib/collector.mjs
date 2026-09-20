import fs from 'node:fs/promises';
import path from 'node:path';
import { handoff, key } from './shared/records.mjs';
import { atomicJson, Ledger, readJson, stateRoot } from './shared/storage.mjs';
import { recognize, inject, shellFor } from './shared/commands.mjs';
import { worktree } from './shared/process.mjs';
import { reconcileTranscript } from './transcript.mjs';

export async function handleHook(input, {
  root = stateRoot(), warn = code => console.error(`[agent-attestation] partial: ${code}`),
  env = process.env, locateWorktree = worktree,
} = {}) {
  if (!input.session_id || !input.cwd) return {};
  const deadline = Date.now() + 4000;
  const metadata = path.join(root, 'claude-code', 'sessions', key(input.session_id));
  const contextFile = path.join(metadata, 'context.json');
  let context = await readJson(contextFile).catch(() => undefined);
  if (!context) {
    const sessions = await fs.readdir(path.dirname(metadata)).catch(() => []);
    if (sessions.length >= 256) { warn('session-storage-limit'); return {}; }
    context = { worktree: await locateWorktree(input.cwd), agent: input.agent_id ? undefined : input.agent_type };
    await atomicJson(contextFile, context, { exclusive: true });
    context = await readJson(contextFile);
  }
  const ledger = new Ledger('claude-code', input.session_id, context.worktree, { root, warn });
  const event = input.hook_event_name;
  const entries = await fs.readdir(metadata);
  if (entries.length >= 256) { warn('session-metadata-limit'); return {}; }
  if (input.agent_id && input.agent_type) {
    await atomicJson(path.join(metadata, `agent-${key(input.agent_id)}.json`), { agent: input.agent_type });
  }
  const agent = input.agent_type || (input.agent_id
    ? (await readJson(path.join(metadata, `agent-${key(input.agent_id)}.json`)).catch(() => ({}))).agent
    : undefined);
  if (event === 'SubagentStop' && input.agent_id && input.agent_transcript_path) {
    await atomicJson(path.join(metadata, `child-${key(input.agent_id)}.json`), {
      agentID: input.agent_id, agent, transcript: input.agent_transcript_path,
    });
  }

  async function reconcile(file, agentID, agent) {
    if (typeof file !== 'string' || !path.isAbsolute(file)) return;
    for (let attempt = 0; attempt < 3; attempt++) {
      if (Date.now() >= deadline) { warn('collection-time-limit'); return; }
      try {
        const found = await reconcileTranscript(file, { session: input.session_id, agentID, agent }, ledger, metadata, warn);
        if (found || attempt === 2) return;
      } catch { if (attempt === 2) { warn('transcript-unavailable'); return; } }
      await new Promise(resolve => setTimeout(resolve, 25 * (attempt + 1)));
    }
  }

  await reconcile(input.transcript_path, undefined, context.agent);
  if (input.agent_id) await reconcile(input.transcript_path, input.agent_id, agent);
  if (input.agent_id && input.agent_transcript_path) {
    await reconcile(input.agent_transcript_path, input.agent_id, agent);
  }
  if (event !== 'PreToolUse') return {};
  const shell = shellFor(input.tool_name);
  if (!shell) return {};
  const command = input.tool_input?.command;
  const invocation = recognize(command, 'cc-commit', shell);
  if (!invocation) {
    if (typeof command === 'string' && /\bcc-commit\b/.test(command)) warn('unsupported-or-explicit-command');
    return {};
  }
  if (Object.hasOwn(env, 'AI_ATTESTATION_JSON')) return {};
  const children = (await fs.readdir(metadata)).filter(name => /^child-[a-f0-9]{64}\.json$/.test(name));
  if (children.length > 32) warn('session-tree-limit');
  for (const child of children.slice(0, 32)) {
    try {
      const data = await readJson(path.join(metadata, child));
      await reconcile(data.transcript, data.agentID, data.agent);
    } catch { warn('child-unavailable'); }
  }
  const json = handoff(await ledger.records(), 'claude-code', warn);
  warn('source-evidence-unavailable');
  return {
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      updatedInput: { ...input.tool_input, command: inject(command, json, shell, invocation) },
    },
  };
}
