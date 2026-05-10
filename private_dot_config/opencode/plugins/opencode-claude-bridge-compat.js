// opencode-claude-bridge-compat.js
// Local compatibility shim for opencode-claude-bridge.
//
// This plugin patches the final fetch boundary for Anthropic Messages requests.
// It is intentionally conservative: no payload contents, auth headers, or other
// sensitive request details are logged.

import { appendFile, mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const PLUGIN_NAME = "opencode-claude-bridge-compat";
const PLUGIN_VERSION = "1.0.0";

const COMPAT_ENV = "OPENCODE_CLAUDE_BRIDGE_COMPAT";
const DEBUG_ENV = "OPENCODE_CLAUDE_BRIDGE_COMPAT_DEBUG";
const CACHE_CONTROL_MAX_ENV = "OPENCODE_CLAUDE_BRIDGE_CACHE_CONTROL_MAX";
const FILTER_STUB_TOOLS_ENV = "OPENCODE_CLAUDE_BRIDGE_FILTER_STUB_TOOLS";

const WRAPPED_FETCH = Symbol.for(`${PLUGIN_NAME}.wrappedFetch`);
const STATE = Symbol.for(`${PLUGIN_NAME}.state`);

const STUB_TOOL_NAMES = new Set([
  "AskUserQuestion",
  "CronCreate",
  "CronDelete",
  "CronList",
  "EnterPlanMode",
  "EnterWorktree",
  "ExitPlanMode",
  "ExitWorktree",
  "Monitor",
  "NotebookEdit",
  "RemoteTrigger",
  "TaskOutput",
  "TaskStop",
  "WebSearch",
]);

function envFlag(name, defaultValue = true) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return defaultValue;
  return !/^(0|false|no|off)$/i.test(String(raw).trim());
}

function cacheControlMax() {
  const raw = process.env[CACHE_CONTROL_MAX_ENV];
  if (raw === undefined || raw === "") return 1;
  const parsed = Number.parseInt(String(raw), 10);
  if (!Number.isFinite(parsed) || parsed < 0) return 1;
  // Anthropic rejects more than four cache_control markers. Never allow an
  // environment override to reintroduce that failure mode.
  return Math.min(parsed, 4);
}

function debugLogPath() {
  return path.join(
    os.homedir(),
    ".local",
    "state",
    "opencode",
    `${PLUGIN_NAME}.log`
  );
}

async function appendDebugFile(line) {
  try {
    const logPath = debugLogPath();
    await mkdir(path.dirname(logPath), { recursive: true });
    await appendFile(
      logPath,
      `${new Date().toISOString()} [v${PLUGIN_VERSION}] ${line}\n`,
      "utf8"
    );
  } catch {}
}

function createDebugLogger(ctx) {
  return (message) => {
    if (!envFlag(DEBUG_ENV, false)) return;

    const line = `[${PLUGIN_NAME}] ${message}`;
    void appendDebugFile(line);
    const loggers = [ctx?.app?.log, ctx?.log, ctx?.logger];

    for (const logger of loggers) {
      try {
        if (typeof logger?.info === "function") {
          logger.info(line);
          return;
        }
        if (typeof logger?.warn === "function") {
          logger.warn(line);
          return;
        }
      } catch {}
    }

    try {
      console.warn(line);
    } catch {}
  };
}

function debugLog(message) {
  try {
    globalThis[STATE]?.debug?.(message);
  } catch {}
}

function requestUrl(input) {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  if (typeof input?.url === "string") return input.url;
  return "";
}

function requestMethod(input, init) {
  return String(init?.method || input?.method || "GET").toUpperCase();
}

function isAnthropicMessagesEndpoint(rawUrl) {
  if (!rawUrl) return false;

  try {
    const url = new URL(rawUrl);
    const path = url.pathname.replace(/\/+$/, "");
    return path.endsWith("/v1/messages") || path === "/messages";
  } catch {
    const path = String(rawUrl).split("?")[0].replace(/\/+$/, "");
    return path.endsWith("/v1/messages") || path === "/messages";
  }
}

function isPromptCacheMarker(value) {
  const marker = value?.cache_control;
  return (
    marker &&
    typeof marker === "object" &&
    !Array.isArray(marker) &&
    marker.type === "ephemeral"
  );
}

function collectCacheControlObjects(value, matches, seen = new Set()) {
  if (!value || typeof value !== "object" || seen.has(value)) return;
  seen.add(value);

  if (isPromptCacheMarker(value)) matches.push(value);

  if (Array.isArray(value)) {
    for (const item of value) collectCacheControlObjects(item, matches, seen);
    return;
  }

  for (const child of Object.values(value)) {
    collectCacheControlObjects(child, matches, seen);
  }
}

