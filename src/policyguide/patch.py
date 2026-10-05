"""Integrate PolicyGuide with the tau2 orchestrator.

The main runner guides user turns and intercepts the first unauthorized write
in each user-turn region to request corrective guidance. Immediate write
retries are allowed. Unauthorized transfers also trigger corrective guidance.
State belongs to each orchestrator instance."""
from __future__ import annotations

import json
from typing import Optional

from tau2.data_model.message import (
    AssistantMessage, MultiToolMessage, ToolMessage, UserMessage,
)
from tau2.orchestrator.orchestrator import Orchestrator, Role

from policyguide.agent import GUIDANCE_PREFIX
from policyguide.guide import run_guide_turn
from policyguide.loader import SimplifiedWorkflow
from policyguide.state import SimplifiedState

_TRANSFER_TOOL = "transfer_to_human_agents"
_TRANSFER_DIRECTIVE = (
    "This request cannot be completed within policy. Transfer the user to a human "
    "agent now: first call transfer_to_human_agents, then send the message "
    "'YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON.'")


def _agent_has_transferred(trajectory) -> bool:
    for msg in trajectory:
        if getattr(msg, "role", None) != "assistant":
            continue
        for tc in (getattr(msg, "tool_calls", None) or []):
            if getattr(tc, "name", None) == _TRANSFER_TOOL:
                return True
    return False


def _get_state(self, max_strikes: int) -> SimplifiedState:
    st = getattr(self, "_pg_state", None)
    if st is None:
        st = self._pg_state = SimplifiedState(max_strikes=max_strikes)
    return st


