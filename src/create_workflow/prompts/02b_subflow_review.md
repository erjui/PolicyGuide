# Task: Structural path review of the `<<SUBFLOW_NAME>>` subflow (<<DOMAIN>>)

Review ONLY the control flow of the generated subflow against its intended skeleton — not policy wording. Output the corrected file, or `NO_CHANGES`.

## Intended skeleton

```json
<<SKELETON_JSON>>
```

## Generated subflow

```json
<<SUBFLOW_JSON>>
```

## Path checks — trace EVERY path from `entry` to an exit, following edges and each decision branch

1. **No premature success exit.** A path reaches a resolved/normal exit only after it could perform the steps the skeleton places before it. Flag a re-test/recheck that routes straight to a resolved exit while skipping remaining checks.
2. **Outcome-dependent routing goes through a decision.** Where the next step depends on an outcome (passed/failed, present/absent, resolved/unresolved), routing passes through a `decision` with the right branches — not a single unconditional edge assuming one outcome.
3. **No dead/unreachable nodes or tails.** Every non-exit node is reachable from `entry` and has a forward path to an exit.
4. **Branches complete + consistent.** Every decision branch label has a valid in-file target, and every outcome the flow must handle has a branch (resolved → exit; continue → next check; exhausted → escalate/transfer).

Fix only control flow (edges, decision nodes/branches, ordering). Do NOT rewrite node criteria text. Preserve node ids, `kind`, `max_directives_per_node`.

## Output

Reason briefly (trace each path; list violations citing node ids, or state the flow is sound). Then:
- If no control-flow change is needed: output exactly `NO_CHANGES`
- Otherwise output the complete corrected file:

--- FILE: subflows/<<SUBFLOW_NAME>>.subflow.json ---
```json
...
```
