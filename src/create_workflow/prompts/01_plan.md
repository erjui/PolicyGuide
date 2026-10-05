# Task: Generation plan for the <<DOMAIN>> workflow graph

Decompose the policy below into a generation plan: the user intents the graph serves, any shared subflows, and an ordered step skeleton for every subflow. Later calls generate each file from this plan, so make the structural decisions now (gate placement, branching, shared subflows).

## Policy document(s) — source of truth

<<POLICY_DOCUMENTS>>

## Tool specifications (with the environment's WRITE/READ annotation)

<<TOOL_SPECS>>

## Planning requirements

1. **Cover every WRITE tool.** Each mutating tool is reachable through at least one intent subflow ending in its `authorize -> verify` pair. If one tool implements two operations with genuinely different eligibility, plan an internal `decision` branch inside that one subflow.
2. **Intents from the policy, not just the tools.** Include advisory/no-tool intents the policy describes and procedure-driven intents from any troubleshooting manual. Name each intent by the user's reported need. Unify all policy documents into ONE flat intent taxonomy (one classifier branch per leaf intent) — do not mirror document boundaries; add a sub-classifier only if one branch fans out to ≥4 issue types the user cannot name up front.
3. **Two archetypes.** *Transactional* (mutate a record): load the target → gate eligibility → confirm → execute (lean, a handful of nodes). *Troubleshooting* (diagnose a symptom against a manual): an ordered checklist — check a documented cause → apply its fix → re-test → continue; resolve on a passing re-test, escalate after all causes exhausted. An agent WRITE-tool fix routes into that tool's mutating subflow; a user-device fix is an `agent_action` instructing the user.
4. **Shared subflows.** Plan the identification subflow (if the policy requires identifying first) and a `present_summary_and_confirm` subflow. Add any other sequence shared by 2+ intents.
5. **Per-subflow skeletons.** For each subflow list ordered steps: `id` (snake_case), `type`, `tool` where applicable, one-line `gist` citing the policy line(s). The user is identified once on the spine; an intent subflow begins by loading the record it operates on. Order: load target (tool_call) → eligibility gate(s) → collect details/disclosures → summary_and_confirm → authorize → verify → exit (place the eligibility gate right after the data load even if the policy narrates reasons first). Merge related collection details into one step rather than one step per sentence.
6. **Coverage audit.** Walk the policy section by section; assign every normative statement to a step, or list it as excluded (with a reason) if out of graph scope.
7. **Lean.** Fewest subflows and nodes that enforce the policy: one subflow per leaf intent anchored from the main classifier; general/transfer/forbidden requests are classifier branches, not subflows.

## Output

Reason first (section-by-section walk). Then output exactly one fenced block:

```json
{
  "domain": "<<DOMAIN>>",
  "intents": [
    {"label": "<branch label>", "subflow": "<subflow name>",
     "description": "<one-line classifier branch description>",
     "mutating_tools": ["<tool>", "..."],
     "policy_sections": ["<section>", "..."], "notes": "<internal branches / gates, or empty>"}
  ],
  "shared_subflows": [{"name": "<name>", "purpose": "<one line>", "used_by": ["<subflow or main>", "..."]}],
  "sub_classifiers": [{"id": "<decision node id in main>", "purpose": "<one line>", "branches": {"<label>": "<subflow name>"}}],
  "subflow_skeletons": [
    {"name": "<name>", "entry": "<first step id>", "exits": ["<exit id>"],
     "steps": [{"id": "<id>", "type": "<type>", "tool": "<tool or omit>", "gist": "<one line>"}]}
  ],
  "excluded_policy_statements": [{"statement": "<quote>", "reason": "<why out of scope>"}]
}
```
