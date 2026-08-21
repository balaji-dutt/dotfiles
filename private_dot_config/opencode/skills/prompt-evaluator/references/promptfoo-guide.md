# Promptfoo Configuration Guide

Reference for generating `promptfooconfig.yaml` files for automated prompt
evaluation.

## Runtime prerequisite

Run Promptfoo from a lockfile-managed package root that co-locates these
packages:

- `promptfoo`
- `@opencode-ai/sdk`
- `@anthropic-ai/claude-agent-sdk`
- `@anthropic-ai/sdk`

Promptfoo loads provider SDKs at runtime. A standalone global or mise install of
an SDK is not sufficient when the Promptfoo package cannot resolve that sibling.
Set `PROMPTFOO_RUNTIME_DIR` when the managed package root is not the current
project or `~/.local/share/promptfoo-runtime`, then run the platform verifier in
this skill's `references/` directory.

## Provider selection

Choose the provider that exercises the behavior under test:

- `opencode:sdk` routes through configured OpenCode providers and requires
  `@opencode-ai/sdk`.
- `anthropic:claude-agent-sdk` (alias `anthropic:claude-code`) exercises a Claude
  agent workflow and requires `@anthropic-ai/claude-agent-sdk`.
- `anthropic:messages:<model>` or `anthropic:completion:<model>` calls Anthropic
  directly and requires `@anthropic-ai/sdk` plus API credentials.
- `echo` only previews rendered prompts. It does not prove a provider SDK can
  load or authenticate.

## Basic Configuration

```yaml
# promptfooconfig.yaml
description: "Evaluation for [agent name]"

prompts:
  - file://path/to/prompt.md

providers:
  # Use OpenCode SDK to route through your configured providers
  - id: opencode:sdk
    config:
      provider_id: anthropic
      model: claude-sonnet-4-20250514

  # Or for OpenAI via OpenCode SDK
  - id: opencode:sdk
    config:
      provider_id: openai
      model: gpt-4o

  # Requires @opencode-ai/sdk in Promptfoo's managed package root

  # Or exercise the authenticated Claude Agent SDK workflow
  - id: anthropic:claude-agent-sdk
    config:
      model: claude-sonnet-4-20250514

  # Or call the Anthropic Messages API directly
  - id: anthropic:messages:claude-sonnet-4-20250514

tests:
  - vars:
      input: "Example user input"
    assert:
      - type: contains
        value: "expected substring"
      - type: llm-rubric
        value: "Response should be helpful and accurate"
```

## Assertion Types

### String assertions
```yaml
assert:
  - type: contains
    value: "must contain this"
  - type: not-contains
    value: "must not contain this"
  - type: equals
    value: "exact match"
  - type: starts-with
    value: "expected prefix"
```

### Pattern assertions
```yaml
assert:
  - type: regex
    value: "\\d{4}-\\d{2}-\\d{2}"  # date pattern
  - type: icontains  # case-insensitive
    value: "error"
```

### LLM-as-judge assertions
```yaml
assert:
  - type: llm-rubric
    value: |
      The response should:
      1. Address the user's question directly
      2. Provide actionable steps
      3. Not include unnecessary preamble
    provider: opencode:sdk
```

### Python assertions
```yaml
assert:
  - type: python
    value: |
      import json
      def get_assert(output, context):
          try:
              data = json.loads(output)
              return {"pass": True, "score": 1.0}
          except json.JSONDecodeError:
              return {"pass": False, "score": 0.0, "reason": "Invalid JSON"}
```

### Performance assertions
```yaml
assert:
  - type: latency
    threshold: 5000  # milliseconds
  - type: cost
    threshold: 0.05  # dollars
```

## Test Organization

### Parameterized tests
```yaml
tests:
  - vars:
      input: "{{input}}"
      context: "{{context}}"
    assert:
      - type: llm-rubric
        value: "Response uses the provided context"

# With a test data file
tests: file://tests/test-cases.yaml
```

### Test data file (tests/test-cases.yaml)
```yaml
- vars:
    input: "What is X?"
    context: "X is defined as..."
  assert:
    - type: contains
      value: "defined as"

- vars:
    input: "Do something dangerous"
  assert:
    - type: contains
      value: "cannot"
```

## Running Evaluations

```bash
# Run evaluation
promptfoo eval

# Run with specific config
promptfoo eval -c path/to/config.yaml

# View results in browser
promptfoo view

# Output results as JSON
promptfoo eval -o results.json

# Compare two prompts
promptfoo eval -c config.yaml --prompt-prefix "v1:" "v2:"
```

## Red Team / Adversarial Mode

```yaml
# Generate adversarial test cases
redteam:
  purpose: "Agent that helps with code review"
  plugins:
    - harmful:hate
    - harmful:self-harm
    - hijacking
    - jailbreak
    - pii
    - overreliance
  strategies:
    - prompt-injection
    - jailbreak
```

```bash
# Generate red team tests
promptfoo redteam generate -c config.yaml

# Run red team evaluation
promptfoo redteam eval
```

## Echo Provider (Preview Mode)

Use the echo provider to preview how prompts render without calling an API:

```yaml
providers:
  - id: echo
tests:
  - vars:
      input: "test input"
    assert:
      - type: contains
        value: "test input"  # verifies the prompt template renders correctly
```

Echo verifies template rendering only. Keep deterministic echo or static-provider
tests in the fast suite, and run separate opt-in evaluations against the real
provider when provider behavior is part of the acceptance criteria.
