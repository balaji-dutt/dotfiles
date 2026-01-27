import { appendFile } from "node:fs/promises";
import { appendFileSync } from "node:fs";

export default async () => {
  // Off by default. Enable with: OPENCODE_EVENT_TAP=1
  const enabled = /^(1|true|on)$/i.test(process.env.OPENCODE_EVENT_TAP || "");
  const outFile = process.env.OPENCODE_EVENT_TAP_FILE || "/tmp/opencode-event-tap.log";

  // Optional: dump one full sanitized payload for a specific event type:
  // OPENCODE_EVENT_TAP_DUMP=message.updated
  const forcedDumpType = (process.env.OPENCODE_EVENT_TAP_DUMP || "").trim();
  let dumped = false;

  const logAsync = (obj) => {
    if (!enabled) return;
    const line = `${new Date().toISOString()} ${JSON.stringify(obj)}\n`;
    // Fire-and-forget; never await inside the event loop.
    void appendFile(outFile, line).catch(() => {});
  };

  // Sync write only for one-time FULL_DUMP, so it survives fast shutdown.
  const logSync = (obj) => {
    if (!enabled) return;
    try {
      const line = `${new Date().toISOString()} ${JSON.stringify(obj)}\n`;
      appendFileSync(outFile, line);
    } catch {
      // ignore
    }
  };

  // Best-effort redaction
  const redact = (value) => {
    if (typeof value !== "string") return value;
    if (
      /sk-|api[_-]?key|token|bearer|anthropic|openai/i.test(value) &&
      value.length > 12
    ) {
      return "[REDACTED]";
    }
    return value;
  };

  const sanitize = (obj, depth = 0) => {
    if (depth > 6) return "[TRUNCATED_DEPTH]";
    if (obj === null || obj === undefined) return obj;

    if (Array.isArray(obj)) {
      if (obj.length > 50) {
        return obj
          .slice(0, 50)
          .map((x) => sanitize(x, depth + 1))
          .concat(["[TRUNCATED_ARRAY]"]);
      }
      return obj.map((x) => sanitize(x, depth + 1));
    }

    if (typeof obj === "object") {
      const out = {};
      const keys = Object.keys(obj);
      for (const k of keys.slice(0, 80)) {
        const v = obj[k];
        if (typeof v === "string") out[k] = redact(v);
        else out[k] = sanitize(v, depth + 1);
      }
      if (keys.length > 80) out.__truncated_keys = keys.length - 80;
      return out;
    }

    return redact(obj);
  };

  setTimeout(() => {
    logAsync({ kind: "loaded", enabled, outFile, forcedDumpType });
  }, 0);

  // Keep this list small — logging everything can get noisy fast.
  const interesting = new Set([
    "file.edited",
    "session.idle",
    "session.created",
    "message.updated",
    "message.part.updated",
    "tool.execute.before",
    "tool.execute.after",
  ]);

  // Throttle small entries
  let lastMs = 0;

  function pickFirst(...vals) {
    for (const v of vals) {
      if (v !== undefined && v !== null && v !== "") return v;
    }
    return null;
  }

  function extractCommandish(evt) {
    return pickFirst(
      evt?.properties?.command,
      evt?.properties?.text,
      evt?.properties?.args,
      evt?.input?.command,
      evt?.input?.text,
      evt?.input?.args,
      evt?.command,
      evt?.text,
      evt?.args
    );
  }

  return {
    // Rename handler param to avoid DOM global `event` lint warnings.
    event: async ({ event: evt }) => {
      if (!enabled || !evt?.type || !interesting.has(evt.type)) return;

      // One-time full dump if requested
      if (!dumped && forcedDumpType && evt.type === forcedDumpType) {
        dumped = true;
        logSync({
          kind: "FULL_DUMP",
          note: "One-time sanitized dump by forced type",
          eventType: evt.type,
          event: sanitize(evt),
        });
      }

      const now = Date.now();
      if (now - lastMs < 100) return; // 10 lines/sec max
      lastMs = now;

      const entry = { type: evt.type };

      if (evt.type === "file.edited") {
        entry.path = pickFirst(
          evt.path,
          evt.file,
          evt.filePath,
          evt.properties?.path,
          evt.properties?.file
        );
      }

      if (evt.type.startsWith("tool.execute.")) {
        entry.tool = pickFirst(evt.tool, evt.properties?.tool, evt.input?.tool);
        entry.command = extractCommandish(evt);
      }

      if (evt.type === "message.updated") {
        // Useful to debug session routing without logging entire payload
        entry.sessionID = pickFirst(evt?.properties?.info?.sessionID, evt?.properties?.sessionID);
        entry.role = pickFirst(evt?.properties?.info?.role);
        entry.agent = pickFirst(evt?.properties?.info?.agent);
      }

      logAsync(entry);
    },
  };
};
