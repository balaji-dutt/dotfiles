# Model-Specific Prompt Guidance

## Claude (Anthropic)

### Structural preferences
- Use XML tags (`<context>`, `<instructions>`, `<examples>`) to delineate
  sections. Claude parses XML structure natively.
- Place the most important instructions at the beginning and end of the prompt
  (primacy and recency effects).
- Use `<thinking>` blocks for extended reasoning when the model supports it.

### Effective patterns
- **System prompt**: Define role, constraints, and output format here. Keep it
  stable across turns.
- **Prefill**: Start the assistant turn with the beginning of the expected
  output to guide format compliance.
- **Artifacts**: For code or structured deliverables, use artifact-style output
  blocks.

### Temperature guidance
- Factual/analytical tasks: 0.0-0.3
- Creative/generative tasks: 0.7-1.0
- Code generation: 0.0-0.2
- Note: When using extended thinking, temperature settings may be overridden.

### Claude-specific tips
- Claude responds well to "be direct" and "be concise" instructions.
- Avoid overly polite framing; Claude does not need encouragement to comply.
- For multi-step tasks, numbered steps with clear deliverables per step work
  well.

## GPT (OpenAI)

### Structural preferences
- Use the system/user/assistant message structure explicitly.
- System message: role + constraints + format.
- User message: task + context + examples.
- Function/tool calling: define tools with JSON Schema for structured outputs.

### Effective patterns
- **Structured outputs**: Use `response_format: { type: "json_schema", ... }`
  for guaranteed JSON compliance.
- **Reasoning models (o-series)**: Provide high-level goals, not step-by-step
  instructions. The model plans its own chain of thought.
- **Few-shot in system**: Place examples in the system message for consistent
  behavior across turns.

### Temperature guidance
- Factual/analytical: 0.0-0.2
- Creative/generative: 0.7-1.2
- Code generation: 0.0-0.2
- Reasoning models: temperature is typically fixed.

### GPT-specific tips
- GPT models benefit from explicit output length guidance ("respond in 2-3
  paragraphs").
- For JSON output, always provide a schema or example.
- Use `max_tokens` to prevent runaway generation.

## General Cross-Model Guidance

### What works everywhere
- Clear role definition in the first sentence.
- Explicit output format specification.
- Numbered steps for multi-step tasks.
- Examples for non-trivial expected behavior.
- Negative instructions for common failure modes.

### What varies by model
- XML tags (Claude-native, tolerated by others).
- Function calling schemas (model-specific APIs).
- Reasoning/thinking modes (model-specific features).
- Token limits and context window sizes.

### Adaptation strategy
When porting a prompt between models:
1. Keep the core RTCF structure unchanged.
2. Swap structural markers (XML tags vs markdown headers).
3. Adjust temperature and sampling for the target model.
4. Test with 3-5 representative inputs and compare outputs.
5. Iterate on model-specific failure modes.
