"use strict";

/**
 * Claude Code Router transformer
 * Name must match what you reference in config: "openai-reasoning-presets"
 *
 * What this does:
 * - Provides local “virtual model names”:
 *     - gpt-5.2-high  -> model=gpt-5.2, reasoning_effort=high
 *     - gpt-5.2-xhigh -> model=gpt-5.2, reasoning_effort=xhigh
 * - Removes/normalizes any accidental Responses-style `reasoning` object
 * - For GPT‑5.x on Chat Completions: converts max_tokens -> max_completion_tokens (and removes max_tokens)
 * - For gpt-4o-mini: clamps max_tokens/max_completion_tokens to 16384 to avoid “too large” errors
 * - For GPT‑5.2 with reasoning_effort != "none": removes temperature/top_p/logprobs (to avoid API errors)
 */
class OpenAIReasoningPresetsTransformer {
  constructor() {
    this.name = "openai-reasoning-presets";
  }

  async transformRequestIn(request /*, provider */) {
    // CCR sometimes passes an object; sometimes a JSON string.
    // We’ll be defensive to avoid syntax/parse crashes.
    let body;
    if (typeof request === "string") {
      try {
        body = JSON.parse(request);
      } catch (e) {
        // If this happens, something upstream is already broken (not valid JSON).
        // Return it unchanged so CCR can surface the real error without crashing here.
        return { body: request };
      }
    } else if (request && typeof request === "object") {
      body = { ...request };
    } else {
      return { body: request };
    }

    // ---- Helpers ----
    const isObject = (v) => v && typeof v === "object" && !Array.isArray(v);
    const clampNumber = (n, min, max) => Math.max(min, Math.min(max, n));

    // ---- 1) Translate/remove Responses-style `reasoning` -> ChatCompletions `reasoning_effort` ----
    // Top-level
    if (isObject(body.reasoning)) {
      if (body.reasoning_effort == null && typeof body.reasoning.effort === "string") {
        body.reasoning_effort = body.reasoning.effort;
      }
      delete body.reasoning;
    }

    // Nested params (some CCR setups merge params later)
    if (isObject(body.params)) {
      if (isObject(body.params.reasoning)) {
        if (body.params.reasoning_effort == null && typeof body.params.reasoning.effort === "string") {
          body.params.reasoning_effort = body.params.reasoning.effort;
        }
        delete body.params.reasoning;
      }

      // If params contains reasoning_effort but top-level doesn’t, copy upward
      if (body.reasoning_effort == null && typeof body.params.reasoning_effort === "string") {
        body.reasoning_effort = body.params.reasoning_effort;
      }
    }

    // Extra safety: ensure `reasoning` is gone
    delete body.reasoning;
    if (isObject(body.params)) delete body.params.reasoning;

    // ---- 2) Apply virtual model presets ----
    const presets = {
      "gpt-5.2-high": { model: "gpt-5.2", reasoning_effort: "high" },
      "gpt-5.2-xhigh": { model: "gpt-5.2", reasoning_effort: "xhigh" },
    };

    const preset = presets[body.model];
    if (preset) {
      body.model = preset.model;
      body.reasoning_effort = preset.reasoning_effort;
    }

    const finalModel = body.model;

    // ---- 3) Token parameter normalization ----
    const isGpt5Family = typeof finalModel === "string" && finalModel.startsWith("gpt-5");
    const is4oMini = finalModel === "gpt-4o-mini";

    if (isGpt5Family) {
      // GPT‑5.x on Chat Completions: use max_completion_tokens, not max_tokens
      if (typeof body.max_tokens === "number" && body.max_completion_tokens == null) {
        body.max_completion_tokens = body.max_tokens;
      }
      delete body.max_tokens;

      if (isObject(body.params)) {
        if (typeof body.params.max_tokens === "number" && body.params.max_completion_tokens == null) {
          body.params.max_completion_tokens = body.params.max_tokens;
        }
        delete body.params.max_tokens;

        if (body.max_completion_tokens == null && typeof body.params.max_completion_tokens === "number") {
          body.max_completion_tokens = body.params.max_completion_tokens;
        }
      }
    } else if (is4oMini) {
      // gpt-4o-mini hard cap observed in your logs: 16384 completion tokens
      const CAP = 16384;

      if (typeof body.max_tokens === "number") body.max_tokens = clampNumber(body.max_tokens, 1, CAP);
      if (typeof body.max_completion_tokens === "number") {
        body.max_completion_tokens = clampNumber(body.max_completion_tokens, 1, CAP);
      }

      if (isObject(body.params)) {
        if (typeof body.params.max_tokens === "number") {
          body.params.max_tokens = clampNumber(body.params.max_tokens, 1, CAP);
        }
        if (typeof body.params.max_completion_tokens === "number") {
          body.params.max_completion_tokens = clampNumber(body.params.max_completion_tokens, 1, CAP);
        }
      }
    }

    // ---- 4) GPT‑5.2 + reasoning effort: strip sampling params that cause errors ----
    const isGpt52 = typeof finalModel === "string" && finalModel.startsWith("gpt-5.2");
    const effort = body.reasoning_effort;

    if (isGpt52 && effort && effort !== "none") {
      delete body.temperature;
      delete body.top_p;
      delete body.logprobs;

      if (isObject(body.params)) {
        delete body.params.temperature;
        delete body.params.top_p;
        delete body.params.logprobs;
      }
    }

    return { body };
  }

  async transformResponseOut(response) {
    return response;
  }
}

module.exports = OpenAIReasoningPresetsTransformer;
