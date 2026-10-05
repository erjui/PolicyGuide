"""Deterministic tool-spec extraction from tau2 (no LLM call).

Uses the environment's own @is_tool(ToolType.WRITE/READ/GENERIC) annotations,
so mutating-tool classification needs no model call.
"""
from __future__ import annotations

import inspect
TOOL_ATTR = "__tool__"
TOOL_TYPE_ATTR = "__tool_type__"


def _tools_class(domain: str):
    if domain == "airline":
        from tau2.domains.airline.tools import AirlineTools
        return AirlineTools
    if domain == "retail":
        from tau2.domains.retail.tools import RetailTools
        return RetailTools
    if domain == "telecom":
        from tau2.domains.telecom.tools import TelecomTools
        return TelecomTools
    raise ValueError(f"Unknown domain: {domain!r}")


def extract_tools(domain: str) -> list[dict]:
    """[{name, signature, doc, tool_type}] for every annotated tool, sorted by name."""
    cls = _tools_class(domain)
    out = []
    for _, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
        if not getattr(fn, TOOL_ATTR, None):
            continue
        ttype = getattr(fn, TOOL_TYPE_ATTR, None)
        ttype_name = getattr(ttype, "name", str(ttype) if ttype else "UNKNOWN")
        sig = str(inspect.signature(fn)).replace("(self, ", "(").replace("(self)", "()")
        out.append({
            "name": fn.__name__,
            "signature": sig,
            "doc": inspect.getdoc(fn) or "",
            "tool_type": ttype_name,
        })
    return sorted(out, key=lambda t: t["name"])


def extract_user_tools(domain: str) -> list[dict]:
    """User-side device tools (end-user actions), or [] if the domain has none.

    These are NOT callable by the agent — the generator must model steps that
    involve them as agent_action nodes instructing the user.
    """
    import importlib
    try:
        mod = importlib.import_module(f"tau2.domains.{domain}.user_tools")
    except ModuleNotFoundError:
        return []
    import inspect as _inspect
    out = []
    for _, cls in _inspect.getmembers(mod, predicate=_inspect.isclass):
        if cls.__module__ != mod.__name__:
            continue
        for _, fn in _inspect.getmembers(cls, predicate=_inspect.isfunction):
            if not getattr(fn, TOOL_ATTR, None):
                continue
            sig = str(_inspect.signature(fn)).replace("(self, ", "(").replace(
                "(self)", "()")
            out.append({"name": fn.__name__, "signature": sig,
                        "doc": _inspect.getdoc(fn) or "",
                        "tool_type": "USER"})
    return sorted(out, key=lambda t: t["name"])


def render_user_tool_specs(tools: list[dict]) -> str:
    """Markdown section for user-side tools (empty string if none)."""
    if not tools:
        return ""
    lines = [
        "## USER-SIDE DEVICE TOOLS (the agent CANNOT call these)",
        "",
        "The following actions can only be performed by the END USER on their "
        "own device, typically at the agent's instruction. NEVER author a "
        "`tool_call` or `tool_authorization` node for these. A step involving "
        "one is an `agent_action` node: the agent instructs the user to "
        "perform/report it, and the criterion judges the user's reported "
        "outcome in the conversation.",
        "",
    ]
    for t in tools:
        lines.append(f"### {t['name']}  —  user-side")
        lines.append(f"`{t['name']}{t['signature']}`")
        if t["doc"]:
            lines.append("")
            lines.append(t["doc"])
        lines.append("")
    return "\n".join(lines)


def classify_tools(tools: list[dict]) -> dict:
    return {
        "write": [t["name"] for t in tools if t["tool_type"] == "WRITE"],
        "read": [t["name"] for t in tools if t["tool_type"] == "READ"],
        "other": [t["name"] for t in tools if t["tool_type"] not in ("WRITE", "READ")],
    }


def render_tool_specs(tools: list[dict]) -> str:
    """Markdown block for prompts: name, type, signature, docstring."""
    lines = []
    for t in tools:
        kind = {"WRITE": "MUTATING (WRITE)", "READ": "read-only (READ)"}.get(
            t["tool_type"], t["tool_type"])
        lines.append(f"### {t['name']}  —  {kind}")
        lines.append(f"`{t['name']}{t['signature']}`")
        if t["doc"]:
            lines.append("")
            lines.append(t["doc"])
        lines.append("")
    return "\n".join(lines)
