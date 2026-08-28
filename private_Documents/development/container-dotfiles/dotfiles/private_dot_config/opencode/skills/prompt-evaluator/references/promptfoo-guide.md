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

Use a repository-targeted live OpenCode server only when the test must exercise
OpenCode authentication, model routing, or runtime discovery. Prompt text and
rendering contracts should normally use deterministic assertions plus
LLM-as-judge. Claude Agent SDK and direct Anthropic API behavior should use their
matching providers.

## Repository-targeted OpenCode evaluation

### Decide what the candidate is

| Candidate | How to evaluate it |
| --- | --- |
| Prompt text | Point Promptfoo at the candidate prompt file. Prompt edits are direct inputs and do not require an OpenCode restart. |
| Agent, skill, plugin, or other startup-loaded configuration | Stage or generate the candidate into an isolated fixture rooted at the intended repository/worktree before server startup. Restart after every candidate configuration change. |

Starting OpenCode from a source repository does not by itself prove that it
loaded ungenerated source files. Confirm that the candidate artifact is in a
location OpenCode discovers at startup rather than silently evaluating an older
installed copy.

### Start a fresh owned server

Run the server from the exact repository or linked worktree under evaluation:

```bash
TARGET_DIR="$(git -C "/absolute/path/to/target-worktree" rev-parse --show-toplevel)"
SERVER_LOG="$(mktemp "${TMPDIR:-/tmp}/promptfoo-opencode.XXXXXX.log")"
SERVER_PID=""

cleanup_opencode_eval() {
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  command rm -f -- "$SERVER_LOG"
}
trap cleanup_opencode_eval EXIT INT TERM

(
  cd "$TARGET_DIR"
  exec opencode serve --hostname 127.0.0.1 --port 0
) >"$SERVER_LOG" 2>&1 &
SERVER_PID="$!"

BASE_URL="$(
  python3 - "$SERVER_LOG" <<'PY'
import pathlib
import re
import sys
import time

log_path = pathlib.Path(sys.argv[1])
for _ in range(100):
    text = log_path.read_text(errors="replace") if log_path.exists() else ""
    match = re.search(r"http://127\.0\.0\.1:\d+", text)
    if match:
        print(match.group(0))
        break
    time.sleep(0.1)
else:
    raise SystemExit("OpenCode did not advertise a loopback endpoint")
PY
)"
```

Port `0` asks the operating system to select one currently available loopback
port. It does not expose every port. The polling block captures the
`http://127.0.0.1:<port>` endpoint that OpenCode advertises in `SERVER_LOG`; do
not guess a port or reuse an unverified server discovered elsewhere.

Do not add `--pure` automatically. It can be useful when external plugins are
explicitly outside the test, but it invalidates a test whose candidate or
behavior depends on plugin loading.

### Verify health, version, and repository identity

Before Promptfoo sends a test, verify the server against the intended target.
The following check uses only Python's standard library after `BASE_URL` is
captured:

```bash
EXPECTED_VERSION="$(opencode --version)"

python3 - "$BASE_URL" "$TARGET_DIR" "$EXPECTED_VERSION" <<'PY'
import json
import pathlib
import sys
import urllib.parse
import urllib.request

base_url, target_dir, expected_version = sys.argv[1:]
target = str(pathlib.Path(target_dir).resolve())
parsed_url = urllib.parse.urlparse(base_url)
if parsed_url.scheme != "http" or parsed_url.hostname != "127.0.0.1":
    raise SystemExit("OpenCode endpoint is not loopback HTTP")
if parsed_url.port is None:
    raise SystemExit("OpenCode endpoint does not advertise a port")


def get_json(route):
    with urllib.request.urlopen(f"{base_url}{route}", timeout=5) as response:
        return json.load(response)


def resolved(value):
    if not value:
        return None
    return str(pathlib.Path(value).resolve())


health = get_json("/global/health")
if health.get("healthy") is not True or health.get("version") != expected_version:
    raise SystemExit("OpenCode health/version check failed")

path_info = get_json("/path")
if resolved(path_info.get("directory", "")) != target:
    raise SystemExit("OpenCode directory does not match target")
if resolved(path_info.get("worktree", "")) != target:
    raise SystemExit("OpenCode worktree does not match target")

project = get_json("/project/current")
project_paths = [project.get("worktree", ""), *(project.get("sandboxes") or [])]
if target not in {resolved(value) for value in project_paths if value}:
    raise SystemExit("OpenCode project does not contain target worktree")
PY
```

For a normal checkout, `/project/current.worktree` usually matches the target.
For a linked worktree, it may name the common/main project root while the exact
target appears in `sandboxes`. `/path.directory` and `/path.worktree` must still
match the requested target. These checks prevent a healthy server for another
repository or concurrent worktree from producing misleading results.

### Configure Promptfoo explicitly

Substitute the captured endpoint and exact target path into a task-local config:

```yaml
providers:
  - id: opencode:sdk
    config:
      baseUrl: http://127.0.0.1:<advertised-port>
      working_dir: /absolute/path/to/target-worktree
      provider_id: anthropic
      model: claude-sonnet-4-20250514
      tools:
        bash: false
        edit: false
        write: false
        read: false
        grep: false
        glob: false
        list: false
        patch: false
        todowrite: false
        todoread: false
        webfetch: false
        question: false
        skill: false
        lsp: false
```

When `working_dir` is set, Promptfoo otherwise enables several read-oriented
tools by default. Prompt-only tests must set every supported tool to `false` as
shown. Tool-behavior tests should run in an isolated fixture and change only the
minimum keys needed to `true`.

Supplying `baseUrl` makes Promptfoo connect to that server; do not mix in
Promptfoo server-spawn options and assume they affect an already running
server. Run the live evaluation manually, capture its result, and let the trap
terminate the owned process and remove the temporary log.

### Reuse a caller-owned server only by explicit handoff

If a caller already owns the desired server, pass its URL only for the current
command:

```bash
PROMPTFOO_OPENCODE_BASE_URL="$VERIFIED_BASE_URL" \
  promptfoo eval -c /tmp/task-promptfooconfig.yaml
```

Reference it from YAML as:

```yaml
baseUrl: "{{env.PROMPTFOO_OPENCODE_BASE_URL}}"
```

Run the same health, version, `/path`, and `/project/current` checks before use.
Do not terminate the caller-owned process. Never use fixed ports or create
repository-specific environment variable names.

### Classify failures and retain only useful assets

Failure to start the server, load the SDK, authenticate, pass health checks, or
match repository identity is an infrastructure failure. Record it separately
from prompt behavior and fall back to LLM-as-judge; do not claim that a live
provider passed or that the prompt failed.

Keep one-off configs, logs, and result files in task-local temporary storage and
remove them after reporting. Commit a config or result only when it represents a
stable public contract or a deliberate regression gate. Credentialed live
provider runs stay manual and opt-in rather than becoming required CI.

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
