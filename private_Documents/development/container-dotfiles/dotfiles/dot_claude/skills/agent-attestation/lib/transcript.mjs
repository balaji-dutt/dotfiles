import fs from 'node:fs/promises';
import path from 'node:path';
import { key } from './shared/records.mjs';
import { atomicJson, readJson } from './shared/storage.mjs';

export function response(record, context) {
  if (record.type !== 'assistant' || record.isMeta || record.isCompactSummary) return undefined;
  if (record.sessionId && record.sessionId !== context.session) return undefined;
  if (context.agentID ? record.agentId !== context.agentID : record.isSidechain || record.agentId) return undefined;
  const message = record.message;
  if (message?.role !== 'assistant' || typeof message.model !== 'string' || message.model.startsWith('<')) return undefined;
  if (!Array.isArray(message.content) || !message.content.some(part =>
    (part.type === 'text' && part.text?.trim()) || part.type === 'tool_use')) return undefined;
  if (!message.id && !record.uuid) return undefined;
  return {
    evidence: `${context.agentID || 'root'}:${message.id || record.uuid}:${message.model}`,
    participant: { tool: 'claude-code', agent: context.agent, model: message.model },
    time: Date.parse(record.timestamp),
  };
}

export async function reconcileTranscript(file, context, ledger, metadata, warn) {
  const checkpoint = path.join(metadata, `cursor-${key(JSON.stringify([file, context.agentID]))}.json`);
  let previous = await readJson(checkpoint).catch(() => ({}));
  const handle = await fs.open(file, 'r');
  try {
    const stat = await handle.stat();
    const identity = `${stat.dev}:${stat.ino}:${stat.birthtimeMs}`;
    let position = previous.identity === identity && previous.offset <= stat.size ? previous.offset : 0;
    let budget = 4 * 1024 * 1024;
    let found = 0;
    while (position < stat.size && budget > 0) {
      const buffer = Buffer.alloc(Math.min(budget, stat.size - position));
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, position);
      budget -= bytesRead;
      const lastNewline = buffer.subarray(0, bytesRead).lastIndexOf(10);
      if (lastNewline < 0) { warn('transcript-tail-or-limit'); break; }
      for (const line of buffer.subarray(0, lastNewline).toString('utf8').split('\n')) {
        try {
          const item = response(JSON.parse(line), context);
          if (!item) continue;
          await ledger.record(item.evidence, item.participant, Number.isFinite(item.time) ? item.time : Date.now());
          found++;
        } catch { warn('transcript-record'); }
      }
      position += lastNewline + 1;
      await atomicJson(checkpoint, { identity, offset: position });
    }
    if (position < stat.size) warn('transcript-partial');
    return found;
  } finally { await handle.close(); }
}
