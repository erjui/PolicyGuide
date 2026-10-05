"""PolicyGuide agent with cumulative developer-role remediation.

The orchestrator sets ``_pending_guidance`` before an agent turn. Incoming
messages are appended before guidance so tool-call/result pairing is preserved.
Guidance stays in the agent state and is never sent to the simulated user.
A small message converter adds developer-role support to tau2's wire format."""
from __future__ import annotations

import os
from typing import Literal, Optional

from tau2.agent.llm_agent import LLMAgent
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    SystemMessage,
    UserMessage,
)
from tau2.utils import llm_utils
from tau2.utils.llm_utils import generate


# Canonical guidance prefix. Imported by patch.py so the trajectory marker and the
# agent-facing message share one string (and prior-guidance stripping stays in sync).
GUIDANCE_PREFIX = "[POLICY GUIDANCE] Follow this policy-workflow guidance for your next step:"


class DeveloperMessage(SystemMessage):
    """A developer-role instruction message.

    Subclasses ``SystemMessage`` so tau2's ``validate_message`` (which requires
    system messages to carry content) accepts it unchanged. The wire role is
    overridden to ``developer``; ``to_litellm_messages`` is patched below to emit
    it as ``{"role": "developer", ...}`` instead of ``system``.
    """

    role: Literal["developer"] = "developer"


def _install_developer_role_converter() -> None:
    """Teach tau2's ``to_litellm_messages`` to emit ``role="developer"`` for
    ``DeveloperMessage`` (idempotent). Without this, a ``DeveloperMessage`` matches
    the ``SystemMessage`` isinstance branch and would be sent as ``system``.
    """
    if getattr(llm_utils.to_litellm_messages, "_policyguide_dev_role", False):
        return

    _orig = llm_utils.to_litellm_messages

    def _to_litellm_with_developer(messages):
        out = []
        for m in messages:
            if isinstance(m, DeveloperMessage):
                out.append({"role": "developer", "content": m.content})
            else:
                out.extend(_orig([m]))
        return out

    _to_litellm_with_developer._policyguide_dev_role = True
    llm_utils.to_litellm_messages = _to_litellm_with_developer


_install_developer_role_converter()


def _is_guidance(msg) -> bool:
    # Match guidance whether stored as the new developer-role message or (legacy /
    # trajectory-side) an assistant message, so trajectory markers can be removed without duplicating directives.
    return (
        isinstance(msg, (DeveloperMessage, AssistantMessage))
        and (getattr(msg, "content", None) or "").startswith(GUIDANCE_PREFIX)
    )


class PolicyGuideLLMAgent(LLMAgent):
    """LLMAgent that injects the current policy directive as a developer-role
    instruction turn instead of a trailing assistant message.

    ``_pending_guidance`` is set by the Orchestrator patch each turn (to the directive
    string, or ``None`` when there is no guidance). Delivery rules:
      * the incoming message is appended first → tool_call/tool_result pairing intact;
      * previous developer guidance is retained by default; duplicate trajectory markers are stripped;
      * the directive is appended as a single ``DeveloperMessage`` so the model treats
        it as an authoritative instruction to act on (not a prefill to continue).
    """

    _pending_guidance: Optional[str] = None

    def _generate_next_message(self, message, state):
        if isinstance(message, UserMessage) and getattr(message, "is_audio", False):
            raise ValueError("User message cannot be audio. Use VoiceLLMAgent instead.")

        # 1) Append the incoming message exactly as the base agent would.
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)

        # 2) Guidance handling. Default = CUMULATIVE: keep prior developer-role directives so the
        #    agent sees ALL past guidance, and only strip the trajectory-side AssistantMessage
        #    markers (avoid duplicating each directive). POLICYGUIDE_CUMULATIVE=0 restores tau2's
        #    original strip-all (current-directive-only) behavior.
        if os.environ.get("POLICYGUIDE_CUMULATIVE", "1") != "0":
            state.messages = [m for m in state.messages
                              if not (isinstance(m, AssistantMessage) and _is_guidance(m))]
        else:
            state.messages = [m for m in state.messages if not _is_guidance(m)]

        # 3) Inject the current directive as a developer-role instruction turn.
        directive = getattr(self, "_pending_guidance", None)
        if directive:
            state.messages.append(
                DeveloperMessage(content=f"{GUIDANCE_PREFIX}\n\n{directive}")
            )

        messages = state.system_messages + state.messages
        return generate(
            model=self.llm,
            tools=self.tools,
            messages=messages,
            call_name="agent_response",
            **self.llm_args,
        )


def create_policyguide_llm_agent(tools, domain_policy, **kwargs):
    """Factory for ``PolicyGuideLLMAgent`` (mirrors ``create_llm_agent``)."""
    return PolicyGuideLLMAgent(
        tools=tools,
        domain_policy=domain_policy,
        llm=kwargs.get("llm"),
        llm_args=kwargs.get("llm_args"),
    )
