// opencode-quota-anthropic-compat.js
// Local compatibility shim for @slkiser/opencode-quota Anthropic usage probes.
//
// This plugin patches only the Claude OAuth usage endpoint used by
// opencode-quota. It intentionally never logs or stores request headers,
// bearer tokens, or raw credential material. It stores only a local one-way
// token fingerprint so cache entries can be invalidated after Claude auth changes.

import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFile, mkdir, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const PLUGIN_NAME = "opencode-quota-anthropic-compat";
const PLUGIN_VERSION = "1.1.0";

const COMPAT_ENV = "OPENCODE_QUOTA_ANTHROPIC_COMPAT";
const DEBUG_ENV = "OPENCODE_QUOTA_ANTHROPIC_COMPAT_DEBUG";
const CACHE_TTL_ENV = "OPENCODE_QUOTA_ANTHROPIC_CACHE_TTL_MS";
const STALE_TTL_ENV = "OPENCODE_QUOTA_ANTHROPIC_STALE_TTL_MS";
const BACKOFF_TTL_ENV = "OPENCODE_QUOTA_ANTHROPIC_BACKOFF_TTL_MS";
const AUTH_REFRESH_ENV = "OPENCODE_QUOTA_ANTHROPIC_AUTH_REFRESH";

const DEFAULT_CACHE_TTL_MS = 10 * 60 * 1000;
const DEFAULT_STALE_TTL_MS = 5 * 60 * 60 * 1000;
const DEFAULT_BACKOFF_TTL_MS = 30 * 60 * 1000;
const KEYCHAIN_TIMEOUT_MS = 3000;
const AUTH_REFRESH_RETRY_DELAYS_MS = [2000, 10000];

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

