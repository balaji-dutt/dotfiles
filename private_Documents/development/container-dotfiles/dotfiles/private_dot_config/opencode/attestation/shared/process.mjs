import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

export async function run(executable, args, cwd) {
  const env = { ...process.env };
  delete env.AI_ATTESTATION_JSON;
  for (const name of Object.keys(env)) if (name.startsWith('GIT_')) delete env[name];
  const result = await promisify(execFile)(executable, args, {
    cwd, env, shell: false, windowsHide: true, timeout: 2000, maxBuffer: 1024 * 1024,
  });
  return result.stdout.trim();
}

export async function worktree(cwd) {
  return run('git', ['rev-parse', '--show-toplevel'], cwd).catch(() => cwd);
}
