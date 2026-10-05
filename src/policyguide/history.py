"""Format the agent-visible conversation for the guide."""
from typing import List
from tau2.data_model.message import Message, SystemMessage


def _format_history_shared(messages: List[Message], full: bool = False) -> str:
    """Format conversation history for verifier prompt (shared helper).

    Verifier-view alignment: the verifier should reason from the same
    information set the agent had access to. In tau2's dual-control telecom
    domain, the orchestrator's trajectory contains user-side tool calls and
    tool results that are NEVER routed to the agent (agent only sees the
    user's natural-language messages). We filter those out here so the
    verifier evaluates the agent's decisions on the agent's evidence —
    avoiding both unfair verdicts (verifier sees ground truth the agent
    couldn't have seen) and benchmark-specific overfitting (a verifier that
    relies on user-side tool results does not generalize to deployment).

    For airline / retail (no user_tools.py), this filter is a no-op.

    """
    lines = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            continue
        role = getattr(msg, "role", "?")
        tool_calls = getattr(msg, "tool_calls", None)
        content = getattr(msg, "content", None)
        requestor = getattr(msg, "requestor", None)

        # Drop user-side tool activity — the agent never receives these.
        if tool_calls and role == "user":
            continue
        if role == "tool" and requestor == "user":
            continue

        if tool_calls:
            for tc in tool_calls:
                args_str = str(tc.arguments)
                if not full and len(args_str) > 700:
                    args_str = args_str[:700] + "..."
                lines.append(f"AGENT->TOOL: {tc.name}({args_str})")
        elif content:
            if full:
                text = content
            else:
                text = content if len(content) <= 1500 else content[:1500] + "..."
            if role == "tool":
                error = getattr(msg, "error", False)
                prefix = "TOOL_ERROR" if error else "TOOL_RESULT"
                lines.append(f"{prefix}: {text}")
            else:
                lines.append(f"{role.upper()}: {text}")
    if full:
        return "\n".join(lines)
    return "\n".join(lines[-50:])
