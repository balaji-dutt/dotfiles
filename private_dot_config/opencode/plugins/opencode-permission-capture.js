// opencode-permission-capture.js
// Opt-in recorder for OpenCode permission prompts.
//
// Capture is disabled by default. Start it explicitly with the
// permission_capture_start tool, or set OPENCODE_PERMISSION_CAPTURE=1 before
// launching OpenCode. The plugin records selected permission fields only and
// never changes the permission decision.

import { appendFile, chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { tool } from "@opencode-ai/plugin";

const PLUGIN_NAME = "opencode-permission-capture";
const PLUGIN_VERSION = "1.0.0";

const ENABLE_ENV = "OPENCODE_PERMISSION_CAPTURE";
const FILE_ENV = "OPENCODE_PERMISSION_CAPTURE_FILE";
const MAX_TEXT_ENV = "OPENCODE_PERMISSION_CAPTURE_MAX_TEXT_CHARS";

const STATE = Symbol.for(`${PLUGIN_NAME}.state`);
const DEFAULT_MAX_TEXT_CHARS = 20_000;
const DEFAULT_READ_LIMIT = 50;
const MAX_READ_LIMIT = 500;
const CAPTURE_SCOPES = new Set(["bash", "all"]);

function envFlag(name, defaultValue = false) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return defaultValue;
  return /^(1|true|yes|on)$/i.test(String(raw).trim());
}

function envInt(name, defaultValue) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return defaultValue;
  const parsed = Number.parseInt(String(raw), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return defaultValue;
  return parsed;
}

function expandHome(input) {
  if (typeof input !== "string" || input === "") return input;
  if (input === "~") return os.homedir();
  if (input.startsWith(`~${path.sep}`)) return path.join(os.homedir(), input.slice(2));
  if (path.sep === "\\" && input.startsWith("~/")) {
    return path.join(os.homedir(), input.slice(2));
  }
  return input;
}

function defaultStateDir() {
  if (process.platform === "win32") {
    return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "opencode");
  }

  return path.join(os.homedir(), ".local", "state", "opencode");
}

function defaultCapturePath() {
  const configured = process.env[FILE_ENV];
  if (configured && configured.trim() !== "") return path.resolve(expandHome(configured.trim()));
  return path.join(defaultStateDir(), "permission-prompts.jsonl");
}

function getState(ctx) {
  const existing = globalThis[STATE] || {};
  const next = {
    enabled: existing.enabled ?? envFlag(ENABLE_ENV, false),
    scope: existing.scope || "bash",
    filePath: existing.filePath || defaultCapturePath(),
    directory: ctx?.directory || existing.directory || "",
    worktree: ctx?.worktree || existing.worktree || "",
    written: existing.written || 0,
    lastError: existing.lastError || "",
  };

  globalThis[STATE] = next;
  return next;
}

function state() {
  return globalThis[STATE] || getState();
}

function asArray(value) {
  if (value === undefined || value === null) return [];
  return Array.isArray(value) ? value : [value];
}

