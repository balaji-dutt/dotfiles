class PerplexityWebSearchFix {
  name = "perplexity-websearch-fix";

  async transformRequestIn(request, provider) {
    const pname = (provider?.name || "").toLowerCase();
    if (pname !== "perplexity") return request;
    if (!request || typeof request !== "object") return request;

    // Perplexity rejects CCR/Claude tool schemas -> don't send tools
    delete request.tools;
    delete request.tool_choice;
    delete request.parallel_tool_calls;

    // Normalize messages to OpenAI/Perplexity-friendly shapes
    if (Array.isArray(request.messages)) {
      request.messages = request.messages
        .filter((m) => m?.role !== "tool")
        .map((m) => {
          if (!m || typeof m !== "object") return m;
          const mm = { ...m };

          // Remove tool-call artifacts
          delete mm.tool_calls;
          delete mm.tool_call_id;

          // Flatten Anthropic-style content blocks -> plain string
          if (Array.isArray(mm.content)) {
            mm.content = mm.content
              .map((part) => {
                if (typeof part === "string") return part;
                if (part && typeof part === "object" && typeof part.text === "string") return part.text;
                return "";
              })
              .join("");
          }

          // If content is still not a string, coerce (defensive)
          if (mm.content != null && typeof mm.content !== "string") {
            mm.content = String(mm.content);
          }

          return mm;
        });
    }

    return request;
  }
}

module.exports = PerplexityWebSearchFix;
