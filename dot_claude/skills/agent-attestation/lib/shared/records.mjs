import { createHash } from 'node:crypto';

export const digest = value => `sha256:${createHash('sha256').update(value).digest('hex')}`;
export const key = value => digest(value).slice(7);
const identifier = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$/;
const role = /^[a-z0-9][a-z0-9-]{0,63}$/;

export function portable(value) {
  return typeof value === 'string' && value.length <= 512 &&
    /^[A-Za-z0-9][A-Za-z0-9._@+-]*(?:\/[A-Za-z0-9][A-Za-z0-9._@+-]*)*$/.test(value);
}

export function participant(input) {
  if (!input || typeof input.tool !== 'string' || !identifier.test(input.tool)) return undefined;
  const result = { tool: input.tool };
  for (const name of ['agent', 'role', 'model']) {
    if (typeof input[name] === 'string' && (name === 'role' ? role : identifier).test(input[name])) {
      result[name] = input[name];
    }
  }
  if (portable(input.sourceDefinition) && /^sha256:[a-f0-9]{64}$/.test(input.sourceDigest ?? '')) {
    result.sourceDefinition = input.sourceDefinition;
    result.sourceDigest = input.sourceDigest;
  }
  return result;
}

export function handoff(records, tool, warn = () => {}) {
  const unique = new Map();
  for (const item of records) {
    const record = participant(item);
    if (record?.tool === tool) unique.set(JSON.stringify(record), record);
  }
  if (unique.size > 8) warn('participant-limit');
  const participants = [...unique.values()].slice(0, 8);
  if (!participants.length) {
    warn('no-response-evidence');
    participants.push({ tool });
  }
  const result = JSON.stringify({ schemaVersion: 1, participants });
  if (Buffer.byteLength(result) > 16384) throw new Error('handoff-size');
  return result;
}
