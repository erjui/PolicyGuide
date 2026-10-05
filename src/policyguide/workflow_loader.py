"""Validate workflow specifications and compose their referenced subflows.

Subflow anchors are inlined into a flat graph when the workflow is loaded."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable


NODE_TYPES = frozenset({
    "entry", "exit",
    "agent_action", "user_input", "tool_call", "tool_authorization",
    "decision", "subflow",
})
# Nodes that make an LLM evaluator call (and therefore require prompt + directive).
# tool_authorization is NOT here — it has no LLM call; it matches attempted_tool_name directly.
LLM_EVALUATOR_TYPES = frozenset({"agent_action", "user_input", "tool_call"})
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VALID_KINDS = frozenset({"main", "subflow"})


class WorkflowSpecError(ValueError):
    """Raised by validate_file / compose on malformed input."""


# ─────────────────────────────────────────────────────────────────────────────
# File-level validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_file(spec: dict) -> None:
    """Validate a single main or subflow spec dict. Raises WorkflowSpecError."""
    name = spec.get("name", "?")

    required_top = ("schema_version", "kind", "domain", "name",
                    "entry", "exits", "nodes", "edges")
    for f in required_top:
        if f not in spec:
            raise WorkflowSpecError(f"Spec {name!r}: missing top-level field {f!r}")

    if spec["kind"] not in VALID_KINDS:
        raise WorkflowSpecError(
            f"Spec {name!r}: invalid kind {spec['kind']!r} "
            f"(must be one of {sorted(VALID_KINDS)})"
        )

    if not isinstance(spec["nodes"], list) or not spec["nodes"]:
        raise WorkflowSpecError(f"Spec {name!r}: nodes[] must be a non-empty list")
    if not isinstance(spec["exits"], list) or not spec["exits"]:
        raise WorkflowSpecError(f"Spec {name!r}: exits[] must be a non-empty list")
    if not isinstance(spec["edges"], list):
        raise WorkflowSpecError(f"Spec {name!r}: edges must be a list")

    ids: set[str] = set()
    entry_seen = False
    for n in spec["nodes"]:
        nid = n.get("id")
        if not isinstance(nid, str) or not ID_RE.match(nid):
            raise WorkflowSpecError(
                f"Spec {name!r}: bad node id {nid!r} (must match {ID_RE.pattern})"
            )
        if nid in ids:
            raise WorkflowSpecError(f"Spec {name!r}: duplicate node id {nid!r}")
        ids.add(nid)

        t = n.get("type")
        if t not in NODE_TYPES:
            raise WorkflowSpecError(
                f"Spec {name!r}: node {nid!r} has invalid type {t!r} "
                f"(must be one of {sorted(NODE_TYPES)})"
            )
        if t == "entry":
            entry_seen = True

        if t in ("tool_call", "tool_authorization") and not n.get("tool"):
            raise WorkflowSpecError(
                f"Spec {name!r}: {t} node {nid!r} missing required 'tool' field"
            )
        if t == "decision":
            branches = n.get("branches")
            if not isinstance(branches, dict) or not branches:
                raise WorkflowSpecError(
                    f"Spec {name!r}: decision node {nid!r} missing non-empty 'branches' dict"
                )
        if t == "subflow" and not n.get("subflow"):
            raise WorkflowSpecError(
                f"Spec {name!r}: subflow node {nid!r} missing 'subflow' reference"
            )
        if t in LLM_EVALUATOR_TYPES:
            for f in ("evaluation_prompt", "directive_template"):
                if not n.get(f):
                    raise WorkflowSpecError(
                        f"Spec {name!r}: {t} node {nid!r} missing required {f!r}"
                    )
        if t == "decision" and not n.get("evaluation_prompt"):
            raise WorkflowSpecError(
                f"Spec {name!r}: decision node {nid!r} missing required 'evaluation_prompt'"
            )

    if spec["entry"] not in ids:
        raise WorkflowSpecError(
            f"Spec {name!r}: entry {spec['entry']!r} is not a declared node id"
        )
    for ex in spec["exits"]:
        if ex not in ids:
            raise WorkflowSpecError(
                f"Spec {name!r}: exit {ex!r} is not a declared node id"
            )

    # Edge sanity
    for i, e in enumerate(spec["edges"]):
        for f in ("from", "to"):
            if f not in e:
                raise WorkflowSpecError(
                    f"Spec {name!r}: edge[{i}] missing field {f!r}"
                )
            if e[f] not in ids:
                raise WorkflowSpecError(
                    f"Spec {name!r}: edge[{i}] references unknown node {e[f]!r}"
                )

    # Decision branch targets must be local node IDs
    for n in spec["nodes"]:
        if n["type"] == "decision":
            for label, target in n["branches"].items():
                if target not in ids:
                    raise WorkflowSpecError(
                        f"Spec {name!r}: decision {n['id']!r} branch "
                        f"{label!r} targets unknown node {target!r}"
                    )


# ─────────────────────────────────────────────────────────────────────────────
# Directory loader
# ─────────────────────────────────────────────────────────────────────────────

def load_workflow_dir(workflow_dir: Path, expected_domain: str) -> dict:
    """Load a workflow directory.

    Returns {'manifest': dict, 'main': spec, 'subflows': {name: spec}}.

    Raises FileNotFoundError if `_manifest.json` or `main.workflow.json` is missing.
    Raises WorkflowSpecError on validation failures or domain mismatches.
    """
    workflow_dir = Path(workflow_dir)

    manifest_path = workflow_dir / "_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Missing manifest: {manifest_path}. Every workflow directory "
            f"must contain _manifest.json."
        )
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("domain") != expected_domain:
        raise WorkflowSpecError(
            f"Manifest domain {manifest.get('domain')!r} != "
            f"expected {expected_domain!r} ({manifest_path})"
        )

    main_path = workflow_dir / "main.workflow.json"
    if not main_path.exists():
        raise FileNotFoundError(
            f"Missing main graph: {main_path}. Every workflow directory must "
            f"contain main.workflow.json."
        )
    main_spec = json.loads(main_path.read_text())
    validate_file(main_spec)
    if main_spec["kind"] != "main":
        raise WorkflowSpecError(
            f"{main_path}: top-level file must have kind=main "
            f"(got {main_spec['kind']!r})"
        )
    if main_spec["domain"] != expected_domain:
        raise WorkflowSpecError(
            f"{main_path}: domain {main_spec['domain']!r} != expected {expected_domain!r}"
        )

    subflows: dict[str, dict] = {}
    subflows_dir = workflow_dir / "subflows"
    if subflows_dir.exists():
        for sp in sorted(subflows_dir.glob("*.subflow.json")):
            spec = json.loads(sp.read_text())
            validate_file(spec)
            if spec["kind"] != "subflow":
                raise WorkflowSpecError(
                    f"{sp}: file under subflows/ must have kind=subflow "
                    f"(got {spec['kind']!r})"
                )
            if spec["domain"] != expected_domain:
                raise WorkflowSpecError(
                    f"{sp}: domain {spec['domain']!r} != expected {expected_domain!r}"
                )
            if spec["name"] in subflows:
                raise WorkflowSpecError(f"Duplicate subflow name {spec['name']!r}")
            subflows[spec["name"]] = spec

    return {"manifest": manifest, "main": main_spec, "subflows": subflows}


# ─────────────────────────────────────────────────────────────────────────────
# Composition (sub-workflow inlining)
# ─────────────────────────────────────────────────────────────────────────────

def compose(bundle: dict) -> dict:
    """Inline all subflow references into a single flat composed graph.

    Inputs:
      bundle = {'manifest': ..., 'main': spec, 'subflows': {name: spec}}

    Output: dict with:
      - domain (str)
      - main_name (str)
      - entry (str)                     composed node ID of the main entry
      - exits (list[str])               composed node IDs of all main exits
      - nodes (list[dict])              each has 'composed_id' + original node fields
      - edges (list[dict])              each {from, to, when}
      - max_directives_per_node (int)

    Raises WorkflowSpecError on subflow-reference cycles or missing subflows.
    """
    main = bundle["main"]
    subflows: dict[str, dict] = bundle["subflows"]

    all_nodes: dict[str, dict] = {}
    all_edges: list[dict] = []

    def _expand(spec: dict, prefix: str, visiting: frozenset[str]) -> tuple[str, list[str]]:
        """Expand one spec under `prefix`, recursing into subflow nodes.

        Returns (entry_composed_id, exit_composed_ids).
        """
        local_to_composed: dict[str, str] = {}
        sub_anchors: dict[str, tuple[str, list[str]]] = {}

        # Pass 1: assign composed IDs for every node; recurse subflows.
        for n in spec["nodes"]:
            local = n["id"]
            composed = f"{prefix}.{local}" if prefix else local
            local_to_composed[local] = composed

            if n["type"] == "subflow":
                sub_name = n["subflow"]
                if sub_name in visiting:
                    raise WorkflowSpecError(
                        f"Cycle in subflow references at {sub_name!r} "
                        f"(visiting={sorted(visiting)})"
                    )
                if sub_name not in subflows:
                    raise WorkflowSpecError(
                        f"Subflow {sub_name!r} referenced by {composed!r} "
                        f"is not defined in subflows/"
                    )
                sub_entry, sub_exits = _expand(
                    subflows[sub_name], composed, visiting | {sub_name}
                )
                sub_anchors[local] = (sub_entry, sub_exits)
                # The subflow node itself is dissolved — no entry in all_nodes.
            else:
                node_copy = dict(n)
                node_copy["composed_id"] = composed
                if composed in all_nodes:
                    raise WorkflowSpecError(
                        f"Composed node id collision: {composed!r}. "
                        f"This usually means the same subflow is referenced "
                        f"under the same prefix. Use distinct local ids."
                    )
                all_nodes[composed] = node_copy

        # Pass 2: resolve edges, dissolving subflow anchors.
        for e in spec["edges"]:
            src_local = e["from"]
            dst_local = e["to"]
            when = e.get("when", "satisfied")

            # Destination: subflow → its entry
            if dst_local in sub_anchors:
                dst_resolved = [sub_anchors[dst_local][0]]
            else:
                dst_resolved = [local_to_composed[dst_local]]

            # Source: subflow → each of its exits
            if src_local in sub_anchors:
                src_resolved = list(sub_anchors[src_local][1])
            else:
                src_resolved = [local_to_composed[src_local]]

            for s in src_resolved:
                for d in dst_resolved:
                    all_edges.append({"from": s, "to": d, "when": when})

        # Pass 3: rewrite decision branches to composed IDs.
        for n in spec["nodes"]:
            if n["type"] == "decision":
                composed = local_to_composed[n["id"]]
                rewritten: dict[str, str] = {}
                for label, target in n["branches"].items():
                    if target in sub_anchors:
                        rewritten[label] = sub_anchors[target][0]
                    else:
                        rewritten[label] = local_to_composed[target]
                all_nodes[composed]["branches"] = rewritten

        # Resolve this spec's entry/exits to composed IDs (handle subflow case).
        spec_entry = spec["entry"]
        if spec_entry in sub_anchors:
            entry_composed = sub_anchors[spec_entry][0]
        else:
            entry_composed = local_to_composed[spec_entry]

        spec_exits_composed: list[str] = []
        for ex in spec["exits"]:
            if ex in sub_anchors:
                spec_exits_composed.extend(sub_anchors[ex][1])
            else:
                spec_exits_composed.append(local_to_composed[ex])

        return entry_composed, spec_exits_composed

    entry, exits = _expand(main, prefix="", visiting=frozenset())

    return {
        "domain": main["domain"],
        "main_name": main["name"],
        "entry": entry,
        "exits": exits,
        "nodes": list(all_nodes.values()),
        "edges": all_edges,
        "max_directives_per_node": main.get("max_directives_per_node", 5),
        "max_reclassifications": main.get("max_reclassifications", 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: top-level entry point
# ─────────────────────────────────────────────────────────────────────────────

def load_and_compose(workflow_dir: Path, expected_domain: str) -> tuple[dict, dict]:
    """Load a workflow directory and return (manifest, composed_graph)."""
    bundle = load_workflow_dir(workflow_dir, expected_domain)
    return bundle["manifest"], compose(bundle)


def compute_intent_containment(bundle: dict) -> dict:
    """{intent_label: {other_intent_labels run as internal steps}} from the raw bundle.

    A top-level intent (main-classifier branch -> subflow) "contains" another intent when
    its subflow reaches that other intent's subflow via `subflow` nodes (transitively).
    The tracker uses this so it does not open a contained operation as a separate request
    while the containing intent is active (the agent running it is a STEP of that request).
    """
    main = bundle["main"]
    subflows: dict = bundle["subflows"]
    main_nodes = {n["id"]: n for n in main["nodes"]}
    classifier = max(
        (n for n in main["nodes"] if n.get("type") == "decision" and n.get("branches")),
        key=lambda n: len(n["branches"]), default=None)
    if not classifier:
        return {}
    intent_subflow: dict = {}
    for label, tgt in classifier["branches"].items():
        node = main_nodes.get(tgt)
        if node and node.get("type") == "subflow" and node.get("subflow"):
            intent_subflow[label] = node["subflow"]
    subflow_to_label = {sf: lab for lab, sf in intent_subflow.items()}

    def _reachable(sf: str, seen: set) -> set:
        out: set = set()
        for n in (subflows.get(sf) or {}).get("nodes", []):
            if n.get("type") == "subflow" and n.get("subflow") and n["subflow"] not in seen:
                seen.add(n["subflow"]); out.add(n["subflow"]); out |= _reachable(n["subflow"], seen)
        return out

    containment: dict = {}
    for label, sf in intent_subflow.items():
        contained = {subflow_to_label[r] for r in _reachable(sf, set())
                     if r in subflow_to_label and subflow_to_label[r] != label}
        if contained:
            containment[label] = contained
    return containment


def iter_mutating_tools(composed: dict) -> Iterable[str]:
    """Yield tool names from all tool_authorization nodes in a composed graph."""
    for n in composed["nodes"]:
        if n.get("type") == "tool_authorization" and n.get("tool"):
            yield n["tool"]