function authStatePath() {
  return path.join(stateDir(), `${PLUGIN_NAME}-auth.json`);
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

function tokenFingerprint(token) {
  if (typeof token !== "string" || token.trim() === "") return null;
  return createHash("sha256").update(token).digest("hex");
}

function isValidFingerprint(value) {
  return typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
}

function normalizeHeaderValue(value) {
  if (value === undefined || value === null) return null;
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  return String(value);
}

function readHeaderValue(headers, name) {
  if (!headers) return null;
  const lowerName = name.toLowerCase();

  try {
    if (typeof Headers !== "undefined" && headers instanceof Headers) {
      return normalizeHeaderValue(headers.get(name));
    }
  } catch {}

  try {
    if (typeof headers.get === "function") {
      return normalizeHeaderValue(headers.get(name) || headers.get(lowerName));
    }
  } catch {}

  if (Array.isArray(headers)) {
    for (const entry of headers) {
      if (!Array.isArray(entry) || entry.length < 2) continue;
      if (String(entry[0]).toLowerCase() === lowerName) return normalizeHeaderValue(entry[1]);
    }
    return null;
  }

  if (typeof headers === "object") {
    for (const [key, value] of Object.entries(headers)) {
      if (String(key).toLowerCase() === lowerName) return normalizeHeaderValue(value);
    }
  }

  return null;
}

function bearerTokenFromAuthorization(value) {
  const match = normalizeHeaderValue(value)?.match(/^\s*Bearer\s+(.+?)\s*$/i);
  return match?.[1] || null;
}

function requestAuthFingerprint(input, init) {
  const authorization =
    readHeaderValue(init?.headers, "authorization") || readHeaderValue(input?.headers, "authorization");
  return tokenFingerprint(bearerTokenFromAuthorization(authorization));
}

function cacheMatchesAuth(cache, authFingerprint) {
  if (!authFingerprint) return true;
  return cache?.authFingerprint === authFingerprint;
}

function cacheCanFallbackAfterTransient(cache, authFingerprint) {
  if (!authFingerprint) return true;
  if (!cache?.authFingerprint) return true;
  return cache.authFingerprint === authFingerprint;
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

function backoffTtlMs() {
  return Math.min(durationMs(BACKOFF_TTL_ENV, DEFAULT_BACKOFF_TTL_MS), staleTtlMs());
}

function backoffRemainingMs(cache) {
  if (!Number.isFinite(cache?.backoffUntilMs)) return 0;
  return Math.max(0, cache.backoffUntilMs - Date.now());
}

function isBackoffActive(cache) {
  return isStaleUsable(cache) && backoffRemainingMs(cache) > 0;
}

function cacheLogState(cache) {
  if (!cache) return "cache=miss";
  if (!isStaleUsable(cache)) return `cache=expired age_ms=${cacheAgeMs(cache)}`;
  return `cache=unused age_ms=${cacheAgeMs(cache)}`;
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
    if (parsed.authFingerprint !== undefined && !isValidFingerprint(parsed.authFingerprint)) return null;
    JSON.parse(parsed.body);
    return parsed;
  } catch {
    return null;
  }
}

async function writeCache(body, authFingerprint) {
  try {
    JSON.parse(body);
    return await writeCacheRecord({
      cachedAtMs: Date.now(),
      body,
      ...(authFingerprint ? { authFingerprint } : {}),
    });
  } catch {
    return false;
  }
}

async function writeCacheRecord(record) {
  try {
    JSON.parse(record.body);
    const dir = stateDir();
    const target = cachePath();
    const tmp = `${target}.${process.pid}.${Date.now()}.tmp`;
    const payload = `${JSON.stringify(record)}\n`;

    await mkdir(dir, { recursive: true });
    await writeFile(tmp, payload, { encoding: "utf8", mode: 0o600 });
    await rename(tmp, target);
    return true;
  } catch {
    return false;
  }
}

async function recordBackoff(cache, details = {}) {
  const ttl = backoffTtlMs();
  const backoffUntilMs = Math.min(Date.now() + ttl, cache.cachedAtMs + staleTtlMs());
  const authFingerprint = details.authFingerprint || cache.authFingerprint;

  await writeCacheRecord({
    cachedAtMs: cache.cachedAtMs,
    body: cache.body,
    ...(authFingerprint ? { authFingerprint } : {}),
    backoffUntilMs,
    ...(Number.isFinite(details.status) ? { backoffStatus: details.status } : {}),
    ...(details.reason ? { backoffReason: details.reason } : {}),
  });

  return Math.max(0, backoffUntilMs - Date.now());
}

async function cacheSuccessfulResponse(response, authFingerprint) {
  try {
    const body = await response.clone().text();
    return await writeCache(body, authFingerprint);
  } catch {
    return false;
  }
}

function credentialTokenFromObject(value) {
  if (!value || typeof value !== "object") return null;
  const candidateObjects = [value.claudeAiOauth, value.oauth, value];

  for (const candidate of candidateObjects) {
    if (!candidate || typeof candidate !== "object") continue;
    for (const key of ["accessToken", "access_token", "token"]) {
      const token = candidate[key];
      if (typeof token === "string" && token.trim() !== "") return token.trim();
    }
  }

  return null;
}

function credentialTokenFromPayload(payload, allowPlaintext = false) {
  const text = typeof payload === "string" ? payload.trim() : "";
  if (!text) return null;

  try {
    const parsed = JSON.parse(text);
    if (typeof parsed === "string" && allowPlaintext && parsed.trim() !== "") {
      return parsed.trim();
    }
    return credentialTokenFromObject(parsed);
  } catch {
    return allowPlaintext ? text : null;
  }
}

function execFileText(command, args, options = {}) {
  return new Promise((resolve) => {
    execFile(command, args, options, (error, stdout) => {
      if (error) {
        resolve(null);
        return;
      }
      resolve(String(stdout || "").trim());
    });
  });
}

async function readMacOSKeychainCredential() {
  if (process.platform !== "darwin") return null;

  const payload = await execFileText(
    "security",
    ["find-generic-password", "-s", "Claude Code-credentials", "-w"],
    { timeout: KEYCHAIN_TIMEOUT_MS, maxBuffer: 128 * 1024 }
  );
  const token = credentialTokenFromPayload(payload, true);
  const fingerprint = tokenFingerprint(token);

  return fingerprint ? { status: "configured", fingerprint, source: "macos-keychain" } : null;
}

async function readCredentialsFileCredential() {
  try {
    const payload = await readFile(path.join(os.homedir(), ".claude", ".credentials.json"), "utf8");
    const token = credentialTokenFromPayload(payload, false);
    const fingerprint = tokenFingerprint(token);
    return fingerprint ? { status: "configured", fingerprint, source: "credentials-file" } : null;
  } catch {
    return null;
  }
}

async function resolveClaudeCredentialFingerprint() {
  return (
    (await readMacOSKeychainCredential()) ||
    (await readCredentialsFileCredential()) ||
    { status: "unavailable" }
  );
}

async function readAuthState() {
  try {
    const parsed = JSON.parse(await readFile(authStatePath(), "utf8"));
    const status = parsed?.status === "configured" ? "configured" : "unavailable";
    const lastConfiguredFingerprint = isValidFingerprint(parsed?.lastConfiguredFingerprint)
      ? parsed.lastConfiguredFingerprint
      : undefined;
    return {
      status,
      ...(lastConfiguredFingerprint ? { lastConfiguredFingerprint } : {}),
      ...(typeof parsed?.source === "string" ? { source: parsed.source } : {}),
      ...(Number.isFinite(parsed?.observedAtMs) ? { observedAtMs: parsed.observedAtMs } : {}),
    };
  } catch {
    return null;
  }
}

async function writeAuthState(record) {
  try {
    const dir = stateDir();
    const target = authStatePath();
    const tmp = `${target}.${process.pid}.${Date.now()}.tmp`;
    await mkdir(dir, { recursive: true });
    await writeFile(tmp, `${JSON.stringify(record)}\n`, { encoding: "utf8", mode: 0o600 });
    await rename(tmp, target);
    return true;
  } catch {
    return false;
  }
}

async function purgeLocalUsageCacheForAuth(authFingerprint) {
  const cache = await readCache();
  if (!cache || cache.authFingerprint === authFingerprint) return false;

  try {
    await rm(cachePath(), { force: true });
    return true;
  } catch {
    return false;
  }
}

function upstreamProviderCacheDirs() {
  const home = os.homedir();
  const candidates = new Set();
  const add = (dir) => {
    if (dir) candidates.add(path.join(dir, "opencode", "quota-provider-state"));
  };

  add(process.env.XDG_CACHE_HOME || path.join(home, ".cache"));
  add(path.join(home, ".cache"));

  if (process.platform === "darwin") {
    add(path.join(home, "Library", "Caches"));
    add(path.join(home, "Library", "Application Support"));
  }

  if (process.platform === "win32") {
    add(process.env.LOCALAPPDATA);
    add(process.env.APPDATA);
  }

  return [...candidates];
}

async function purgeUpstreamAnthropicProviderCache() {
  let purged = 0;

  for (const dir of upstreamProviderCacheDirs()) {
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of entries) {
      if (!/^anthropic-.*\.json$/u.test(entry.name)) continue;
      if (!entry.isFile() && !entry.isSymbolicLink()) continue;

      try {
        await rm(path.join(dir, entry.name), { force: true });
        purged += 1;
      } catch {}
    }
  }

  return purged;
}

