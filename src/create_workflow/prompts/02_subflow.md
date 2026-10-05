# Task: Generate the `<<SUBFLOW_NAME>>` subflow for the <<DOMAIN>> domain

Generate the complete `subflows/<<SUBFLOW_NAME>>.subflow.json`, implementing its planned skeleton against the policy. The skeleton fixes the structure; you write precise node text and the edges.

## Policy document(s) — source of truth

<<POLICY_DOCUMENTS>>

## Tool specifications

<<TOOL_SPECS>>

## Generation plan (context)

```json
<<PLAN_JSON>>
```

## Target skeleton for this subflow

```json
<<SKELETON_JSON>>
```

## Shared subflows you may anchor (reference by name only)

<<SHARED_SUBFLOW_INTERFACES>>

## Requirements

- Follow the skeleton's step order and ids; add/split a step only if a policy rule cannot otherwise be expressed (say so in reasoning).
- Every policy rule assigned to this subflow appears in some node's criteria, numbers/tables quoted exactly.
- Eligibility/permissibility gates are outcome-framed (SATISFIED only if the condition HOLDS; merely checking does not satisfy; on failure stay NOT_SATISFIED and REFUSE), placed right after the data they judge, with exact-enum + whose-field wording where the environment gates on a literal. Encode only conditions the policy states.
- Data steps are source-neutral ("known/available") with lookup-first directives (look up with a read-only tool before asking the user); a search/lookup step demands the SPECIFIC item the user wants.
- A troubleshooting cause-check with a remedy is SATISFIED only after the user has performed the remedy and re-tested (it advances after that retry whether or not the symptom cleared); a pure prerequisite/escalation step advances once reported/instructed.
- End each mutating path with `present_summary_and_confirm` anchor -> `authorize_<short>` -> `verify_<tool>` -> exit. The verify node confirms a successful TOOL_RESULT (NOT a TOOL_ERROR); on a correctable error it directs a retry, otherwise concludes the action is not possible.
- `kind`: "subflow", `max_directives_per_node`: 5.

## Output

Reason briefly, then output exactly one file:

--- FILE: subflows/<<SUBFLOW_NAME>>.subflow.json ---
```json
...
```