function filterStubTools(parsed, stats) {
  if (!envFlag(FILTER_STUB_TOOLS_ENV, true)) return;
  if (!Array.isArray(parsed?.tools)) return;

  const kept = [];
  const removed = [];

  for (const tool of parsed.tools) {
    const name = typeof tool?.name === "string" ? tool.name : "";
    if (STUB_TOOL_NAMES.has(name)) {
      removed.push(name);
      continue;
    }
    kept.push(tool);
  }

  if (removed.length === 0) return;

  if (kept.length === 0) {
    delete parsed.tools;
    stats.toolsDeleted = true;
  } else {
    parsed.tools = kept;
  }
  stats.stubToolsRemoved = removed;
  stats.changed = true;

  const removedNames = new Set(removed);
  if (
    parsed.tool_choice &&
    (kept.length === 0 ||
      (parsed.tool_choice.type === "tool" &&
        removedNames.has(parsed.tool_choice.name)))
  ) {
    delete parsed.tool_choice;
    stats.toolChoiceRemoved = true;
  }
}

function trimCacheControlMarkers(parsed, stats) {
  const matches = [];
  collectCacheControlObjects(parsed, matches);

  const keep = cacheControlMax();
  const removeCount = Math.max(0, matches.length - keep);

  stats.cacheControlTotal = matches.length;
  stats.cacheControlKept = matches.length - removeCount;
  stats.cacheControlRemoved = removeCount;

  for (let i = 0; i < removeCount; i++) {
    delete matches[i].cache_control;
    stats.changed = true;
  }
}

function sanitizePayload(parsed) {
  const stats = {
    changed: false,
    cacheControlTotal: 0,
    cacheControlKept: 0,
    cacheControlRemoved: 0,
    stubToolsRemoved: [],
    toolsDeleted: false,
    toolChoiceRemoved: false,
  };

  filterStubTools(parsed, stats);
  trimCacheControlMarkers(parsed, stats);

  return stats;
}

function headersWithoutContentLength(headers) {
  if (!headers) return headers;

  if (typeof Headers !== "undefined" && headers instanceof Headers) {
    const copy = new Headers(headers);
    copy.delete("content-length");
    return copy;
  }

  if (Array.isArray(headers)) {
    return headers.filter(([key]) => String(key).toLowerCase() !== "content-length");
  }

  if (typeof headers === "object") {
    const copy = {};
    for (const [key, value] of Object.entries(headers)) {
      if (String(key).toLowerCase() !== "content-length") copy[key] = value;
    }
    return copy;
  }

  return headers;
}

function sanitizeFetchArgs(input, init) {
  const rawUrl = requestUrl(input);
  if (!isAnthropicMessagesEndpoint(rawUrl)) return null;
  if (requestMethod(input, init) !== "POST") return null;
  if (typeof init?.body !== "string") return null;

  let parsed;
  try {
    parsed = JSON.parse(init.body);
  } catch {
    return null;
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;

  const stats = sanitizePayload(parsed);
  if (!stats.changed) return null;

  const nextInit = {
    ...init,
    body: JSON.stringify(parsed),
    headers: headersWithoutContentLength(init.headers),
  };

  debugLog(
    `sanitized ` +
      `cache_control=${stats.cacheControlTotal}/${stats.cacheControlRemoved}` +
      `/${stats.cacheControlKept} ` +
      `stub_tools_removed=${stats.stubToolsRemoved.length}` +
      `${stats.toolsDeleted ? " tools_deleted=true" : ""}` +
      `${stats.toolChoiceRemoved ? " tool_choice_removed=true" : ""}`
  );

  return { input, init: nextInit };
}

export default async (ctx = {}) => {
  globalThis[STATE] = { debug: createDebugLogger(ctx) };

  if (!envFlag(COMPAT_ENV, true)) {
    debugLog(`disabled by ${COMPAT_ENV}`);
    return {};
  }

  if (typeof globalThis.fetch !== "function") {
    debugLog("globalThis.fetch is unavailable; plugin disabled");
    return {};
  }

  if (globalThis.fetch[WRAPPED_FETCH]) {
    debugLog("fetch wrapper already installed");
    return {};
  }

  const previousFetch = globalThis.fetch;

  async function compatFetch(input, init) {
    if (!envFlag(COMPAT_ENV, true)) {
      return previousFetch.call(this, input, init);
    }

    try {
      const sanitized = sanitizeFetchArgs(input, init);
      if (sanitized) {
        return previousFetch.call(this, sanitized.input, sanitized.init);
      }
    } catch {
      debugLog("sanitize failed; forwarding unchanged");
    }

    return previousFetch.call(this, input, init);
  }

  Object.defineProperty(compatFetch, WRAPPED_FETCH, { value: true });
  globalThis.fetch = compatFetch;

  debugLog(`initialized v${PLUGIN_VERSION}`);
  return {};
};
