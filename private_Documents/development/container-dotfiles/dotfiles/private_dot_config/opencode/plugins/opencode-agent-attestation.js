import { createCollector } from '../attestation/collector.mjs';
import { sourceResolver } from '../attestation/sources.mjs';
import { createDiagnostics } from '../attestation/diagnostics.mjs';

export default async function AgentAttestation(context) {
  const warn = createDiagnostics(context.client);
  const sources = sourceResolver(context, { warn });
  return {
    ...createCollector(context, { source: sources.source, warn }),
    config: async config => {
      try { await sources.config(config); }
      catch { warn('source-config-unavailable'); }
    },
  };
}