def patch_orchestrator_simplified(workflow: SimplifiedWorkflow, *, guide_model: str,
                                  seed: int = 300, enforce: bool = False,
                                  guide_log_path: Optional[str] = None,
                                  debug_log_path: Optional[str] = None,
                                  user_turn_only: bool = False,
                                  memory_mode: str = "full",
                                  prompt_mode: str = "append",
                                  write_gate: bool = False):
    original_step = Orchestrator.step
    write_tools = set(workflow.write_tools)

    def _log(path: Optional[str], entry: dict):
        if not path:
            return
        with open(path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def _gate_block(self) -> bool:
        """AGENT->ENV: block an unauthorized CONSEQUENTIAL call BEFORE it executes, and flag a forced
        guide fire so the agent is RE-STEERED on the forthcoming turn (not just handed a static error).
        Gated: transfer_to_human_agents ALWAYS (the guide must have signalled transfer) — closes the
        agent self-transferring while unguided under user_turn_only; mutating WRITES only under enforce
        (advisory by default — hard write-gating can false-block a correct write whose authorize_tool the
        guide momentarily omits). The forced re-fire is what makes the block corrective rather than a
        dead-end loop: a blocked call re-invokes the guide, which can authorize it if it was actually
        right, or steer the agent to the real next step if it was not."""
        if not (self.from_role == Role.AGENT and self.to_role == Role.ENV
                and self.message.is_tool_call()):
            return False
        st = getattr(self, "_pg_state", None)
        open_tools = st.open_tools if st else set()
        transfer_ok = bool(st and st.transferred)
        write_gate_armed = bool(getattr(self, "_pg_write_gate_armed", True))

        def _unauthorized(tc):
            if tc.requestor != "assistant":
                return False
            if tc.name == _TRANSFER_TOOL:
                return not transfer_ok
            if tc.name in write_tools and tc.name not in open_tools:
                if enforce:
                    return True                      # hard gate: block every time
                if write_gate and write_gate_armed:  # soft gate: block ONCE to force a corrective fire
                    return True
            return False

        blocked = next((tc for tc in self.message.tool_calls if _unauthorized(tc)), None)
        if blocked is None:
            return False
        # Soft write-gate is one-shot per user-turn region: disarm now so the immediate re-attempt
        # (after the corrective guide fire) is allowed through — advisory, never a dead-end.
        if write_gate and not enforce and blocked.name != _TRANSFER_TOOL:
            self._pg_write_gate_armed = False
        _log(debug_log_path, {"event": "tool_gate_block", "task_id": str(self.task.id),
                              "tool": blocked.name, "transfer_ok": transfer_ok,
                              "open_tools": sorted(open_tools)})
        self.trajectory.pop()  # drop the AssistantMessage carrying the blocked call
        content = (f"This action was blocked: the policy workflow has not authorized "
                   f"'{blocked.name}' yet. Do NOT retry it now — follow the next guidance for the "
                   f"correct step (gather the missing prerequisite, or continue the workflow).")
        tool_msgs = [ToolMessage(id=tc.id, role="tool", requestor=tc.requestor, error=True,
                                 content=(content if tc is blocked else
                                          "Not executed: another call was blocked by policy enforcement."))
                     for tc in self.message.tool_calls]
        self.message = (MultiToolMessage(role="tool", tool_messages=tool_msgs)
                        if len(tool_msgs) > 1 else tool_msgs[0])
        self.to_role = Role.AGENT
        self.from_role = Role.ENV
        self.step_count += 1
        self.environment.sync_tools()
        self._pg_force_fire = True  # re-steer on the forthcoming agent turn, bypassing the user-only skip
        return True

    def patched_step(self):
        if _gate_block(self):
            return
        if self.to_role != Role.AGENT:
            original_step(self)
            return

        # A genuine USER message opens a fresh write-decision region: re-arm the one-shot soft gate
        # so the next unguided write in this region gets one corrective guide fire before it commits.
        if self.from_role == Role.USER:
            self._pg_write_gate_armed = True

        agent = getattr(self, "agent", None)
        if agent is not None:
            agent._pending_guidance = None
        # A gate-block just forced a re-steer: fire the guide for a corrective directive even under
        # user-only firing (consumed once).
        force_fire = bool(getattr(self, "_pg_force_fire", False))
        self._pg_force_fire = False
        # USER-ONLY firing: skip the guide on ENV->AGENT (tool-result) turns. The guide is not
        # blind to the skipped reads — state.hist_cursor folds every skipped tool result into the
        # next USER-turn delta — but it issues no fresh directive here, so the agent proceeds on its
        # accumulated prior guidance plus the new tool result. _pending_guidance was reset above, so
        # no stale directive is re-injected. EXCEPTION: a gate-block forces a fire (force_fire) so the
        # agent that just tried an unauthorized transfer/write is re-steered, not left to loop.
        if user_turn_only and self.from_role == Role.ENV and not force_fire:
            original_step(self)
            return
        if _agent_has_transferred(self.trajectory):
            original_step(self)
            return

        task_id = str(self.task.id)
        state = _get_state(self, workflow.max_strikes)

        try:
            guide = run_guide_turn(workflow, state, list(self.trajectory), guide_model, seed,
                                   memory_mode=memory_mode, prompt_mode=prompt_mode)
        except Exception as e:  # noqa: BLE001 — guidance must never break the conversation
            _log(debug_log_path, {"event": "guide_error", "task_id": task_id, "error": repr(e)})
            original_step(self)
            return

        directive = (guide.get("directive") or "").strip()
        if state.transferred:
            reason = state.forced_transfer_reason
            directive = _TRANSFER_DIRECTIVE + (f" (reason: {reason})" if reason else "")

        _log(guide_log_path, {"event": "guide_turn", "task_id": task_id, "turn": state.turn,
                              "intents": guide.get("intents"), "directive": directive,
                              "transfer": state.transferred, "open_tools": sorted(state.open_tools),
                              "reasoning": guide.get("reasoning"), "summary": guide.get("summary"),
                              "call_metrics": guide.get("call_metrics"),
                              "reconcile": guide.get("reconcile"), "traverse": guide.get("traverse")})

        if agent is not None and directive:
            agent._pending_guidance = directive
        if directive:
            self.trajectory.append(AssistantMessage(
                role="assistant", content=f"{GUIDANCE_PREFIX}\n\n{directive}", cost=0.0))
        original_step(self)

    Orchestrator.step = patched_step
