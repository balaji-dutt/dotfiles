import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { key, participant } from './records.mjs';

export function stateRoot(env = process.env, platform = process.platform, home = os.homedir()) {
  const base = platform === 'win32'
    ? (env.LOCALAPPDATA || path.join(home, 'AppData', 'Local'))
    : (env.XDG_STATE_HOME || path.join(home, '.local', 'state'));
  return path.join(base, 'agent-attestation');
}

export async function readJson(file, max = 65536) {
  const handle = await fs.open(file, 'r');
  try {
    if ((await handle.stat()).size > max) throw new Error('record-size');
    return JSON.parse(await handle.readFile('utf8'));
  } finally { await handle.close(); }
}

export async function atomicJson(file, data, { rename = fs.rename, exclusive = false, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)) } = {}) {
  await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${randomUUID()}.tmp`;
  try {
    await fs.writeFile(temporary, JSON.stringify(data), { flag: 'wx', mode: 0o600 });
    for (let attempt = 0; ; attempt++) {
      try {
        if (exclusive) await fs.link(temporary, file);
        else await rename(temporary, file);
        break;
      }
      catch (error) {
        if (exclusive && error.code === 'EEXIST') break;
        if (attempt === 3 || !['EPERM', 'EACCES', 'EBUSY'].includes(error.code)) throw error;
        await sleep(10 * 2 ** attempt);
      }
    }
  } finally { await fs.rm(temporary, { force: true }).catch(() => {}); }
}

export class Ledger {
  constructor(tool, session, worktree, { root = stateRoot(), warn = () => {} } = {}) {
    if (!session || !worktree) throw new Error('missing-session-context');
    this.directory = path.join(root, tool, key(JSON.stringify([session, worktree])));
    this.warn = warn;
  }

  async record(evidence, value, time = Date.now()) {
    const record = participant(value);
    if (!record) return;
    if (!(await fs.stat(this.directory).catch(() => undefined))) {
      const sessions = await fs.readdir(path.dirname(this.directory)).catch(() => []);
      if (sessions.filter(name => /^[a-f0-9]{64}$/.test(name)).length >= 256) {
        this.warn('session-storage-limit');
        return;
      }
    }
    const entries = await fs.readdir(this.directory).catch(() => []);
    if (entries.length >= 2048) { this.warn('ledger-limit'); return; }
    const file = path.join(this.directory, `${key(evidence)}.json`);
    try {
      await atomicJson(file, { record, time, evidence: key(evidence) }, { exclusive: true });
    } catch (error) { if (error.code !== 'EEXIST') throw error; }
  }

  async records() {
    const entries = (await fs.readdir(this.directory).catch(() => [])).filter(name => /^[a-f0-9]{64}\.json$/.test(name));
    if (entries.length > 2048) this.warn('ledger-limit');
    const records = [];
    for (const file of entries.slice(0, 2048)) {
      try {
        const entry = await readJson(path.join(this.directory, file));
        if (Number.isFinite(entry.time) && participant(entry.record)) records.push(entry);
      } catch { this.warn('unreadable-record'); }
    }
    return records.sort((a, b) => a.time - b.time || a.evidence.localeCompare(b.evidence)).map(entry => entry.record);
  }
}
