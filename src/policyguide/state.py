"""Code-level intent state for the simplified runtime.

The guide is driven step by step; THIS class is the authoritative, code-inspectable
record that the runtime mutates between steps and hands back to the guide. It owns:

  * the **guide chat** — one persistent conversation per task (`chat`). The static
    prefix is cached for the whole task; only the per-turn conversation delta + the
    per-step questions are appended, with the dynamic state always at the tail.
  * the **history cursor** — how much of the agent/user trajectory the guide has
    already been shown, so each turn appends only the new delta (cache-friendly).
  * the **tool gate** — `open_tools`, the WRITE tools currently authorized to fire.
    A tool stays authorized (`authorized_tool` on the intent) from when its
    authorization node passes until its verification node passes.
  * the **N-strikes escalation** — if the guide keeps an intent parked on the same
    node for more than ``max_strikes`` turns, force a transfer (computed here, never
    trusted to the LLM, so a stuck loop can never run away).
"""
from __future__ import annotations

import json
from typing import Optional


class SimplifiedState:
    def __init__(self, max_strikes: int = 5):
        self.max_strikes = max_strikes
        self.turn = 0
        self.intents: dict[str, dict] = {}     # id -> {request,intent,node,status,authorized_tool,directive}
        self._streak: dict[str, int] = {}      # id -> consecutive frozen-loop turns
        self._last_directive: dict[str, str] = {}  # id -> prior turn's directive (loop detection)
        self.open_tools: set[str] = set()       # WRITE tools authorized right now
        self.transferred = False
        self.forced_transfer_reason: Optional[str] = None
        # Persistent guide conversation + how far the trajectory has been narrated.
        self.chat: list = []
        self.hist_cursor = 0
        # Flat-rebuild prompt mode: the guide's prior per-turn memory records (text), carried
        # forward as one block instead of the append-only `chat` (see guide.run_guide_turn).
        self.records: list[str] = []

    # ── state shown to the guide (always at the tail of an appended message) ──
    def as_json(self) -> str:
        if not self.intents:
            return "[]"
        return json.dumps([
            {"id": i, "request": r.get("request", ""), "intent": r.get("intent", ""),
             "node": r.get("node", ""), "status": r.get("status", "open"),
             "authorized_tool": r.get("authorized_tool")}
            for i, r in self.intents.items()
        ], ensure_ascii=False)

    def open_ids(self) -> list[str]:
        return [i for i, r in self.intents.items() if r.get("status") != "done"]

    def intents_snapshot(self) -> list[dict]:
        """The current intent records — for logging / delivery."""
        return [
            {"id": i, "request": r.get("request", ""), "intent": r.get("intent", ""),
             "node": r.get("node", ""), "status": r.get("status", "open"),
             "authorize_tool": r.get("authorized_tool")}
            for i, r in self.intents.items()
        ]

    # ── fold the guide's single-generation output into the code-level state ──
    def apply_guide_output(self, workflow, roster: list, prev_nodes: dict[str, str],
                           directives: dict[str, str], transfer: bool) -> None:
        """`roster` is the guide's COMPLETE updated state machine (reconcile + traverse
        already done in the one generation): one entry per open intent at its advanced
        node, with `authorize_tool` set where prerequisites are met. `directives` maps an
        intent id to the directive the guide wrote for it (only for blocked intents).

        Code re-derives only what it must not trust to the LLM text: the tool gate
        (`open_tools` from authorized WRITE tools), the per-intent no-progress streak,
        and the N-strikes forced transfer. Any intent omitted by the guide is dropped
        (closed / merged); a node id the guide returns that is not in the graph falls
        back to the intent's previous node (or the entry).
        """
        write_tools = set(getattr(workflow, "write_tools", []) or [])
        new_intents: dict[str, dict] = {}
        new_streak: dict[str, int] = {}
        new_last_directive: dict[str, str] = {}
        open_tools: set[str] = set()

        for rec in (roster or []):
            if not isinstance(rec, dict):
                continue
            iid = str(rec.get("id") or rec.get("intent") or rec.get("request") or "intent")
            node = str(rec.get("node") or "").strip()
            if not workflow.node(node):
                node = prev_nodes.get(iid) or workflow.entry
            status = "done" if str(rec.get("status", "open")).strip().lower() == "done" else "open"
            tool = rec.get("authorize_tool", rec.get("authorized_tool"))
            tool = tool.strip() if isinstance(tool, str) else None
            authorized = tool if tool in write_tools else None

            new_intents[iid] = {
                "request": rec.get("request", ""), "intent": rec.get("intent", ""),
                "node": node, "status": status, "authorized_tool": authorized}

            advanced = (iid not in prev_nodes) or (prev_nodes.get(iid) != node) or status == "done"
            # A strike counts only on a FROZEN loop — the same node AND the same directive
            # as last turn. A changing directive on an unchanged node is normal progress
            # (e.g. gathering inputs one at a time at a collect node), not a stall.
            d = (directives.get(iid) or "").strip()
            frozen = (not advanced) and status == "open" and d and d == self._last_directive.get(iid)
            new_streak[iid] = self._streak.get(iid, 0) + 1 if frozen else 0
            new_last_directive[iid] = d
            if authorized and status != "done":
                open_tools.add(authorized)
            if new_streak[iid] > self.max_strikes:
                self.forced_transfer_reason = (
                    f"request {iid!r} stuck on node {node!r} repeating the same directive "
                    f"for {new_streak[iid]} turns")

        self.intents = new_intents
        self._streak = new_streak
        self._last_directive = new_last_directive
        self.open_tools = open_tools
        if transfer or self.forced_transfer_reason:
            self.transferred = True

    def is_tool_open(self, tool_name: str) -> bool:
        return tool_name in self.open_tools
