# Task: Review of the generated <<DOMAIN>> workflow graph

You are the reviewer. The complete generated workflow is below with the policy (source of truth) and a deterministic lint report. Fix every defect; output ONLY the files that must change.

## Policy document(s) — source of truth

<<POLICY_DOCUMENTS>>

## Tool specifications

<<TOOL_SPECS>>

## Generated workflow files

<<WORKFLOW_FILES>>

## Deterministic lint report (resolve all ERRORs)

<<LINT_REPORT>>

## Review checklist — apply in this order

1. **Policy completeness.** Walk the policy line by line. Every normative statement must be enforced by some node's criteria (or be genuinely out of graph scope, e.g. style rules the agent's base prompt owns). Fix any missing rule, wrong number, or weakened condition.
2. **Cause coverage (enumerate explicitly).** Wherever the policy enumerates the possible causes/conditions of a problem, list every one and name the node whose criterion handles it. If any cause has no node, add it. A cause whose remedy is a mutating tool must be its OWN checkpoint reaching that tool's authorization — confirm it was not folded into a grouped node (where it gets skipped) or named without a path to the tool.
3. **Essentials compliance.** Gates outcome-framed (merely checking does not satisfy; refuse without advancing; placed right after the data they judge). Environment-gated enums quoted with exact equality and whose-field named. Facts grounded in tool results (not user assertions); consent/preference from the user. Numbers identical across a node's fields. No invented gate that no tool/data can establish. Every WRITE tool has its `authorize -> verify` pair with everything the policy requires upstream of the authorization. Tool nodes name only agent-inventory tools; user-device actions are agent_actions.
4. **Graph sanity.** Entry reaches every node; every node reaches an exit; decision branch labels/targets consistent; ids snake_case; edges all `when: "satisfied"`; classifier covers all intents + general + transfer with discriminative descriptions.
5. **Over-restriction sweep.** A node whose criterion cannot be satisfied from the available tools/data is a transfer machine — relax it to the policy's actual condition.

## Output

Reason through the checklist first. Then output every file that needs a change, complete:

--- FILE: <relative path> ---
```json
...
```

If nothing needs changing, output exactly: NO_CHANGES