async function reconcileLaunchAuthCache() {
  const state = (globalThis[STATE] ||= {});
  if (state.authRefreshRunning) return;
  state.authRefreshRunning = true;

  try {
    const previous = await readAuthState();
    const current = await resolveClaudeCredentialFingerprint();
    const observedAtMs = Date.now();

    if (current.status !== "configured") {
      await writeAuthState({
        status: "unavailable",
        ...(previous?.lastConfiguredFingerprint
          ? { lastConfiguredFingerprint: previous.lastConfiguredFingerprint }
          : {}),
        observedAtMs,
      });
      debugLog("auth_refresh status=unavailable purged_provider_cache_files=0");
      return;
    }

    const authChanged =
      previous?.status !== "configured" || previous.lastConfiguredFingerprint !== current.fingerprint;

    let purgedLocalUsageCache = false;
    let purgedProviderCacheFiles = 0;

    if (authChanged) {
      purgedLocalUsageCache = await purgeLocalUsageCacheForAuth(current.fingerprint);
      purgedProviderCacheFiles = await purgeUpstreamAnthropicProviderCache();
    }

    await writeAuthState({
      status: "configured",
      lastConfiguredFingerprint: current.fingerprint,
      source: current.source,
      observedAtMs,
    });

    debugLog(
      `auth_refresh status=configured auth_changed=${authChanged ? 1 : 0} purged_local_cache=${purgedLocalUsageCache ? 1 : 0} purged_provider_cache_files=${purgedProviderCacheFiles}`
    );
  } catch (err) {
    debugLog(`auth_refresh error=${err?.name || "unknown"}`);
  } finally {
    state.authRefreshRunning = false;
  }
}

