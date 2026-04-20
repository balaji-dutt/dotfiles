# Promptfoo Configuration Guide

Reference for generating `promptfooconfig.yaml` files for automated prompt
evaluation.

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

  # Requires: npm install @opencode-ai/sdk

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
npx promptfoo@latest eval

# Run with specific config
npx promptfoo@latest eval -c path/to/config.yaml

# View results in browser
npx promptfoo@latest view

# Output results as JSON
npx promptfoo@latest eval -o results.json

# Compare two prompts
npx promptfoo@latest eval -c config.yaml --prompt-prefix "v1:" "v2:"
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
npx promptfoo@latest redteam generate -c config.yaml

# Run red team evaluation
npx promptfoo@latest redteam eval
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
