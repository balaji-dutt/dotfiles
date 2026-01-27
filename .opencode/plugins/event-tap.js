import { appendFile } from "node:fs/promises";

export default async () => {
  const enabled = /^(1|true|on)$/i.test(process.env.OPENCODE_EVENT_TAP || "");
  const outFile = process.env.OPENCODE_EVENT_TAP_FILE || "/tmp/opencode-event-tap.log";

  // Dump full payload once for one of these event types (in order)
  const dumpTargets = [
    "tui.command.execute",
    "session.idle",
  ];

  // If you want to force which one gets dumped:
  // OPENCODE_EVENT_TAP_DUMP=tui.command.execute
  const forcedDump = (process.env.OPENCODE_EVENT_TAP_DUMP || "").trim();

  let dumped = false;

  const log = (obj) => {
    if (!enabled) return;
    const line = `${new Date().toISOString()} ${JSON.stringify(obj)}\n`;
    void appendFile(outFile, line).catch(() => {});
  };

  // Best-effort redaction
  const redact = (value) => {
    if (typeof value !== "string") return value;
    // crude but helpful: redact obvious key/token-ish strings
    if (/sk-|api[_-]?key|token|bearer|anthropic|openai/i.test(value) && value.length > 12) {
      return "[REDACTED]";
    }
    return value;
  };

  const sanitize = (obj, depth = 0) => {
    if (depth > 6) return "[TRUNCATED_DEPTH]";
    if (obj === null || obj === undefined) return obj;

    if (Array.isArray(obj)) {
      if (obj.length > 50) return obj.slice(0, 50).map((x) => sanitize(x, depth + 1)).concat(["[TRUNCATED_ARRAY]"]);
      return obj.map((x) => sanitize(x, depth + 1));
    }

    if (typeof obj === "object") {
      const out = {};
      const keys = Object.keys(obj);
      // cap keys to avoid huge logs
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

  setTimeout(() => log({ kind: "loaded", enabled, outFile }), 0);

  const interesting = new Set([
    "file.edited",
    "tool.execute.before",
    "tool.execute.after",
    "session.idle",
    "tui.command.execute",
  ]);

  // Throttle for the small entries
  let lastMs = 0;

  return {
    event: async ({ event }) => {
      if (!enabled || !event?.type || !interesting.has(event.type)) return;

      // One-time full dump
      if (!dumped) {
        const target = forcedDump || dumpTargets.find((t) => t === event.type);
        if (target && event.type === target) {
          dumped = true;
          log({
            kind: "FULL_DUMP",
            note: "One-time sanitized dump of event payload",
            eventType: event.type,
            event: sanitize(event),
          });
        }
      }

      const now = Date.now();
      if (now - lastMs < 100) return;
      lastMs = now;

      // Small stable entries
      const entry = { type: event.type };

      if (event.type === "file.edited") {
        entry.path =
          event.path ??
          event.file ??
          event.filePath ??
          event.properties?.path ??
          event.properties?.file ??
          null;
      }

      if (event.type.startsWith("tool.execute.")) {
        entry.tool =
          event.tool ??
          event.properties?.tool ??
          event.input?.tool ??
          null;
      }

      if (event.type === "tui.command.execute") {
        entry.command =
          event.properties?.command ??
          event.properties?.name ??
          event.command ??
          null;
      }

      log(entry);
    },
  };
};
