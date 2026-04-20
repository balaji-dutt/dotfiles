# EARS Syntax Reference

EARS (Easy Approach to Requirements Syntax) provides five patterns for writing
unambiguous, testable requirements.

## Pattern 1: Ubiquitous

**Template**: "The [system] shall [action]."

**Use for**: Behavior that is always active, with no trigger or condition.

**Examples**:
- "The agent shall respond in English."
- "The agent shall include source citations in every response."
- "The agent shall use markdown formatting for all output."

**Test strategy**: Verify across all interactions; any violation is a defect.

## Pattern 2: Event-Driven

**Template**: "When [event], the [system] shall [action]."

**Use for**: Behavior triggered by a specific event or input.

**Examples**:
- "When the user provides a code snippet, the agent shall analyze it for bugs."
- "When an error is detected, the agent shall report the error type and
  suggest a fix."
- "When the user says 'done', the agent shall summarize all changes made."

**Test strategy**: Trigger the event and verify the action occurs.

## Pattern 3: State-Driven

**Template**: "While [state], the [system] shall [action]."

**Use for**: Behavior that persists as long as a condition holds.

**Examples**:
- "While in debug mode, the agent shall show its reasoning steps."
- "While processing a multi-file change, the agent shall track all modified
  files."
- "While the user has not confirmed, the agent shall not execute destructive
  commands."

**Test strategy**: Enter the state, verify behavior persists, exit the state,
verify behavior stops.

## Pattern 4: Conditional (If-Then)

**Template**: "If [condition], then the [system] shall [action]."

**Use for**: One-time conditional behavior based on a detected condition.

**Examples**:
- "If the input exceeds 1000 tokens, then the agent shall summarize before
  processing."
- "If no examples are provided, then the agent shall ask for at least one."
- "If the task involves filesystem changes, then the agent shall preview
  changes before applying."

**Test strategy**: Create the condition, verify the action fires exactly once.

## Pattern 5: Unwanted Behavior

**Template**: "If [unwanted situation], then the [system] shall [mitigation]."

**Use for**: Error handling, safety guardrails, and edge cases.

**Examples**:
- "If the agent cannot determine the user's intent, then it shall ask a
  clarifying question rather than guessing."
- "If the generated code fails syntax validation, then the agent shall fix the
  errors before presenting the result."
- "If the request involves sensitive data, then the agent shall refuse and
  explain why."

**Test strategy**: Induce the unwanted situation, verify mitigation fires.

## Combining Patterns

Complex requirements often combine patterns:

```
While in review mode,                          (State-driven)
  when the user submits a change,              (Event-driven)
    if the change affects more than 3 files,   (Conditional)
      then the agent shall request             (Action)
      a detailed justification.
```

## Common Pitfalls

- **Vague verbs**: "handle", "manage", "process" — replace with specific
  actions.
- **Missing trigger**: If behavior is not ubiquitous, it needs a when/while/if.
- **Untestable**: If you cannot write a test case, the requirement is too vague.
- **Compound requirements**: Split "shall X and Y" into separate requirements.
