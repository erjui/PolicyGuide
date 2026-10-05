"""Minimal lint for the simplified generator.

Keeps only checks that serve the bottom line — a graph that is (1) faithful to the
policy's mutating surface, (2) faithful to task/tool properties, (3) valid + runnable:

  ERROR  composition (cycles / missing subflow refs)      [runnable]
  ERROR  write-coverage (every WRITE tool authorized)      [faithful surface]
  ERROR  phantom-tool (tool nodes name only agent tools)   [task properties]
  ERROR  edge-arity (evaluator nodes: exactly 1 out-edge; decisions: branches only)
  WARN   authorize -> verify pair present                  [tool-gate keys on it]
  WARN   reachability (entry reaches all; all reach an exit)
  WARN   main conventions (classify w/ general + transfer; exit_general/transfer)

Dropped vs create_workflow/lint.py: r15.multitool, r16.thin, r18 granularity,
r20.flat, r1.confirm, r17.dup, decision-style nags.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tracked/src for policyguide
from policyguide.workflow_loader import compose, WorkflowSpecError

_EVAL_TYPES = {"agent_action", "user_input", "tool_call"}


def lint_bundle(main_spec: dict, subflows: dict, write_tools, agent_tools=None) -> list[dict]:
    findings: list[dict] = []
    write_tools = set(write_tools or [])
    agent_tools = set(agent_tools or [])

    def err(code, msg):
        findings.append({"level": "ERROR", "code": code, "msg": msg})

    def warn(code, msg):
        findings.append({"level": "WARN", "code": code, "msg": msg})

    all_specs = [main_spec] + list(subflows.values())

    # phantom-tool + edge-arity (per file)
    for spec in all_specs:
        nid = {n["id"] for n in spec["nodes"]}
        out_edges: dict[str, int] = {}
        for e in spec.get("edges", []):
            out_edges[e["from"]] = out_edges.get(e["from"], 0) + 1
        for n in spec["nodes"]:
            t = n.get("type")
            if t in ("tool_call", "tool_authorization") and agent_tools:
                for tool in str(n.get("tool", "")).split("|"):
                    if tool and tool not in agent_tools:
                        err("phantom_tool", f"{spec['name']}.{n['id']}: tool {tool!r} not in agent inventory")
            if t in _EVAL_TYPES or t in ("entry", "subflow", "tool_authorization"):
                c = out_edges.get(n["id"], 0)
                if n["id"] not in spec["exits"] and c != 1:
                    err("edge_arity", f"{spec['name']}.{n['id']} ({t}) has {c} outgoing edges (need exactly 1)")
            if t == "decision":
                if out_edges.get(n["id"], 0):
                    err("edge_arity", f"{spec['name']}.{n['id']} (decision) must route via branches, not edges")
                for lab, tgt in (n.get("branches") or {}).items():
                    if tgt not in nid:
                        err("decision_branch", f"{spec['name']}.{n['id']} branch {lab!r} -> unknown {tgt!r}")

    # write-coverage + authorize->verify
    authorized: set[str] = set()
    for spec in all_specs:
        nodes = {n["id"]: n for n in spec["nodes"]}
        nxt: dict[str, list[str]] = {}
        for e in spec.get("edges", []):
            nxt.setdefault(e["from"], []).append(e["to"])
        for n in spec["nodes"]:
            if n.get("type") == "tool_authorization" and n.get("tool"):
                authorized.add(n["tool"])
                targets = [nodes.get(t) for t in nxt.get(n["id"], [])]
                if not any(t and t.get("type") == "agent_action"
                           and ("verif" in t["id"] or n["tool"] in (t.get("evaluation_prompt") or ""))
                           for t in targets):
                    warn("authorize_verify", f"{spec['name']}.{n['id']} authorizes {n['tool']!r} "
                                             f"but is not followed by a verify agent_action")
    for tool in sorted(write_tools - authorized):
        err("coverage", f"WRITE tool {tool!r} has no tool_authorization node")

    # composition (cycles / missing refs) + reachability
    try:
        composed = compose({"manifest": {}, "main": main_spec, "subflows": subflows})
        ids = {n["composed_id"] for n in composed["nodes"]}
        adj: dict[str, list[str]] = {}
        for e in composed["edges"]:
            adj.setdefault(e["from"], []).append(e["to"])
        for n in composed["nodes"]:
            if n.get("type") == "decision":
                adj.setdefault(n["composed_id"], []).extend((n.get("branches") or {}).values())
        # forward reach from entry
        seen, stack = set(), [composed["entry"]]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(adj.get(x, []))
        for missing in sorted(ids - seen):
            warn("reach", f"node {missing} unreachable from entry")
        # backward reach to an exit
        exits = set(composed["exits"])
        radj: dict[str, list[str]] = {}
        for src, dsts in adj.items():
            for d in dsts:
                radj.setdefault(d, []).append(src)
        can_exit, stack = set(), list(exits)
        while stack:
            x = stack.pop()
            if x in can_exit:
                continue
            can_exit.add(x)
            stack.extend(radj.get(x, []))
        for stuck in sorted(ids - can_exit):
            warn("reach", f"node {stuck} cannot reach any exit")
    except WorkflowSpecError as e:
        err("compose", f"composition failed: {e}")

    # main conventions
    decisions = [n for n in main_spec["nodes"] if n.get("type") == "decision"]
    if not decisions:
        err("main", "main has no classifier decision node")
    else:
        classify = max(decisions, key=lambda n: len(n.get("branches") or {}))
        br = classify.get("branches") or {}
        for need in ("general", "transfer"):
            if need not in br:
                warn("main", f"classifier missing {need!r} branch")
        bd = classify.get("branch_descriptions") or {}
        for lab in br:
            if lab not in bd:
                warn("main", f"classifier branch {lab!r} missing branch_descriptions entry")
    for ex in ("exit_general", "exit_transfer"):
        if ex not in main_spec.get("exits", []):
            warn("main", f"main missing conventional exit {ex!r}")
    return findings


def has_errors(findings) -> bool:
    return any(f["level"] == "ERROR" for f in findings)


def format_findings(findings) -> str:
    if not findings:
        return "No lint findings."
    return "\n".join(f"- [{f['level']}] {f['code']}: {f['msg']}" for f in findings)
