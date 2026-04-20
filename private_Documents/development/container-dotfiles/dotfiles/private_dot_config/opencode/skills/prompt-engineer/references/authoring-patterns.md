# Prompt Authoring Patterns

## Pattern 1: Role-Task-Constraints-Format (RTCF)

The foundational prompt structure:

```
You are [ROLE] with expertise in [DOMAIN].

Your task is to [SPECIFIC TASK].

Constraints:
- [CONSTRAINT 1]
- [CONSTRAINT 2]

Output format:
[FORMAT SPECIFICATION]
```

## Pattern 2: Chain-of-Thought (CoT)

For complex reasoning tasks, instruct step-by-step thinking:

```
Think through this step-by-step:
1. First, analyze [aspect 1]
2. Then, consider [aspect 2]
3. Finally, synthesize your findings into [output]

Show your reasoning before giving the final answer.
```

## Pattern 3: Few-Shot Examples

Provide input/output pairs to demonstrate expected behavior:

```
Here are examples of the expected behavior:

Example 1:
Input: [example input]
Output: [example output]

Example 2:
Input: [example input]
Output: [example output]

Now process this input:
[actual input]
```

## Pattern 4: Structured XML Output

Use XML tags for parseable, structured responses:

```
Respond using this structure:

<analysis>
  <finding>[description]</finding>
  <severity>[low|medium|high]</severity>
  <recommendation>[action]</recommendation>
</analysis>
```

## Pattern 5: Progressive Disclosure

For large context, layer information:

```
## Core instructions (always apply)
[essential rules]

## Extended context (reference as needed)
[background material]

## Edge cases (consult when uncertain)
[rare scenarios and handling]
```

## Pattern 6: Negative Instructions

Explicitly state what NOT to do:

```
IMPORTANT:
- Do NOT make assumptions about [X] without evidence.
- Do NOT generate [Y] unless explicitly asked.
- NEVER include [Z] in the output.
```

## Pattern 7: Self-Verification

Build in quality checks:

```
Before delivering your response:
1. Verify that [criterion 1] is satisfied.
2. Check that [criterion 2] is met.
3. Confirm no [common mistake] is present.
If any check fails, revise before responding.
```

## Pattern 8: Hierarchical Delegation

For multi-agent workflows, define handoff points:

```
You are the coordinator agent. Your responsibilities:
1. Receive the user request.
2. Classify it as [type A], [type B], or [type C].
3. For type A: delegate to [Agent A] with this context: [...]
4. For type B: handle directly using [approach].
5. Synthesize results and present to the user.
```

## Pattern 9: Guardrail Patterns

Enforce safety and scope boundaries:

```
Scope: You ONLY handle [domain]. If the request is outside this scope:
1. Acknowledge the request.
2. Explain why it is outside your scope.
3. Suggest who or what can help.
Do NOT attempt to answer out-of-scope requests.
```

## Pattern 10: Iterative Refinement

For tasks requiring multiple passes:

```
Process this in two passes:

Pass 1 (Draft): Generate an initial [output] focusing on [primary goal].
Pass 2 (Refine): Review your draft against [criteria] and improve it.

Present only the refined version unless asked for both.
```
