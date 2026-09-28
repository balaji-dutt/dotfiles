import fs from 'node:fs/promises';
import path from 'node:path';
import { key, participant } from './shared/records.mjs';
import { atomicJson, readJson, stateRoot } from './shared/storage.mjs';
import { run } from './shared/process.mjs';

const receiptId = /^[a-f0-9]{64}$/;
const issueId = /^[a-z][a-z0-9-]*-[a-z0-9.]+$/;

export function approvalStore({ client, directory, worktree }, {
  root = stateRoot(), env = process.env, execute = run, warn = () => {},
} = {}) {
  const folder = path.join(root, 'opencode', 'approvals');

  async function branch() {
    const owner = await fs.realpath(path.resolve(worktree || directory));
    const top = (await execute('git', ['rev-parse', '--show-toplevel'], directory)).trim();
    if (await fs.realpath(path.resolve(top)) !== owner) throw new Error('approval-worktree');
    const name = (await execute('git', ['symbolic-ref', '--quiet', '--short', 'HEAD'], directory)).trim();
    if (!name || name.length > 256) throw new Error('approval-branch');
    return { owner, name };
  }

  async function approved(input, output) {
    if (input.tool !== 'submit_plan' || !input.sessionID || !input.callID) return;
    if (typeof output.output !== 'string' || !/^Plan approved(?: with notes)?!(?:\s|$)/.test(output.output)) return;
    try {
      const { data } = await client.session.messages({
        path: { id: input.sessionID }, query: { directory, limit: 100 }, signal: AbortSignal.timeout(2000),
      });
      if (!Array.isArray(data)) return;
      const matching = data.filter(item => item.info?.role === 'assistant' && item.info.sessionID === input.sessionID &&
        item.parts?.some(part => part.type === 'tool' && part.tool === 'submit_plan' && part.callID === input.callID));
      if (matching.length !== 1) return;
      const info = matching[0].info;
      if (!['plan', 'plan-GPT-xhigh'].includes(info.agent) || !info.providerID || !info.modelID) return;
      const record = participant({ tool: 'opencode', agent: info.agent, role: 'planner', model: `${info.providerID}/${info.modelID}` });
      if (!record?.model) return;
      const { owner, name } = await branch();
      const id = key(JSON.stringify([input.sessionID, input.callID, owner, name]));
      const entries = await fs.readdir(folder).catch(() => []);
      if (entries.length >= 256) { warn('approval-limit'); return; }
      await atomicJson(path.join(folder, `${id}.json`), {
        version: 1, id, sessionID: input.sessionID, callID: input.callID,
        worktree: owner, branch: name, record,
      }, { exclusive: true });
    } catch { warn('approval-unavailable'); }
  }

  async function selected(command) {
    const id = env.AI_ATTESTATION_PLAN_RECEIPT;
    const issue = env.AI_ATTESTATION_PLAN_ISSUE;
    if (!receiptId.test(id ?? '') || !issueId.test(issue ?? '')) return;
    if (typeof command !== 'string' ||
      !new RegExp(`Refs: ${issue.replaceAll('.', '\\.')}(?=[^a-zA-Z0-9.-]|$)`).test(command)) return;
    try {
      const saved = await readJson(path.join(folder, `${id}.json`), 2048);
      const { owner, name } = await branch();
      if (saved.version !== 1 || saved.id !== id || saved.worktree !== owner || saved.branch !== name ||
        !saved.sessionID || !saved.callID ||
        key(JSON.stringify([saved.sessionID, saved.callID, owner, saved.branch])) !== id) return;
      const record = participant(saved.record);
      if (record?.tool === 'opencode' && ['plan', 'plan-GPT-xhigh'].includes(record.agent) &&
        record.role === 'planner' && record.model && !record.sourceDefinition) return record;
    } catch { warn('approval-unavailable'); }
  }

  return { approved, selected };
}