function uniqueStrings(values) {
  const out = [];
  const seen = new Set();

  for (const value of values) {
    if (value === undefined || value === null) continue;
    const text = String(value);
    if (text === "" || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }

  return out;
}

function redactText(value) {
  const maxTextChars = envInt(MAX_TEXT_ENV, DEFAULT_MAX_TEXT_CHARS);
  let text = String(value ?? "");

  text = text.replace(/([a-z][a-z0-9+.-]*:\/\/)([^\s/:@]+):([^\s/@]+)@/gi, "$1<redacted>:<redacted>@");
  text = text.replace(/\b(Authorization:\s*Bearer\s+|Bearer\s+)[A-Za-z0-9._~+/-]+=*/gi, "$1<redacted>");
  text = text.replace(
    /\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASS|API_?KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIAL|AUTH_TOKEN)[A-Z0-9_]*)=([^\s'"`]+)/gi,
    "$1=<redacted>"
  );
  text = text.replace(
    /(--(?:token|secret|password|pass|api-key|apikey|access-token|auth-token|private-key)(?:=|\s+))([^\s'"`]+)/gi,
    "$1<redacted>"
  );
  text = text.replace(
    /(["']?(?:token|secret|password|apiKey|api_key|accessToken|access_token|refreshToken|refresh_token)["']?\s*[:=]\s*)(["']?)([^,"'\s}]+)\2/gi,
    "$1$2<redacted>$2"
  );

  if (text.length <= maxTextChars) return text;
  return `${text.slice(0, maxTextChars)}…[truncated ${text.length - maxTextChars} chars]`;
}

function sanitizedStrings(values) {
  return uniqueStrings(values).map(redactText);
}

function getNestedString(input, keys) {
  let current = input;
  for (const key of keys) {
    if (current === undefined || current === null || typeof current !== "object") return "";
    current = current[key];
  }

  return typeof current === "string" && current.trim() !== "" ? current : "";
}

function permissionName(input) {
  return String(input?.permission || input?.type || input?.metadata?.permission || "");
}

function permissionPatterns(input) {
  return uniqueStrings([...asArray(input?.patterns), ...asArray(input?.pattern)]);
}

function permissionAlways(input) {
  return uniqueStrings(asArray(input?.always));
}

function commandCandidates(input) {
  const metadata = input?.metadata || {};
  const candidates = [
    getNestedString(metadata, ["input", "command"]),
    getNestedString(metadata, ["input", "cmd"]),
    getNestedString(metadata, ["command"]),
    getNestedString(metadata, ["cmd"]),
    getNestedString(metadata, ["shell", "command"]),
    getNestedString(metadata, ["data", "command"]),
  ];

  if (permissionName(input) === "bash") candidates.push(...permissionPatterns(input));
  return sanitizedStrings(candidates);
}

function shouldCapture(input, output) {
  const current = state();
  if (!current.enabled) return false;
  if (output?.status !== "ask") return false;
  if (current.scope === "all") return true;
  return permissionName(input) === "bash";
}

function captureRecord(input, output) {
  const current = state();
  const metadata = input?.metadata || {};

  return {
    schema: 1,
    timestamp: new Date().toISOString(),
    plugin: {
      name: PLUGIN_NAME,
      version: PLUGIN_VERSION,
    },
    status: output?.status || "",
    permission: permissionName(input),
    sessionID: input?.sessionID || "",
    messageID: input?.messageID || input?.tool?.messageID || "",
    callID: input?.callID || input?.tool?.callID || "",
    title: redactText(input?.title || metadata.title || ""),
    commands: commandCandidates(input),
    patterns: sanitizedStrings(permissionPatterns(input)),
    always: sanitizedStrings(permissionAlways(input)),
    directory: current.directory,
    worktree: current.worktree,
  };
}

async function appendRecord(record) {
  const current = state();

  try {
    await mkdir(path.dirname(current.filePath), { recursive: true });
    await appendFile(current.filePath, `${JSON.stringify(record)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    await restrictCaptureFile(current.filePath);
    current.written += 1;
    current.lastError = "";
  } catch (err) {
    current.lastError = err instanceof Error ? err.message : String(err);
  }
}

async function restrictCaptureFile(filePath) {
  try {
    await chmod(filePath, 0o600);
  } catch (err) {
    if (process.platform !== "win32") throw err;
  }
}

function statusPayload() {
  const current = state();
  return {
    enabled: current.enabled,
    scope: current.scope,
    file: current.filePath,
    writtenThisProcess: current.written,
    lastError: current.lastError,
  };
}

function statusText(prefix = "Permission capture status") {
  return `${prefix}: ${JSON.stringify(statusPayload(), null, 2)}`;
}

function setCaptureEnabled(enabled, args = {}) {
  const current = state();
  const scope = args.scope || current.scope || "bash";
  if (!CAPTURE_SCOPES.has(scope)) throw new Error(`Invalid capture scope: ${scope}`);

  current.enabled = enabled;
  current.scope = scope;

  if (typeof args.file === "string" && args.file.trim() !== "") {
    current.filePath = path.resolve(expandHome(args.file.trim()));
  }

  current.lastError = "";
  return current;
}

async function readRecords(limit) {
  const current = state();
  let raw;

  try {
    raw = await readFile(current.filePath, "utf8");
  } catch (err) {
    if (err?.code === "ENOENT") return [];
    throw err;
  }

  const lines = raw.split(/\r?\n/).filter((line) => line.trim() !== "");
  const selected = lines.slice(-limit);

  return selected.map((line) => {
    try {
      return JSON.parse(line);
    } catch {
      return { schema: 1, parseError: true, raw: redactText(line) };
    }
  });
}

export default async function OpenCodePermissionCapture(ctx) {
  getState(ctx);

  return {
    name: `${PLUGIN_NAME}@${PLUGIN_VERSION}`,

    "permission.ask": async (input, output) => {
      if (!shouldCapture(input, output)) return;
      await appendRecord(captureRecord(input, output));
    },

    tool: {
      permission_capture_start: tool({
        description:
          "Explicitly start recording OpenCode permission prompts for this process. Requires confirm='start'. Default scope records bash prompts only.",
        args: {
          confirm: tool.schema.string().describe("Must be exactly 'start'."),
          scope: tool.schema
            .string()
            .optional()
            .describe("Capture bash permission prompts only, or all permission prompts. Defaults to bash."),
          file: tool.schema
            .string()
            .optional()
            .describe("Optional capture file path. Defaults to the OpenCode state directory."),
        },
        async execute(args = {}) {
          if (args.confirm !== "start") return "Capture not started: confirm must be exactly 'start'.";
          setCaptureEnabled(true, { scope: args.scope || "bash", file: args.file });
          return statusText("Permission capture started");
        },
      }),

      permission_capture_stop: tool({
        description: "Stop recording OpenCode permission prompts for this process. Requires confirm='stop'.",
        args: {
          confirm: tool.schema.string().describe("Must be exactly 'stop'."),
        },
        async execute(args = {}) {
          if (args.confirm !== "stop") return "Capture not stopped: confirm must be exactly 'stop'.";
          setCaptureEnabled(false);
          return statusText("Permission capture stopped");
        },
      }),

      permission_capture_status: tool({
        description: "Report whether OpenCode permission prompt capture is active and where records are stored.",
        args: {},
        async execute() {
          return statusText();
        },
      }),

      permission_capture_read: tool({
        description: "Read recent captured OpenCode permission prompt records from the capture file.",
        args: {
          limit: tool.schema
            .number()
            .int()
            .positive()
            .max(MAX_READ_LIMIT)
            .optional()
            .describe(`Maximum records to return. Defaults to ${DEFAULT_READ_LIMIT}.`),
        },
        async execute(args = {}) {
          const limit = args.limit ?? DEFAULT_READ_LIMIT;
          const records = await readRecords(limit);
          return JSON.stringify(
            {
              ...statusPayload(),
              returned: records.length,
              records,
            },
            null,
            2
          );
        },
      }),

      permission_capture_clear: tool({
        description:
          "Clear captured OpenCode permission prompt records after the user approves the review outcome. Requires confirm='clear permission capture'.",
        args: {
          confirm: tool.schema.string().describe("Must be exactly 'clear permission capture'."),
        },
        async execute(args = {}) {
          if (args.confirm !== "clear permission capture") {
            return "Capture not cleared: confirm must be exactly 'clear permission capture'.";
          }

          const current = state();
          await mkdir(path.dirname(current.filePath), { recursive: true });
          await writeFile(current.filePath, "", { encoding: "utf8", mode: 0o600 });
          await restrictCaptureFile(current.filePath);
          current.written = 0;
          current.lastError = "";
          return statusText("Permission capture cleared");
        },
      }),
    },
  };
}
