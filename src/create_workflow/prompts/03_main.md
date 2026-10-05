# Task: Generate `main.workflow.json` for the <<DOMAIN>> domain

Generate the top-level main workflow wiring the spine, the intent classifier, any sub-classifiers, the subflow anchors, and the exits.

## Policy document(s) — source of truth

<<POLICY_DOCUMENTS>>

## Generation plan

```json
<<PLAN_JSON>>
```

## Generated subflows (anchor by name; their entry/exits wire automatically)

<<SUBFLOW_INTERFACES>>

## Requirements

- Spine: `start (entry) -> intake (agent_action: greet + open question fitting this domain) -> identify (anchor of the shared identification subflow, if planned) -> classify (decision) -> intent anchors -> exits ["exit_normal", "exit_general", "exit_transfer"]`.
- The `classify` decision has one branch per planned intent + `general` -> `exit_general` + `transfer` -> `exit_transfer`, each with a discriminative one-line `branch_descriptions` entry (sharpen the plan's descriptions wherever two intents could be confused).
- Sub-classifiers from the plan are additional `decision` nodes branched to from classify, routing to issue subflow anchors (plus a `transfer` branch).
- Every intent subflow anchor edges to `exit_normal` with `when: "satisfied"`. Decision nodes route only via branches — no outgoing edges.
- `kind`: "main", `max_directives_per_node`: 15.

## Output

Reason briefly, then output exactly one file:

--- FILE: main.workflow.json ---
```json
...
```
