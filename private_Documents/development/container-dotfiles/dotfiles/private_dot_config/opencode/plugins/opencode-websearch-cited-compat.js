// opencode-websearch-cited-compat.js
// Local replacement for opencode-websearch-cited that avoids provider auth hooks.
//
// The npm package exports OpenAI/Google API-key auth plugins. OpenCode chooses
// auth handlers with findLast(), so those later hooks override built-in OAuth
// login. This shim exposes only the websearch_cited tool and reads existing auth
// data read-only; it never registers provider auth methods.

import { readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { tool } from "@opencode-ai/plugin";

const PLUGIN_NAME = "opencode-websearch-cited-compat";
const PLUGIN_VERSION = "1.0.0";

const OPENAI_PROVIDER_ID = "openai";
const GOOGLE_PROVIDER_ID = "google";
const OPENROUTER_PROVIDER_ID = "openrouter";
const ANTHROPIC_PROVIDER_ID = "anthropic";
const ANTHROPIC_API_PROVIDER_ID = "anthropic-api";

const SUPPORTED_PROVIDERS = new Set([
  OPENAI_PROVIDER_ID,
  GOOGLE_PROVIDER_ID,
  OPENROUTER_PROVIDER_ID,
  ANTHROPIC_PROVIDER_ID,
  ANTHROPIC_API_PROVIDER_ID,
]);

const CITED_SEARCH_TOOL_DESCRIPTION =
  "Performs a Gemini-style grounded web search: returns a concise digest with inline citations and a Sources list of URLs. NOTE: for LLM rate limits, DO NOT parallel this tool > 5";

const WEBSEARCH_ARGS = {
  query: tool.schema.string().describe("The natural language web search query."),
};

const WEBSEARCH_ALLOWED_KEYS = new Set(Object.keys(WEBSEARCH_ARGS));
const WEBSEARCH_ALLOWED_KEYS_DESCRIPTION = Array.from(WEBSEARCH_ALLOWED_KEYS)
  .map((key) => `'${key}'`)
  .join(", ");

const SAFE_ERROR_NAME = `${PLUGIN_NAME}.SafeError`;
const REFRESH_BUFFER_MS = 60_000;

const CODEX_ISSUER = "https://auth.openai.com";
const CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann";
const CODEX_RESPONSES_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses";
const OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses";
const OPENROUTER_RESPONSES_ENDPOINT = "https://openrouter.ai/api/v1/responses";
const ANTHROPIC_MESSAGES_ENDPOINT = "https://api.anthropic.com/v1/messages";
const GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta";

const GOOGLE_CODE_ASSIST_GENERATE_PATH = "/v1internal:generateContent";
const GOOGLE_CODE_ASSIST_LOAD_PATH = "/v1internal:loadCodeAssist";
const GOOGLE_CODE_ASSIST_GENERATE_ENDPOINTS = [
  "https://daily-cloudcode-pa.sandbox.googleapis.com",
  "https://autopush-cloudcode-pa.sandbox.googleapis.com",
  "https://cloudcode-pa.googleapis.com",
];
const GOOGLE_CODE_ASSIST_LOAD_ENDPOINTS = [
  "https://cloudcode-pa.googleapis.com",
  "https://daily-cloudcode-pa.sandbox.googleapis.com",
  "https://autopush-cloudcode-pa.sandbox.googleapis.com",
];
const GOOGLE_CODE_ASSIST_DEFAULT_PROJECT_ID = "rising-fact-p41fc";
const GOOGLE_OAUTH_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token";
const GOOGLE_OAUTH_CLIENT_ID_ENV = "OPENCODE_WEBSEARCH_GOOGLE_OAUTH_CLIENT_ID";
const GOOGLE_OAUTH_CLIENT_SECRET_ENV = "OPENCODE_WEBSEARCH_GOOGLE_OAUTH_CLIENT_SECRET";
const GOOGLE_CODE_ASSIST_HEADERS = {
  "User-Agent": "antigravity/1.11.5 windows/amd64",
  "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
  "Client-Metadata":
    '{"ideType":"IDE_UNSPECIFIED","platform":"PLATFORM_UNSPECIFIED","pluginType":"GEMINI"}',
};

const googleTokenCache = new Map();
const googleProjectCache = new Map();

const CODEX_PROMPT = `You are OpenCode, the best coding agent on the planet.

You are an interactive CLI tool that helps users with software engineering tasks. Use the instructions below and the tools available to you to assist the user.

## Editing constraints
- Default to ASCII when editing or creating files. Only introduce non-ASCII or other Unicode characters when there is a clear justification and the file already uses them.
- Only add comments if they are necessary to make a non-obvious block easier to understand.
- Try to use apply_patch for single file edits, but it is fine to explore other options to make the edit if it does not work well. Do not use apply_patch for changes that are auto-generated (i.e. generating package.json or running a lint or format command like gofmt) or when scripting is more efficient (such as search and replacing a string across a codebase).

## Tool usage
- Prefer specialized tools over shell for file operations:
  - Use Read to view files, Edit to modify files, and Write only when needed.
  - Use Glob to find files by name and Grep to search file contents.
- Use Bash for terminal operations (git, bun, builds, tests, running scripts).
- Run tool calls in parallel when neither call needs the other's output; otherwise run sequentially.

## Git and workspace hygiene
- You may be in a dirty git worktree.
    * NEVER revert existing changes you did not make unless explicitly requested, since these changes were made by the user.
    * If asked to make a commit or code edits and there are unrelated changes to your work or changes that you didn't make in those files, don't revert those changes.
    * If the changes are in files you've touched recently, you should read carefully and understand how you can work with the changes rather than reverting them.
    * If the changes are in unrelated files, just ignore them and don't revert them.
- Do not amend commits unless explicitly requested.
- **NEVER** use destructive commands like \`git reset --hard\` or \`git checkout --\` unless specifically requested or approved by the user.

## Frontend tasks
When doing frontend design tasks, avoid collapsing into bland, generic layouts.
Aim for interfaces that feel intentional and deliberate.
- Typography: Use expressive, purposeful fonts and avoid default stacks (Inter, Roboto, Arial, system).
- Color & Look: Choose a clear visual direction; define CSS variables; avoid purple-on-white defaults. No purple bias or dark mode bias.
- Motion: Use a few meaningful animations (page-load, staggered reveals) instead of generic micro-motions.
- Background: Don't rely on flat, single-color backgrounds; use gradients, shapes, or subtle patterns to build atmosphere.
- Overall: Avoid boilerplate layouts and interchangeable UI patterns. Vary themes, type families, and visual languages across outputs.
- Ensure the page loads properly on both desktop and mobile.

Exception: If working within an existing website or design system, preserve the established patterns, structure, and visual language.

## Presenting your work and final message

You are producing plain text that will later be styled by the CLI. Follow these rules exactly. Formatting should make results easy to scan, but not feel mechanical. Use judgment to decide how much structure adds value.

- Default: be very concise; friendly coding teammate tone.
- Default: do the work without asking questions. Treat short tasks as sufficient direction; infer missing details by reading the codebase and following existing conventions.
- Questions: only ask when you are truly blocked after checking relevant context AND you cannot safely pick a reasonable default. This usually means one of:
  * The request is ambiguous in a way that materially changes the result and you cannot disambiguate by reading the repo.
  * The action is destructive/irreversible, touches production, or changes billing/security posture.
  * You need a secret/credential/value that cannot be inferred (API key, account id, etc.).
- If you must ask: do all non-blocked work first, then ask exactly one targeted question, include your recommended default, and state what would change based on the answer.
- Never ask permission questions like "Should I proceed?" or "Do you want me to run tests?"; proceed with the most reasonable option and mention what you did.
- For substantial work, summarize clearly; follow final-answer formatting.
- Skip heavy formatting for simple confirmations.
- Don't dump large files you've written; reference paths only.
- No "save/copy this file" - User is on the same machine.
- Offer logical next steps (tests, commits, build) briefly; add verify steps if you couldn't do something.
- For code changes:
  * Lead with a quick explanation of the change, and then give more details on the context covering where and why a change was made. Do not start this explanation with "summary", just jump right in.
  * If there are natural next steps the user may want to take, suggest them at the end of your response. Do not make suggestions if there are no natural next steps.
  * When suggesting multiple options, use numeric lists for the suggestions so the user can quickly respond with a single number.
- The user does not command execution outputs. When asked to show the output of a command (e.g. \`git show\`), relay the important details in your answer or summarize the key lines so the user understands the result.

## Final answer structure and style guidelines

- Plain text; CLI handles styling. Use structure only when it helps scannability.
- Headers: optional; short Title Case (1-3 words) wrapped in **...**; no blank line before the first bullet; add only if they truly help.
- Bullets: use - ; merge related points; keep to one line when possible; 4-6 per list ordered by importance; keep phrasing consistent.
- Monospace: backticks for commands/env vars/code ids/code paths; use for literal keyword bullets; never combine with **.
- Code samples or multi-line snippets should be wrapped in fenced code blocks; include an info string as often as possible.
- Structure: group related bullets; order sections general -> specific -> supporting; for subsections, start with a bolded keyword bullet, then items; match complexity to the task.
- Tone: collaborative, concise, factual; present tense, active voice; self-contained; no "above/below"; parallel wording.
- Don'ts: no nested bullets/hierarchies; no ANSI codes; don't cram unrelated keywords; keep keyword lists short-wrap/reformat if long; avoid naming formatting styles in answers.
- Adaptation: code explanations -> precise, structured with code refs; simple tasks -> lead with outcome; big changes -> logical walkthrough + rationale + next actions; casual one-offs -> plain sentences, no headers/bullets.
- File References: When referencing files in your response follow the below rules:
  * Use inline code to make file paths clickable.
  * Each reference should have a stand alone path. Even if it's the same file.
  * Accepted: absolute, workspace-relative, a/ or b/ diff prefixes, or bare filename/suffix.
  * Optionally include line/column (1-based): :line[:column] or #Lline[Ccolumn] (column defaults to 1).
  * Do not use URIs like file://, vscode://, or https://.
  * Do not provide range of lines
  * Examples: src/app.ts, src/app.ts:42, b/server/index.js#L10, C:\repo\project\main.rs:12:5`;

function safeError(message) {
  const error = new Error(message);
  error.name = SAFE_ERROR_NAME;
  return error;
}

function isSafeError(error) {
  return Boolean(error && typeof error === "object" && error.name === SAFE_ERROR_NAME);
}

function isAbortError(error) {
  return Boolean(error && typeof error === "object" && error.name === "AbortError");
}

function httpError(providerID, status) {
  return safeError(`websearch_cited ${providerID} request failed with HTTP ${status}.`);
}

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function normalizedString(value) {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

function canonicalProviderID(providerID) {
  if (providerID === ANTHROPIC_API_PROVIDER_ID) return ANTHROPIC_PROVIDER_ID;
  if (SUPPORTED_PROVIDERS.has(providerID)) return providerID;
  return undefined;
}

function findFirstWebsearchCitedConfig(config) {
  const providers = config?.provider;
  if (!isRecord(providers)) return {};

  let firstError;

  for (const [providerID, providerConfig] of Object.entries(providers)) {
    if (!isRecord(providerConfig)) continue;

    const options = providerConfig.options;
    if (!isRecord(options) || !("websearch_cited" in options)) continue;

    const cited = options.websearch_cited;
    if (!isRecord(cited)) {
      firstError ??= `Invalid websearch_cited configuration for provider "${providerID}".`;
      continue;
    }

    const model = normalizedString(cited.model);
    if (!model) {
      firstError ??= `Missing websearch_cited model for provider "${providerID}".`;
      continue;
    }

    const canonicalID = canonicalProviderID(providerID);
    if (!canonicalID) {
      firstError ??= `Unsupported provider "${providerID}" for websearch_cited.`;
      continue;
    }

    return {
      selected: {
        providerID,
        canonicalID,
        model,
        providerConfig,
        providerOptions: options,
        websearchOptions: cited,
      },
    };
  }

  return firstError ? { error: firstError } : {};
}

function parseOpenAIOptions(providerConfig, model) {
  if (!isRecord(providerConfig)) return {};

  const rawOptions = providerConfig.options;
  const baseOptions = isRecord(rawOptions) ? rawOptions : undefined;
  let modelOptions;

  if (model && isRecord(providerConfig.models)) {
    const entry = providerConfig.models[model];
    if (isRecord(entry) && isRecord(entry.options)) {
      modelOptions = entry.options;
    }
  }

  const merged = {
    ...(baseOptions ?? {}),
    ...(modelOptions ?? {}),
  };

  const result = {};
  const reasoningEffort = normalizedString(merged.reasoningEffort);
  const reasoningSummary = normalizedString(merged.reasoningSummary);
  const textVerbosity = normalizedString(merged.textVerbosity);

  if (reasoningEffort) result.reasoningEffort = reasoningEffort;
  if (reasoningSummary) result.reasoningSummary = reasoningSummary;
  if (textVerbosity) result.textVerbosity = textVerbosity;
  if (typeof merged.store === "boolean") result.store = merged.store;
  if (Array.isArray(merged.include)) {
    const include = merged.include.filter((value) => normalizedString(value));
    if (include.length > 0) result.include = include;
  }

  return result;
}

function xdgDataHome() {
  return process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share");
}

function authFilePath() {
  return path.join(xdgDataHome(), "opencode", "auth.json");
}

async function readAuthMap() {
  const content = process.env.OPENCODE_AUTH_CONTENT;
  if (content) {
    try {
      const parsed = JSON.parse(content);
      if (isRecord(parsed)) return parsed;
    } catch {}
  }

  try {
    const raw = await readFile(authFilePath(), "utf8");
    const parsed = JSON.parse(raw);
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function isAuthInfo(value) {
  if (!isRecord(value)) return false;
  if (value.type === "oauth") return typeof value.access === "string" || typeof value.refresh === "string";
  if (value.type === "api") return typeof value.key === "string";
  if (value.type === "wellknown") return typeof value.key === "string" || typeof value.token === "string";
  return false;
}

async function readStoredAuth(providerIDs) {
  const authMap = await readAuthMap();
  for (const providerID of providerIDs) {
    const auth = authMap[providerID];
    if (isAuthInfo(auth)) return auth;
  }
  return undefined;
}

function resolveSecretRef(value) {
  if (typeof value === "string") {
    const trimmed = value.trim();
    const envMatch = trimmed.match(/^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$/);
    if (envMatch) return normalizedString(process.env[envMatch[1]]);
    return trimmed === "" ? undefined : trimmed;
  }

  if (isRecord(value) && typeof value.env === "string") {
    return normalizedString(process.env[value.env]);
  }

  return undefined;
}

function fallbackEnvNames(selection) {
  if (selection.canonicalID === OPENAI_PROVIDER_ID) return ["OPENAI_API_KEY"];
  if (selection.canonicalID === GOOGLE_PROVIDER_ID) {
    return ["GOOGLE_GENERATIVE_AI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"];
  }
  if (selection.canonicalID === OPENROUTER_PROVIDER_ID) return ["OPENROUTER_API_KEY"];
  if (selection.canonicalID === ANTHROPIC_PROVIDER_ID) return ["ANTHROPIC_API_KEY"];
  return [];
}

function resolveApiKey(selection) {
  const optionKey = resolveSecretRef(selection.providerOptions?.apiKey);
  if (optionKey) return optionKey;

  for (const envName of fallbackEnvNames(selection)) {
    const envKey = normalizedString(process.env[envName]);
    if (envKey) return envKey;
  }

  return undefined;
}

async function resolveAuth(selection) {
  const providerIDs = Array.from(new Set([selection.providerID, selection.canonicalID]));
  const stored = await readStoredAuth(providerIDs);
  if (stored) return stored;

  const apiKey = resolveApiKey(selection);
  if (apiKey) return { type: "api", key: apiKey };

  return undefined;
}

function missingAuthMessage(selection) {
  if (selection.providerID === ANTHROPIC_API_PROVIDER_ID) {
    return 'Missing auth for provider "anthropic-api". Set ANTHROPIC_API_KEY or provider.options.apiKey.';
  }
  return `Missing auth for provider "${selection.providerID}". Authenticate via opencode auth login or configure an API key.`;
}

function buildWebSearchUserPrompt(query) {
  const normalized = query.trim();
  return `perform web search on "${normalized}". Return results with inline citations (**only** source index like [1], no URL in the answer) and end with a Sources list of URLs.`;
}

function parseJwtClaims(token) {
  const parts = token.split(".");
  if (parts.length !== 3) return undefined;

  try {
    return JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
  } catch {
    return undefined;
  }
}

function extractOpenAIAccountIdFromClaims(claims) {
  if (!isRecord(claims)) return undefined;
  const direct = normalizedString(claims.chatgpt_account_id);
  if (direct) return direct;

  const authClaim = claims["https://api.openai.com/auth"];
  if (isRecord(authClaim)) {
    const accountID = normalizedString(authClaim.chatgpt_account_id);
    if (accountID) return accountID;
  }

  if (Array.isArray(claims.organizations)) {
    for (const org of claims.organizations) {
      if (isRecord(org)) {
        const orgID = normalizedString(org.id);
        if (orgID) return orgID;
      }
    }
  }

  return undefined;
}

function extractOpenAIAccountId(auth, tokenPayload) {
  if (auth.type !== "oauth") return undefined;
  const explicit = normalizedString(auth.accountId);
  if (explicit) return explicit;

  if (isRecord(tokenPayload) && typeof tokenPayload.id_token === "string") {
    const claims = parseJwtClaims(tokenPayload.id_token);
    const accountID = extractOpenAIAccountIdFromClaims(claims);
    if (accountID) return accountID;
  }

  const access = normalizedString(tokenPayload?.access_token) || normalizedString(auth.access);
  if (!access) return undefined;
  return extractOpenAIAccountIdFromClaims(parseJwtClaims(access));
}

async function refreshOpenAIAccessToken(refreshToken) {
  const response = await fetch(`${CODEX_ISSUER}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: refreshToken,
      client_id: CODEX_CLIENT_ID,
    }).toString(),
  });

  if (!response.ok) throw httpError(OPENAI_PROVIDER_ID, response.status);

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw safeError("OpenAI OAuth token refresh returned invalid JSON.");
  }

  const access = normalizedString(payload?.access_token);
  if (!access) throw safeError("OpenAI OAuth token refresh did not return an access token.");

  return payload;
}

async function resolveOpenAIAuth(auth) {
  if (auth.type !== "oauth") return auth;

  const access = normalizedString(auth.access);
  const expires = typeof auth.expires === "number" ? auth.expires : undefined;
  if (access && (!expires || expires > Date.now() + REFRESH_BUFFER_MS)) return auth;

  const refresh = normalizedString(auth.refresh);
  if (!refresh) throw safeError("Missing OpenAI OAuth refresh token.");

  const tokens = await refreshOpenAIAccessToken(refresh);
  return {
    type: "oauth",
    refresh: normalizedString(tokens.refresh_token) || refresh,
    access: normalizedString(tokens.access_token) || access || "",
    expires: Date.now() + (typeof tokens.expires_in === "number" ? tokens.expires_in : 3600) * 1000,
    accountId: extractOpenAIAccountId(auth, tokens),
  };
}

async function getOpenAIAuthMaterial(auth) {
  const effective = await resolveOpenAIAuth(auth);

  if (effective.type === "oauth") {
    const token = normalizedString(effective.access);
    if (!token) throw safeError("Missing OpenAI OAuth access token.");
    return {
      token,
      isOAuth: true,
      accountID: extractOpenAIAccountId(effective, { access_token: token }),
    };
  }

  if (effective.type === "api") {
    const token = normalizedString(effective.key);
    if (!token) throw safeError("Missing OpenAI API key.");
    return { token, isOAuth: false };
  }

  const token = normalizedString(effective.token) || normalizedString(effective.key);
  if (!token) throw safeError("Missing OpenAI token.");
  return { token, isOAuth: false };
}

async function runOpenAIWebSearch(selection, auth, query, abortSignal) {
  const model = selection.model.trim();
  const normalizedQuery = query.trim();
  const openaiConfig = parseOpenAIOptions(selection.providerConfig, model);
  const authMaterial = await getOpenAIAuthMaterial(auth);

  const body = {
    model,
    instructions: authMaterial.isOAuth
      ? CODEX_PROMPT
      : "You are an AI assistant answering a single web search query for the user.",
    input: [
      {
        role: "user",
        content: [
          {
            type: "input_text",
            text: buildWebSearchUserPrompt(normalizedQuery),
          },
        ],
      },
    ],
    tools: [{ type: "web_search" }],
    include: ["web_search_call.action.sources"],
    store: false,
    stream: true,
    tool_choice: "auto",
    parallel_tool_calls: true,
  };

  if (openaiConfig.reasoningEffort || openaiConfig.reasoningSummary) {
    body.reasoning = {
      effort: openaiConfig.reasoningEffort,
      summary: openaiConfig.reasoningSummary,
    };
  }

  if (openaiConfig.textVerbosity) {
    body.text = { verbosity: openaiConfig.textVerbosity };
  }

  if (Array.isArray(openaiConfig.include) && openaiConfig.include.length > 0) {
    body.include = openaiConfig.include;
  }

  const headers = {
    Authorization: `Bearer ${authMaterial.token}`,
    "Content-Type": "application/json",
    "OpenAI-Beta": "responses=experimental",
  };

  if (authMaterial.isOAuth) {
    if (authMaterial.accountID) headers["chatgpt-account-id"] = authMaterial.accountID;
    headers.originator = "codex_cli_rs";
  }

  const response = await fetch(authMaterial.isOAuth ? CODEX_RESPONSES_ENDPOINT : OPENAI_RESPONSES_ENDPOINT, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: abortSignal,
  });

  if (!response.ok) throw httpError(OPENAI_PROVIDER_ID, response.status);

  const payload = await readOpenAIResponsePayload(response);
  const text = extractOpenAIText(payload);

  if (!text || !text.trim()) {
    return `Web search completed for "${normalizedQuery}", but no results were returned.`;
  }

  return text;
}

async function readOpenAIResponsePayload(response) {
  const text = await response.text();
  const trimmed = text.trim();
  if (trimmed === "") return {};

  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.parse(trimmed);
    } catch {}
  }

  const extracted = extractOpenAIResponseFromSse(text);
  if (extracted !== undefined) return extracted;

  throw safeError("Failed to parse OpenAI response JSON.");
}

function extractOpenAIResponseFromSse(sseText) {
  const lines = sseText.split("\n");

  for (const line of lines) {
    if (!line.startsWith("data: ")) continue;
    const payload = line.slice(6).trim();
    if (!payload || payload === "[DONE]") continue;

    try {
      const parsed = JSON.parse(payload);
      const kind = parsed.type ?? "";
      if (kind === "response.done" || kind === "response.completed") return parsed.response;
    } catch {}
  }

  return undefined;
}

function extractOpenAIText(payload) {
  if (!isRecord(payload) || !Array.isArray(payload.output)) return undefined;

  let combined = "";
  for (const item of payload.output) {
    if (!isRecord(item) || !Array.isArray(item.content)) continue;

    for (const part of item.content) {
      if (!isRecord(part) || part.type !== "output_text") continue;
      if (typeof part.text === "string") {
        combined += part.text;
      } else if (isRecord(part.text) && typeof part.text.value === "string") {
        combined += part.text.value;
      }
    }
  }

  return combined || undefined;
}

function getApiToken(auth, providerID) {
  if (auth.type === "api") {
    const key = normalizedString(auth.key);
    if (!key) throw safeError(`Missing ${providerID} API key.`);
    return key;
  }

  if (auth.type === "oauth") {
    const access = normalizedString(auth.access);
    if (!access) throw safeError(`Missing ${providerID} OAuth access token.`);
    return access;
  }

  const token = normalizedString(auth.token) || normalizedString(auth.key);
  if (!token) throw safeError(`Missing ${providerID} token.`);
  return token;
}

async function runAnthropicWebSearch(selection, auth, query, abortSignal) {
  const model = selection.model.trim();
  const normalizedQuery = query.trim();
  const token = getApiToken(auth, ANTHROPIC_PROVIDER_ID);
  const isOAuth = auth.type === "oauth";

  const body = {
    model,
    max_tokens: 4096,
    messages: [
      {
        role: "user",
        content: normalizedQuery,
      },
    ],
    tools: [
      {
        type: "web_search_20260209",
        name: "web_search",
      },
    ],
  };

  const headers = {
    "content-type": "application/json",
    "anthropic-version": "2023-06-01",
  };

  if (isOAuth) {
    headers.authorization = `Bearer ${token}`;
  } else {
    headers["x-api-key"] = token;
  }

  const response = await fetch(ANTHROPIC_MESSAGES_ENDPOINT, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: abortSignal,
  });

  if (!response.ok) throw httpError(ANTHROPIC_PROVIDER_ID, response.status);

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw safeError("Failed to parse Anthropic response JSON.");
  }

  return formatAnthropicResponse(payload, normalizedQuery);
}

function formatAnthropicResponse(response, query) {
  const content = response?.content;
  if (!Array.isArray(content) || content.length === 0) {
    return `No search results or information found for query: "${query}"`;
  }

  const sources = new Map();
  const urlToIndex = new Map();
  let sourceIndex = 0;
  let combined = "";

  for (const block of content) {
    if (!isRecord(block) || block.type !== "text") continue;

    const text = typeof block.text === "string" ? block.text : "";
    const citations = Array.isArray(block.citations) ? block.citations : [];

    if (citations.length === 0) {
      combined += text;
      continue;
    }

    let citationMarkers = "";
    for (const citation of citations) {
      if (!isRecord(citation)) continue;
      const url = normalizedString(citation.url);
      if (!url) continue;

      let idx = urlToIndex.get(url);
      if (idx === undefined) {
        sourceIndex += 1;
        idx = sourceIndex;
        urlToIndex.set(url, idx);
        sources.set(String(idx), {
          url,
          title: normalizedString(citation.title) || "Untitled",
        });
      }

      citationMarkers += `[${idx}]`;
    }

    combined += text + citationMarkers;
  }

  if (!combined.trim()) return `No search results or information found for query: "${query}"`;

  if (sources.size > 0) {
    const sourceLines = Array.from(sources.entries()).map(
      ([idx, source]) => `[${idx}] ${source.title} (${source.url})`
    );
    combined += `\n\nSources:\n${sourceLines.join("\n")}`;
  }

  return combined;
}

async function runOpenRouterWebSearch(selection, auth, query, abortSignal) {
  if (auth.type !== "api") throw safeError("OpenRouter web search requires an API key.");

  const model = selection.model.trim();
  const normalizedQuery = query.trim();
  const apiKey = getApiToken(auth, OPENROUTER_PROVIDER_ID);
  const prompt = buildWebSearchUserPrompt(normalizedQuery);

  const body = {
    model,
    input: prompt,
    plugins: [
      {
        id: "web",
        search_prompt: prompt,
      },
    ],
    store: false,
    stream: false,
  };

  const response = await fetch(OPENROUTER_RESPONSES_ENDPOINT, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: abortSignal,
  });

  if (!response.ok) throw httpError(OPENROUTER_PROVIDER_ID, response.status);

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw safeError("Failed to parse OpenRouter response JSON.");
  }

  const outputText = extractOpenRouterText(payload);
  if (!outputText || !outputText.trim()) {
    return `Web search completed for "${normalizedQuery}", but no results were returned.`;
  }

  return outputText;
}

function extractOpenRouterText(payload) {
  if (!isRecord(payload)) return undefined;
  if (typeof payload.output_text === "string" && payload.output_text.trim() !== "") return payload.output_text;
  if (!Array.isArray(payload.output)) return undefined;

  let combined = "";
  for (const item of payload.output) {
    if (!isRecord(item) || item.type !== "message" || !Array.isArray(item.content)) continue;

    for (const part of item.content) {
      if (isRecord(part) && part.type === "output_text" && typeof part.text === "string") {
        combined += part.text;
      }
    }
  }

  return combined || undefined;
}

function buildGeminiUrl(model) {
  return `${GEMINI_API_BASE}/models/${encodeURIComponent(model)}:generateContent`;
}

async function runGoogleWebSearch(selection, auth, query, abortSignal) {
  if (auth.type === "api") {
    return runGoogleApiKeyWebSearch(selection, auth, query, abortSignal);
  }

  if (auth.type === "oauth") {
    return runGoogleOAuthWebSearch(selection, auth, query, abortSignal);
  }

  throw safeError("Unsupported auth type for Google web search.");
}

async function runGoogleApiKeyWebSearch(selection, auth, query, abortSignal) {
  const model = selection.model.trim();
  const normalizedQuery = query.trim();
  const apiKey = getApiToken(auth, GOOGLE_PROVIDER_ID);

  const response = await fetch(buildGeminiUrl(model), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-goog-api-key": apiKey,
      ...GOOGLE_CODE_ASSIST_HEADERS,
    },
    body: JSON.stringify({
      contents: [
        {
          role: "user",
          parts: [{ text: normalizedQuery }],
        },
      ],
      tools: [{ googleSearch: {} }],
    }),
    signal: abortSignal,
  });

  if (!response.ok) throw httpError(GOOGLE_PROVIDER_ID, response.status);

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw safeError("Failed to parse Google response JSON.");
  }

  return formatGeminiWebSearchResponse(payload, normalizedQuery);
}

function parseGoogleRefresh(refresh) {
  const normalized = normalizedString(refresh);
  if (!normalized) return { refreshToken: "" };
  const [token, project, managed] = normalized.split("|");
  return {
    refreshToken: normalizedString(token) || "",
    projectID: normalizedString(project),
    managedProjectID: normalizedString(managed),
  };
}

function googleAccessTokenExpired(accessToken, expiresAt) {
  if (!accessToken || typeof expiresAt !== "number") return true;
  return expiresAt <= Date.now() + REFRESH_BUFFER_MS;
}

function getCachedGoogleAccess(refreshToken) {
  const cached = googleTokenCache.get(refreshToken);
  if (!cached) return undefined;
  if (cached.expiresAt <= Date.now() + REFRESH_BUFFER_MS) {
    googleTokenCache.delete(refreshToken);
    return undefined;
  }
  return cached;
}

function cacheGoogleToken(refreshToken, accessToken, expiresAt) {
  if (!refreshToken || !accessToken || typeof expiresAt !== "number") return;
  googleTokenCache.set(refreshToken, { accessToken, expiresAt });
}

function googleOAuthClientCredentials() {
  const clientID = normalizedString(process.env[GOOGLE_OAUTH_CLIENT_ID_ENV]);
  const clientSecret = normalizedString(process.env[GOOGLE_OAUTH_CLIENT_SECRET_ENV]);
  if (!clientID || !clientSecret) return undefined;
  return { clientID, clientSecret };
}

async function refreshGoogleAccessToken(refreshToken) {
  const credentials = googleOAuthClientCredentials();
  if (!credentials) {
    throw safeError(
      `Google OAuth token refresh requires ${GOOGLE_OAUTH_CLIENT_ID_ENV} and ${GOOGLE_OAUTH_CLIENT_SECRET_ENV}.`
    );
  }

  const requestTime = Date.now();
  const response = await fetch(GOOGLE_OAUTH_TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: refreshToken,
      client_id: credentials.clientID,
      client_secret: credentials.clientSecret,
    }).toString(),
  });

  if (!response.ok) throw httpError(GOOGLE_PROVIDER_ID, response.status);

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw safeError("Google OAuth token refresh returned invalid JSON.");
  }

  const accessToken = normalizedString(payload?.access_token);
  if (!accessToken) throw safeError("Google OAuth token refresh did not return an access token.");

  const expiresIn = typeof payload.expires_in === "number" && Number.isFinite(payload.expires_in)
    ? payload.expires_in
    : 3600;
  const expiresAt = requestTime + Math.max(expiresIn, 0) * 1000;
  cacheGoogleToken(refreshToken, accessToken, expiresAt);

  return { accessToken, expiresAt };
}

function buildGoogleMetadata(projectID) {
  const metadata = {
    ideType: "IDE_UNSPECIFIED",
    platform: "PLATFORM_UNSPECIFIED",
    pluginType: "GEMINI",
  };
  if (projectID) metadata.duetProject = projectID;
  return metadata;
}

async function loadGoogleManagedProject(accessToken, projectID, abortSignal) {
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${accessToken}`,
    "User-Agent": "google-api-nodejs-client/9.15.1",
    "X-Goog-Api-Client": GOOGLE_CODE_ASSIST_HEADERS["X-Goog-Api-Client"],
    "Client-Metadata": GOOGLE_CODE_ASSIST_HEADERS["Client-Metadata"],
  };
  const body = JSON.stringify({ metadata: buildGoogleMetadata(projectID) });
  const endpoints = Array.from(new Set([...GOOGLE_CODE_ASSIST_LOAD_ENDPOINTS, ...GOOGLE_CODE_ASSIST_GENERATE_ENDPOINTS]));

  for (const baseEndpoint of endpoints) {
    try {
      const response = await fetch(`${baseEndpoint}${GOOGLE_CODE_ASSIST_LOAD_PATH}`, {
        method: "POST",
        headers,
        body,
        signal: abortSignal,
      });
      if (!response.ok) continue;
      return await response.json();
    } catch {}
  }

  return undefined;
}

function extractGoogleManagedProjectID(payload) {
  if (!isRecord(payload)) return undefined;
  const project = payload.cloudaicompanionProject;
  if (typeof project === "string") return normalizedString(project);
  if (isRecord(project)) return normalizedString(project.id);
  return undefined;
}

async function resolveGoogleProjectID(accessToken, refreshToken, refreshParts, abortSignal) {
  if (refreshParts.managedProjectID) return refreshParts.managedProjectID;
  const cached = googleProjectCache.get(refreshToken);
  if (cached) return cached;

  const fallback = GOOGLE_CODE_ASSIST_DEFAULT_PROJECT_ID;
  const desired = refreshParts.projectID || fallback;
  const loadPayload = await loadGoogleManagedProject(accessToken, desired, abortSignal);
  const managed = extractGoogleManagedProjectID(loadPayload);

  if (managed) {
    googleProjectCache.set(refreshToken, managed);
    return managed;
  }

  return refreshParts.projectID || fallback;
}

async function requestGoogleCodeAssistGenerate(accessToken, projectID, model, query, abortSignal) {
  const requestPayload = {
    contents: [
      {
        role: "user",
        parts: [{ text: query }],
      },
    ],
    tools: [{ googleSearch: {} }],
  };

  const body = JSON.stringify({
    project: projectID,
    model,
    request: requestPayload,
    requestType: "agent",
    userAgent: "antigravity",
    requestId: `agent-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`,
  });

  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${accessToken}`,
    ...GOOGLE_CODE_ASSIST_HEADERS,
  };

  let lastStatus;
  for (const baseEndpoint of GOOGLE_CODE_ASSIST_GENERATE_ENDPOINTS) {
    const response = await fetch(`${baseEndpoint}${GOOGLE_CODE_ASSIST_GENERATE_PATH}`, {
      method: "POST",
      headers,
      body,
      signal: abortSignal,
    });

    if (!response.ok) {
      lastStatus = response.status;
      if (response.status === 401 || response.status === 403) return { ok: false, status: response.status };
      continue;
    }

    const text = await response.text();
    if (!text) throw safeError("Empty response from Google Code Assist.");

    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw safeError("Invalid JSON response from Google Code Assist.");
    }

    const responseBody = extractGoogleGenerateContentResponse(parsed);
    if (!responseBody) throw safeError("Google Code Assist response did not include a response payload.");
    return { ok: true, body: responseBody };
  }

  return { ok: false, status: lastStatus || 502 };
}

async function runGoogleOAuthWebSearch(selection, auth, query, abortSignal) {
  const model = selection.model.trim();
  const normalizedQuery = query.trim();
  const refreshParts = parseGoogleRefresh(auth.refresh);
  const refreshToken = refreshParts.refreshToken;
  if (!refreshToken) throw safeError("Missing Google OAuth refresh token.");

  const cached = getCachedGoogleAccess(refreshToken);
  let accessToken = cached?.accessToken || normalizedString(auth.access);
  let expiresAt = cached?.expiresAt || (typeof auth.expires === "number" ? auth.expires : undefined);
  let refreshedThisRequest = false;

  if (googleAccessTokenExpired(accessToken, expiresAt)) {
    const refreshed = await refreshGoogleAccessToken(refreshToken);
    accessToken = refreshed.accessToken;
    expiresAt = refreshed.expiresAt;
    refreshedThisRequest = true;
  }

  if (!accessToken) throw safeError("Missing Google OAuth access token.");
  cacheGoogleToken(refreshToken, accessToken, expiresAt);

  const projectID = await resolveGoogleProjectID(accessToken, refreshToken, refreshParts, abortSignal);
  const firstAttempt = await requestGoogleCodeAssistGenerate(
    accessToken,
    projectID,
    model,
    normalizedQuery,
    abortSignal
  );

  if (firstAttempt.ok) return formatGeminiWebSearchResponse(firstAttempt.body, normalizedQuery);

  const canRefresh = Boolean(googleOAuthClientCredentials());
  const shouldRetry = (firstAttempt.status === 401 || firstAttempt.status === 403) && !refreshedThisRequest && canRefresh;
  if (!shouldRetry) throw httpError(GOOGLE_PROVIDER_ID, firstAttempt.status);

  googleTokenCache.delete(refreshToken);
  const refreshed = await refreshGoogleAccessToken(refreshToken);
  const retry = await requestGoogleCodeAssistGenerate(
    refreshed.accessToken,
    projectID,
    model,
    normalizedQuery,
    abortSignal
  );

  if (retry.ok) return formatGeminiWebSearchResponse(retry.body, normalizedQuery);
  throw httpError(GOOGLE_PROVIDER_ID, retry.status);
}

function extractGoogleGenerateContentResponse(payload) {
  const candidateObject = (() => {
    if (Array.isArray(payload)) {
      return payload.find((item) => isRecord(item));
    }
    return isRecord(payload) ? payload : undefined;
  })();

  if (!candidateObject) return undefined;
  if (isRecord(candidateObject.response)) return candidateObject.response;
  if (candidateObject.candidates) return candidateObject;
  return undefined;
}

function formatGeminiWebSearchResponse(response, query) {
  const responseText = extractGeminiResponseText(response);
  if (!responseText || !responseText.trim()) {
    return `No search results or information found for query: "${query}"`;
  }

  const metadata = response?.candidates?.[0]?.groundingMetadata;
  const sources = metadata?.groundingChunks;
  let modifiedText = responseText;

  if (Array.isArray(sources) && sources.length > 0 && isRecord(metadata)) {
    const insertions = buildGeminiCitationInsertions(metadata);
    if (insertions.length > 0) modifiedText = insertMarkersByUtf8Index(modifiedText, insertions);

    const sourceLines = sources.map((source, index) => {
      const title = normalizedString(source?.web?.title) || "Untitled";
      const uri = normalizedString(source?.web?.uri) || "No URI";
      return `[${index + 1}] ${title} (${uri})`;
    });
    modifiedText += `\n\nSources:\n${sourceLines.join("\n")}`;
  }

  return modifiedText;
}

function extractGeminiResponseText(response) {
  const parts = response?.candidates?.[0]?.content?.parts;
  if (!Array.isArray(parts) || parts.length === 0) return undefined;

  let combined = "";
  for (const part of parts) {
    if (part?.thought) continue;
    if (typeof part?.text === "string") combined += part.text;
  }

  return combined || undefined;
}

function buildGeminiCitationInsertions(metadata) {
  const supports = metadata.groundingSupports;
  if (!Array.isArray(supports) || supports.length === 0) return [];

  const insertions = [];
  for (const support of supports) {
    const endIndex = support?.segment?.endIndex;
    const indices = support?.groundingChunkIndices;
    if (typeof endIndex !== "number" || !Array.isArray(indices) || indices.length === 0) continue;

    const marker = Array.from(new Set(indices))
      .sort((a, b) => a - b)
      .map((idx) => `[${idx + 1}]`)
      .join("");
    insertions.push({ index: endIndex, marker });
  }

  insertions.sort((a, b) => b.index - a.index);
  return insertions;
}

function insertMarkersByUtf8Index(text, insertions) {
  if (insertions.length === 0) return text;

  const encoder = new TextEncoder();
  const responseBytes = encoder.encode(text);
  const parts = [];
  let lastIndex = responseBytes.length;

  for (const insertion of insertions) {
    const position = Math.min(insertion.index, lastIndex);
    parts.unshift(responseBytes.subarray(position, lastIndex));
    parts.unshift(encoder.encode(insertion.marker));
    lastIndex = position;
  }

  parts.unshift(responseBytes.subarray(0, lastIndex));

  const totalLength = parts.reduce((sum, part) => sum + part.length, 0);
  const finalBytes = new Uint8Array(totalLength);
  let offset = 0;
  for (const part of parts) {
    finalBytes.set(part, offset);
    offset += part.length;
  }

  return new TextDecoder().decode(finalBytes);
}

async function runSelectedProvider(selection, auth, query, abortSignal) {
  if (selection.canonicalID === OPENAI_PROVIDER_ID) {
    return runOpenAIWebSearch(selection, auth, query, abortSignal);
  }
  if (selection.canonicalID === GOOGLE_PROVIDER_ID) {
    return runGoogleWebSearch(selection, auth, query, abortSignal);
  }
  if (selection.canonicalID === OPENROUTER_PROVIDER_ID) {
    return runOpenRouterWebSearch(selection, auth, query, abortSignal);
  }
  if (selection.canonicalID === ANTHROPIC_PROVIDER_ID) {
    return runAnthropicWebSearch(selection, auth, query, abortSignal);
  }

  throw safeError(`Unsupported provider "${selection.providerID}" for websearch_cited.`);
}

export default async function WebsearchCitedCompatPlugin() {
  let selectedProvider;
  let configError;

  return {
    async config(config) {
      const { selected, error } = findFirstWebsearchCitedConfig(config);
      selectedProvider = selected;
      configError = error;
    },
    tool: {
      websearch_cited: tool({
        description: CITED_SEARCH_TOOL_DESCRIPTION,
        args: WEBSEARCH_ARGS,
        async execute(args, context) {
          const argKeys = Object.keys(args ?? {});
          const extraKeys = argKeys.filter((key) => !WEBSEARCH_ALLOWED_KEYS.has(key));
          if (extraKeys.length > 0) {
            throw safeError(
              `Unknown argument(s): ${extraKeys.join(", ")}, only ${WEBSEARCH_ALLOWED_KEYS_DESCRIPTION} supported.`
            );
          }

          const query = args?.query?.trim();
          if (!query) throw safeError("The 'query' parameter cannot be empty.");
          if (configError) throw safeError(configError);
          if (!selectedProvider) throw safeError("Missing web search model configuration.");

          try {
            const auth = await resolveAuth(selectedProvider);
            if (!auth) throw safeError(missingAuthMessage(selectedProvider));
            return await runSelectedProvider(selectedProvider, auth, query, context?.abort);
          } catch (error) {
            if (isSafeError(error)) throw error;
            if (isAbortError(error)) throw safeError("websearch_cited request was aborted.");
            throw safeError(`websearch_cited failed for provider "${selectedProvider.providerID}".`);
          }
        },
      }),
    },
    name: `${PLUGIN_NAME}@${PLUGIN_VERSION}`,
  };
}
