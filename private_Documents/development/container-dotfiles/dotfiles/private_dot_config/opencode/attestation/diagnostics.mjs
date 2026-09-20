export function createDiagnostics(client) {
  const seen = new Set();
  return code => {
    if (seen.has(code)) return;
    seen.add(code);
    try {
      const result = client?.app?.log?.({
        body: {
          service: 'agent-attestation',
          level: code === 'source-evidence-unavailable' ? 'debug' : 'warn',
          message: `partial: ${code}`,
        },
        signal: AbortSignal.timeout(2000),
      });
      Promise.resolve(result).catch(() => {});
    } catch {}
  };
}
