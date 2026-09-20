import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { run } from './shared/process.mjs';
import { digest } from './shared/records.mjs';
import { sourceSnapshot } from './shared/source.mjs';
import { createDiagnostics } from './diagnostics.mjs';

export function parseJsonc(text) {
  let output = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      output += char;
      if (char === '\\') output += text[++i] ?? '';
      else if (char === '"') quoted = false;
    } else if (char === '"') { quoted = true; output += char; }
    else if (char === '/' && text[i + 1] === '/') {
      while (i < text.length && text[i] !== '\n') i++;
      output += '\n';
    } else if (char === '/' && text[i + 1] === '*') {
      i += 2;
      while (i < text.length && !(text[i] === '*' && text[i + 1] === '/')) i++;
      i++;
      output += ' ';
    } else output += char;
  }
  let clean = ''; quoted = false;
  for (let i = 0; i < output.length; i++) {
    const char = output[i];
    if (quoted) {
      clean += char;
      if (char === '\\') clean += output[++i] ?? '';
      else if (char === '"') quoted = false;
    } else if (char === '"') { quoted = true; clean += char; }
    else if (char === ',') {
      let next = i + 1;
      while (next < output.length && /\s/.test(output[next])) next++;
      if (output[next] !== '}' && output[next] !== ']') clean += char;
    } else clean += char;
  }
  return JSON.parse(clean);
}

export function sourceResolver({ client, directory, worktree }, {
  configDirectory = fileURLToPath(new URL('../', import.meta.url)), env = process.env, execute = run,
  warn = createDiagnostics(client),
} = {}) {
  const snapshots = new Map();
  return {
    async config(config) {
      snapshots.clear();
      if (env.OPENCODE_CONFIG_CONTENT) return;
      const files = new Set();
      for (const base of [configDirectory, env.OPENCODE_CONFIG_DIR].filter(Boolean)) {
        for (const name of ['opencode.json', 'opencode.jsonc']) files.add(path.join(base, name));
      }
      if (env.OPENCODE_CONFIG) files.add(path.resolve(env.OPENCODE_CONFIG));
      let current = path.resolve(directory);
      for (let depth = 0; depth < 16; depth++) {
        for (const name of ['opencode.json', 'opencode.jsonc', '.opencode/opencode.json', '.opencode/opencode.jsonc']) files.add(path.join(current, name));
        if (current === worktree || path.dirname(current) === current) break;
        current = path.dirname(current);
      }
      const references = new Map();
      for (const file of files) {
        try {
          if ((await fs.stat(file)).size > 1024 * 1024) { warn('source-config-limit'); return; }
          const raw = parseJsonc(await fs.readFile(file, 'utf8'));
          for (const [name, agent] of Object.entries(raw.agent ?? {})) {
            if (!Object.hasOwn(agent, 'prompt')) continue;
            const match = typeof agent.prompt === 'string' && /^\{file:([^{}]+)\}$/.exec(agent.prompt);
            const values = references.get(name) ?? [];
            values.push(match ? path.resolve(path.dirname(file), match[1]) : undefined);
            references.set(name, values);
          }
        } catch (error) { if (error.code !== 'ENOENT') { warn('source-config-unreadable'); return; } }
      }
      let sourceRoot;
      for (const [name, paths] of references) {
        if (paths.length !== 1 || !paths[0] || typeof config.agent?.[name]?.prompt !== 'string') continue;
        try {
          const target = paths[0];
          if ((await fs.stat(target)).size > 1024 * 1024) continue;
          const bytes = await fs.readFile(target);
          if (bytes.toString('utf8') !== config.agent[name].prompt) continue;
          sourceRoot ??= await execute('chezmoi', ['source-path'], directory);
          const sourceFile = await execute('chezmoi', ['source-path', target], directory);
          const snapshot = await sourceSnapshot({ sourceRoot, sourceFile, loadedBytes: bytes });
          snapshots.set(name, { snapshot, promptDigest: digest(bytes) });
        } catch {}
      }
    },
    async source(info) {
      const name = info.agent ?? info.mode;
      const entry = snapshots.get(name);
      if (!entry) return {};
      try {
        const { data } = await client.app.agents({ query: { directory }, signal: AbortSignal.timeout(2000) });
        const agents = data?.filter(agent => agent.name === name) ?? [];
        if (agents.length === 1 && typeof agents[0].prompt === 'string' && digest(agents[0].prompt) === entry.promptDigest) return entry.snapshot;
      } catch {}
      return {};
    },
  };
}
