// opencode-quota-anthropic-compat.js
// Local compatibility shim for @slkiser/opencode-quota Anthropic usage probes.
//
// This plugin patches only the Claude OAuth usage endpoint used by
// opencode-quota. It intentionally never logs or stores request headers,
// bearer tokens, or credential material.

import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const PLUGIN_NAME = "opencode-quota-anthropic-compat";
const PLUGIN_VERSION = "1.0.0";

const COMPAT_ENV = "OPENCODE_QUOTA_ANTHROPIC_COMPAT";
const DEBUG_ENV = "OPENCODE_QUOTA_ANTHROPIC_COMPAT_DEBUG";
const CACHE_TTL_ENV = "OPENCODE_QUOTA_ANTHROPIC_CACHE_TTL_MS";
const STALE_TTL_ENV = "OPENCODE_QUOTA_ANTHROPIC_STALE_TTL_MS";

const DEFAULT_CACHE_TTL_MS = 10 * 60 * 1000;
const DEFAULT_STALE_TTL_MS = 5 * 60 * 60 * 1000;

const WRAPPED_FETCH = Symbol.for(`${PLUGIN_NAME}.wrappedFetch`);
const STATE = Symbol.for(`${PLUGIN_NAME}.state`);

function envFlag(name, defaultValue = true) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return defaultValue;
  return !/^(0|false|no|off)$/i.test(String(raw).trim());
}

function durationMs(name, defaultValue) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return defaultValue;
  const parsed = Number.parseInt(String(raw), 10);
  if (!Number.isFinite(parsed) || parsed <= 0) return defaultValue;
  return parsed;
}

function stateDir() {
  return path.join(os.homedir(), ".local", "state", "opencode");
}

function cachePath() {
  return path.join(stateDir(), `${PLUGIN_NAME}.json`);
}

function debugLogPath() {
  return path.join(stateDir(), `${PLUGIN_NAME}.log`);
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

function isAnthropicUsageEndpoint(rawUrl) {
  if (!rawUrl) return false;

  try {
    const url = new URL(rawUrl);
    return (
      url.protocol === "https:" &&
      url.hostname === "api.anthropic.com" &&
      url.pathname.replace(/\/+$/, "") === "/api/oauth/usage"
    );
  } catch {
    return false;
  }
}

function cacheAgeMs(cache) {
  return Math.max(0, Date.now() - cache.cachedAtMs);
}

function isFresh(cache) {
  return cacheAgeMs(cache) <= durationMs(CACHE_TTL_ENV, DEFAULT_CACHE_TTL_MS);
}

function isStaleUsable(cache) {
  return cacheAgeMs(cache) <= staleTtlMs();
}

function staleTtlMs() {
  return Math.min(durationMs(STALE_TTL_ENV, DEFAULT_STALE_TTL_MS), DEFAULT_STALE_TTL_MS);
}

function shouldUseStaleForStatus(status) {
  return status === 408 || status === 429 || status >= 500;
}

function cachedResponse(cache) {
  return new Response(cache.body, {
    status: 200,
    statusText: "OK",
    headers: {
      "content-type": "application/json",
    },
  });
}

async function readCache() {
  try {
    const raw = await readFile(cachePath(), "utf8");
    const parsed = JSON.parse(raw);
    if (!Number.isFinite(parsed?.cachedAtMs)) return null;
    if (parsed.cachedAtMs > Date.now()) return null;
    if (typeof parsed?.body !== "string") return null;
    JSON.parse(parsed.body);
    return parsed;
  } catch {
    return null;
  }
}

async function writeCache(body) {
  try {
    JSON.parse(body);
    const dir = stateDir();
    const target = cachePath();
    const tmp = `${target}.${process.pid}.${Date.now()}.tmp`;
    const payload = `${JSON.stringify({ cachedAtMs: Date.now(), body })}\n`;

    await mkdir(dir, { recursive: true });
    await writeFile(tmp, payload, { encoding: "utf8", mode: 0o600 });
    await rename(tmp, target);
    return true;
  } catch {
    return false;
  }
}

async function cacheSuccessfulResponse(response) {
  try {
    const body = await response.clone().text();
    return await writeCache(body);
  } catch {
    return false;
  }
}

async function fetchWithQuotaCache(originalFetch, input, init) {
  const rawUrl = requestUrl(input);
  const method = requestMethod(input, init);

  if (method !== "GET" || !isAnthropicUsageEndpoint(rawUrl)) {
    return originalFetch(input, init);
  }

  const cache = await readCache();
  if (cache && isFresh(cache)) {
    debugLog(`served_cache=fresh age_ms=${cacheAgeMs(cache)}`);
    return cachedResponse(cache);
  }

  let response;
  try {
    response = await originalFetch(input, init);
  } catch (err) {
    if (cache && isStaleUsable(cache)) {
      debugLog(`served_cache=stale reason=error age_ms=${cacheAgeMs(cache)}`);
      return cachedResponse(cache);
    }

    debugLog("live_error cache=miss");
    throw err;
  }

  if (response?.ok) {
    const saved = await cacheSuccessfulResponse(response);
    debugLog(`live_ok cache_saved=${saved ? 1 : 0}`);
    return response;
  }

  if (cache && isStaleUsable(cache) && shouldUseStaleForStatus(response.status)) {
    debugLog(
      `served_cache=stale live_status=${response.status} age_ms=${cacheAgeMs(cache)}`
    );
    return cachedResponse(cache);
  }

  debugLog(`live_status=${response?.status || 0} cache=miss`);
  return response;
}

export default async function OpenCodeQuotaAnthropicCompat(ctx) {
  globalThis[STATE] = {
    ...(globalThis[STATE] || {}),
    debug: createDebugLogger(ctx),
  };

  if (!envFlag(COMPAT_ENV, true)) {
    debugLog("disabled");
    return { name: PLUGIN_NAME };
  }

  if (globalThis[WRAPPED_FETCH]) {
    debugLog("already_initialized");
    return { name: PLUGIN_NAME };
  }

  if (typeof globalThis.fetch !== "function") {
    debugLog("fetch_unavailable");
    return { name: PLUGIN_NAME };
  }

  const originalFetch = globalThis.fetch.bind(globalThis);

  globalThis.fetch = async function quotaAnthropicCompatFetch(input, init) {
    return fetchWithQuotaCache(originalFetch, input, init);
  };

  globalThis[WRAPPED_FETCH] = true;
  debugLog(
    `initialized cache_ttl_ms=${durationMs(CACHE_TTL_ENV, DEFAULT_CACHE_TTL_MS)} stale_ttl_ms=${staleTtlMs()}`
  );

  return { name: PLUGIN_NAME };
}