function scheduleLaunchAuthReconciliation() {
  if (!envFlag(AUTH_REFRESH_ENV, true)) {
    debugLog("auth_refresh=disabled");
    return;
  }

  const state = (globalThis[STATE] ||= {});
  if (state.authRefreshScheduled) return;
  state.authRefreshScheduled = true;

  void reconcileLaunchAuthCache();

  for (const delayMs of AUTH_REFRESH_RETRY_DELAYS_MS) {
    const timer = setTimeout(() => {
      void reconcileLaunchAuthCache();
    }, delayMs);
    timer.unref?.();
  }
}

async function fetchWithQuotaCache(originalFetch, input, init) {
  const rawUrl = requestUrl(input);
  const method = requestMethod(input, init);

  if (method !== "GET" || !isAnthropicUsageEndpoint(rawUrl)) {
    return originalFetch(input, init);
  }

  const cache = await readCache();
  const authFingerprint = requestAuthFingerprint(input, init);
  const cacheMatchesCurrentAuth = cacheMatchesAuth(cache, authFingerprint);

  if (cache && !cacheMatchesCurrentAuth) {
    debugLog(`cache_auth=mismatch age_ms=${cacheAgeMs(cache)}`);
  }

  if (cache && cacheMatchesCurrentAuth && isFresh(cache)) {
    debugLog(`served_cache=fresh age_ms=${cacheAgeMs(cache)}`);
    return cachedResponse(cache);
  }

  if (cache && cacheMatchesCurrentAuth && isBackoffActive(cache)) {
    debugLog(
      `served_cache=stale reason=backoff age_ms=${cacheAgeMs(cache)} backoff_remaining_ms=${backoffRemainingMs(cache)}`
    );
    return cachedResponse(cache);
  }

  let response;
  try {
    response = await originalFetch(input, init);
  } catch (err) {
    if (cache && isStaleUsable(cache) && cacheCanFallbackAfterTransient(cache, authFingerprint)) {
      const backoffMs = await recordBackoff(cache, { reason: "error", authFingerprint });
      debugLog(
        `served_cache=stale reason=error age_ms=${cacheAgeMs(cache)} backoff_ms=${backoffMs}`
      );
      return cachedResponse(cache);
    }

    debugLog(`live_error ${cacheLogState(cache)}`);
    throw err;
  }

  if (response?.ok) {
    const saved = await cacheSuccessfulResponse(response, authFingerprint);
    debugLog(`live_ok cache_saved=${saved ? 1 : 0}`);
    return response;
  }

  if (
    cache &&
    isStaleUsable(cache) &&
    cacheCanFallbackAfterTransient(cache, authFingerprint) &&
    shouldUseStaleForStatus(response.status)
  ) {
    const backoffMs = await recordBackoff(cache, { status: response.status, authFingerprint });
    debugLog(
      `served_cache=stale live_status=${response.status} age_ms=${cacheAgeMs(cache)} backoff_ms=${backoffMs}`
    );
    return cachedResponse(cache);
  }

  debugLog(
    cache
      ? `live_status=${response?.status || 0} ${cacheLogState(cache)}`
      : `live_status=${response?.status || 0} cache=miss`
  );
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
  scheduleLaunchAuthReconciliation();
  debugLog(
    `initialized cache_ttl_ms=${durationMs(CACHE_TTL_ENV, DEFAULT_CACHE_TTL_MS)} stale_ttl_ms=${staleTtlMs()} backoff_ttl_ms=${backoffTtlMs()}`
  );

  return { name: PLUGIN_NAME };
}
