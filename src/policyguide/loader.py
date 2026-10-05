"""Load + compose a workflow into the flat structures the simplified runtime needs.

Reuses ``policyguide.workflow_loader`` (the schema contract + subflow inlining) and
renders the static prompt docs (graph + tool inventory) once per registry, so each
turn's guide call only varies the dynamic state + history (cacheable static prefix).
"""
from __future__ import annotations

from pathlib import Path

from policyguide.workflow_loader import load_and_compose, iter_mutating_tools
from policyguide.prompt import SYSTEM_PROMPT, TURN_TASK


class SimplifiedWorkflow:
    """Composed graph + pre-rendered prompt docs for one domain."""

    def __init__(self, workflow_dir: Path, domain: str, policy_doc: str = "",
                 max_strikes: int = 5):
        self.manifest, self.composed = load_and_compose(Path(workflow_dir), domain)
        self.domain = domain
        self.policy_doc = policy_doc or "(policy provided in the agent system prompt)"
        self.nodes = {n["composed_id"]: n for n in self.composed["nodes"]}
        self.entry = self.composed["entry"]
        # Escalation cap: parked > N turns on one node -> transfer. Fixed at 5 (the old
        # subflow cap where the real stalls happened); the composed value (15, from main)
        # was too generous and let stalls run to user_stop.
        self.max_strikes = max_strikes

        # Outgoing edges per node (id -> [next ids]).
        self.edges: dict[str, list[str]] = {}
        for e in self.composed["edges"]:
            self.edges.setdefault(e["from"], []).append(e["to"])

        # Tool inventory derived from the graph.
        self.write_tools = sorted(set(iter_mutating_tools(self.composed)))
        self.read_tools = sorted({
            n["tool"] for n in self.composed["nodes"]
            if n.get("type") == "tool_call" and n.get("tool")
        })
        # Which authorization node authorizes which WRITE tool (for reference / gate sanity).
        self.authz_node_tool = {
            cid: n["tool"] for cid, n in self.nodes.items()
            if n.get("type") == "tool_authorization" and n.get("tool")
        }

        self.graph_doc = self._render_graph_doc()
        self.tools_doc = self._render_tools_doc() + self._render_value_dict()
        # Static system prefix for the guide chat — built once, cached for the whole
        # task (no dynamic state in it; state lives only in appended messages). Use
        # str.replace (NOT str.format) so the literal JSON braces in the prompt's
        # OUTPUT CONTRACT pass through verbatim.
        self.system_prompt = (SYSTEM_PROMPT
                              .replace("{policy_doc}", self.policy_doc)
                              .replace("{graph_doc}", self.graph_doc)
                              .replace("{tools_doc}", self.tools_doc)
                              + "\n\n## Each turn\n" + TURN_TASK)

    # ── graph-walk helpers (code drives traversal; the guide only judges one node) ──

    def node(self, cid: str) -> dict:
        return self.nodes.get(cid) or {}

    def advance(self, cid: str):
        """The single successor of a non-decision node (None if terminal)."""
        nxt = self.edges.get(cid)
        return nxt[0] if nxt else None

    def is_decision(self, cid: str) -> bool:
        return self.node(cid).get("type") == "decision"

    def is_terminal(self, cid: str) -> bool:
        """A node with no successor and no branches — the walk stops here (done)."""
        n = self.node(cid)
        return bool(n) and not self.edges.get(cid) and n.get("type") != "decision"

    def is_structural(self, cid: str) -> bool:
        """A pass-through node (entry / dissolved-subflow exit) the code skips without
        asking the guide — it carries no expectation to judge but still routes onward."""
        n = self.node(cid)
        return n.get("type") in ("entry", "exit", "subflow") and bool(self.edges.get(cid))

    def branches(self, cid: str) -> dict:
        return self.node(cid).get("branches") or {}

    def branch_target(self, cid: str, label):
        br = self.branches(cid)
        if label in br:
            return br[label]
        for k, v in br.items():               # tolerate case / whitespace drift
            if str(k).strip().lower() == str(label).strip().lower():
                return v
        return None

    def node_spec(self, cid: str) -> str:
        """One-line node spec for the per-node guide question (expectation + criteria)."""
        n = self.node(cid)
        parts = []
        exp = (n.get("expectation") or n.get("description") or "").strip()
        ev = (n.get("evaluation_prompt") or "").strip()
        if exp:
            parts.append(exp)
        if ev:
            parts.append("Evaluation criteria: " + ev)
        return " ".join(parts).replace("\n", " ") or "(no expectation recorded)"

    def _render_graph_doc(self) -> str:
        """Two parts: (1) the graph STRUCTURE — compact topology of
        ids, types, edges, and decision branches; (2) the NODE SPECS — each node's short
        description (expectation) and satisfying condition (evaluation_prompt). Splitting
        them keeps the topology scannable instead of burying it under paragraph-long
        node text on every edge line."""
        struct = ["### Graph structure (topology: entry node, edges, decision branches)",
                  f"entry: {self.entry}"]
        specs = ["### Node specs (each node — what it expects, and when it is satisfied)"]
        for cid, n in self.nodes.items():
            t = n.get("type")
            tool = f" tool={n['tool']}" if n.get("tool") else ""
            if t == "decision":
                struct.append(f"- {cid} [decision]:")
                for lab, tgt in (n.get("branches") or {}).items():
                    struct.append(f"    {lab} -> {tgt}")
            else:
                nxt = ", ".join(self.edges.get(cid, []))
                struct.append(f"- {cid} [{t}{tool}]" + (f" -> {nxt}" if nxt else "  (terminal)"))
            exp = (n.get("expectation") or n.get("description") or "").strip().replace("\n", " ")
            ev = (n.get("evaluation_prompt") or "").strip().replace("\n", " ")
            if exp or ev:
                specs.append(f"- {cid} [{t}{tool}]")
                if exp:
                    specs.append(f"    Expects: {exp}")
                if ev:
                    specs.append(f"    Satisfied when: {ev}")
        return "\n".join(struct) + "\n\n" + "\n".join(specs)

    def _render_tools_doc(self) -> str:
        w = ", ".join(self.write_tools) or "(none)"
        r = ", ".join(self.read_tools) or "(none)"
        return f"WRITE (need authorization): {w}\nREAD (free lookups): {r}"

    def _render_value_dict(self) -> str:
        """Closed value vocabularies the environment uses — every short-string field whose
        values form a small fixed set the agent must match EXACTLY (airport codes, statuses,
        cabin classes, …). The guide otherwise has no way to know e.g. that a place name
        resolves to a specific code, so it lets the agent pass an invalid identifier.

        Excludes free-form per-record values that merely happen to be short and low-cardinality
        in this (synthetic) db: any field whose values look like date/time literals, and
        personal-name fields (the key names a person and the values are proper-noun-cased).
        Those are user-supplied values the agent learns from the conversation, not an
        environment-controlled enum — handing them to the guide as ground truth leaks task
        data. Place names (e.g. city) stay: a served-network set, not per-customer PII.
        Best-effort over the domain db; never fatal."""
        import os, re, json as _json
        from collections import defaultdict
        try:
            data_dir = os.environ.get("TAU2_DATA_DIR", "")
            db = _json.load(open(Path(data_dir) / "tau2" / "domains" / self.domain / "db.json"))
        except Exception:
            return ""
        def _is_datetime(v: str) -> bool:
            return bool(re.match(r"\d{4}-\d\d-\d\d", v) or re.match(r"\d\d:\d\d", v))
        def _is_name(v: str) -> bool:
            return v.isalpha() and v[:1].isupper() and v[1:].islower()
        def _drop(key: str, s: set) -> bool:
            half = len(s) // 2
            if sum(_is_datetime(x) for x in s) > half:
                return True
            return "name" in key.lower() and sum(_is_name(x) for x in s) > half
        vals: dict = defaultdict(set)
        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if isinstance(v, str) and 0 < len(v) <= 16 and " " not in v:
                        vals[k].add(v)
                    else:
                        walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(db)
        lines = [f"- `{k}`: {', '.join(sorted(s))}"
                 for k, s in sorted(vals.items())
                 if 1 < len(s) <= 40 and not _drop(k, s)]
        if not lines:
            return ""
        return ("\n\n## Closed value vocabularies (every id/value the agent passes for these "
                "fields must be EXACTLY one of these)\n" + "\n".join(lines))
