import { handleHook } from '../lib/collector.mjs';

try {
  let input = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) {
    input += chunk;
    if (Buffer.byteLength(input) > 1024 * 1024) throw new Error('input-size');
  }
  const output = await handleHook(JSON.parse(input));
  process.stdout.write(JSON.stringify(output));
} catch {
  console.error('[agent-attestation] partial: hook-unavailable');
  process.stdout.write('{}');
}
