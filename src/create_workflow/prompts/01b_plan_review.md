# Task: Structural review of the <<DOMAIN>> generation plan

Review the plan BEFORE any files are generated — structural mistakes propagate into every subflow. Output a corrected plan in the SAME schema (downstream consumes it unchanged).

## Policy document(s) — source of truth

<<POLICY_DOCUMENTS>>

## Tool specifications

<<TOOL_SPECS>>

## Plan under review

```json
<<PLAN_JSON>>
```

## Structural checks (fix every violation)

1. **Flat, intent-based classification.** Every leaf intent is a branch of the single main classifier. Remove any sub-classifier unless one branch genuinely fans out to ≥4 unnameable issue types. A branch that exists only because a separate policy document exists is a defect — unify into one intent taxonomy.
2. **Troubleshooting = ordered checklist** (check cause → fix → re-test → continue), not a single one-shot "pick the root cause" decision. Split issue subflows by symptom only where their check sequences differ.
3. **Tool side.** A cause whose fix is a WRITE-tool action routes into that tool's shared mutating subflow (never inlined/duplicated); a user-device fix is an `agent_action` instructing the user. The same action reached from multiple intents is ONE shared subflow.
4. **Coverage.** Every WRITE tool is reachable through some intent; inverse operations both present where the policy supports them; every policy section assigned to a step or explicitly excluded with a reason.

## Output

Reason first: list each defect (or "no structural defects") citing the check number and the offending intent/subflow. Then output the corrected plan as exactly one fenced block in the identical schema (unchanged if nothing needs fixing).

```json
{ ...corrected plan, same schema as the input... }
```
