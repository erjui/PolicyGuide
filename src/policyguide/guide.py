"""Run one guide generation to reconcile requests and advance workflow state.

The main runner rebuilds the prompt from the full visible conversation and the
latest summarized state record. The guide returns a node walk, authorization,
and remediation for each request; code persists the resulting state."""
from __future__ import annotations

import json
import re
import threading

from policyguide.loader import SimplifiedWorkflow
from policyguide.state import SimplifiedState

_USAGE = {
    "calls": 0,
    "cost": 0.0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "cached_prompt_tokens": 0,
    "reasoning_tokens": 0,
    "generation_time_seconds": 0.0,
}
_USAGE_LOCK = threading.Lock()


def reset_guide_usage() -> None:
    with _USAGE_LOCK:
        for key in _USAGE:
            _USAGE[key] = 0.0 if key in {"cost", "generation_time_seconds"} else 0


def get_guide_usage() -> dict:
    with _USAGE_LOCK:
        return dict(_USAGE)

def _call_metrics(resp) -> dict:
    try:
        cost = float(getattr(resp, "cost", 0.0) or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    usage = getattr(resp, "usage", None) or {}
    raw_usage = (getattr(resp, "raw_data", None) or {}).get("usage") or {}
    prompt_details = raw_usage.get("prompt_tokens_details") or {}
    completion_details = raw_usage.get("completion_tokens_details") or {}
    return {
        "cost": cost,
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "cached_prompt_tokens": int(prompt_details.get("cached_tokens", 0) or 0),
        "reasoning_tokens": int(completion_details.get("reasoning_tokens", 0) or 0),
        "generation_time_seconds": float(
            getattr(resp, "generation_time_seconds", 0.0) or 0.0
        ),
    }


def _record(resp) -> dict:
    metrics = _call_metrics(resp)
    with _USAGE_LOCK:
        _USAGE["calls"] += 1
        for key, value in metrics.items():
            _USAGE[key] += value
    return metrics


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _last_balanced_obj(t: str):
    """(start, end) of the LAST top-level balanced {...} in `t`, string-aware so braces
    inside JSON string values don't unbalance the scan. None if there is no object."""
    depth = 0; start = None; last = None
    in_str = False; esc = False
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    last = (start, i + 1)
    return last


def _extract_output(content: str):
    """Split the guide's reply into (reasoning_text, data). The guide reasons in plain
    text first and ENDS with a fenced ```json block (the only thing parsed). Scan from the
    END — the reasoning preamble may itself contain braces in quoted criteria, so first-
    match must not be used: prefer the LAST fenced block that parses, then the last balanced
    {...}. `reasoning_text` is everything before the parsed JSON."""
    text = content or ""
    for m in reversed(list(_FENCE_RE.finditer(text))):
        try:
            data = json.loads(m.group(1).strip())
            return text[:m.start()].strip(), data
        except Exception:
            continue
    obj = _last_balanced_obj(text)
    if obj:
        s, e = obj
        try:
            return text[:s].strip(), json.loads(text[s:e])
        except Exception:
            pass
    return text.strip(), {}


def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "yes", "1"))


def _memory_text(resp, out: dict, memory_mode: str) -> str:
    """The text stored as this turn's memory, per memory_mode: the full raw reply, or the
    Part-2 JSON as a LABELLED state record (json drops the `summary`, summary keeps it). The
    label keeps the guide in its PART 1 + PART 2 contract rather than imitating a bare ```json
    sample. A failed parse (empty `out`) falls back to the raw reply so nothing is lost."""
    if memory_mode == "full" or not out:
        return resp.content or ""
    payload = dict(out)
    if memory_mode == "json":
        payload.pop("summary", None)   # ablation control: no reasoning recap carried
    return ("[STATE RECORD — your tracked state carried by the runtime, not an output "
            "sample; next turn still write PART 1 reasoning then the PART 2 JSON]\n"
            "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")


def run_guide_turn(workflow: SimplifiedWorkflow, state: SimplifiedState,
                   trajectory: list, model: str, seed: int = 300,
                   memory_mode: str = "full", prompt_mode: str = "append") -> dict:
    """Drive the guide for one agent turn in a SINGLE generation. Mutates `state`;
    returns a record for logging + delivery: {intents, directive, transfer, open_tools,
    reasoning, summary}. ``memory_mode`` (full | json | summary) sets what each turn's
    memory record contains; ``prompt_mode`` sets how it is carried — ``append`` (the
    persistent append-only chat: a per-turn delta + the memory record appended, prefix-
    cached) or ``flat`` (a stateless rebuild each turn: static prefix + the FULL conversation
    + the carried memory + the latest rich state, no per-turn marker/delta)."""
    from tau2.data_model.message import SystemMessage, UserMessage, AssistantMessage
    from tau2.utils.llm_utils import generate
    from policyguide.history import _format_history_shared

    state.turn += 1
    prev_nodes = {i: r.get("node") for i, r in state.intents.items()}

    if prompt_mode == "flat":
        # STATELESS REBUILD: one fresh [System, User] prompt each turn = static prefix + the
        # FULL conversation + the carried memory (prior records) + the latest rich state. No
        # "=== AGENT TURN ===" marker, no delta framing — the whole conversation is re-read.
        # Drops the append-only thread's superseded state snapshots; ordering keeps the growing
        # conversation FIRST and the volatile state LAST (caching impact measured on the smoke).
        full_conv = _format_history_shared(list(trajectory), full=True).strip() or "(no messages yet)"
        recs = state.records or []
        # Carried memory is BOUNDED to the LATEST record — a full snapshot that supersedes all
        # prior turns (Markovian), so older records add nothing. Accumulating them would put a
        # GROWING block after the (cached) conversation, which re-bills in full every turn and
        # breaks the prefix cache; keeping only the latest leaves a small volatile tail, so the
        # prompt caches like the append-only thread. ("new carried memory" = the new output,
        # which replaces — not appends to — the old.)
        latest = recs[-1] if recs else state.as_json()
        user_content = ("Full conversation so far:\n" + full_conv + "\n\n"
                        "Carried memory — your latest state record (supersedes all prior turns):\n" + latest)
        msgs = [SystemMessage(role="system", content=workflow.system_prompt),
                UserMessage(role="user", content=user_content)]
        resp = generate(model=model, messages=msgs, temperature=0.0, seed=seed, call_name="guide_turn")
        call_metrics = _record(resp)
        if not (resp.content or "").strip():
            resp.content = "{}"
        reasoning_text, out = _extract_output(resp.content or "")
        out = out if isinstance(out, dict) else {}
        state.records.append(_memory_text(resp, out, memory_mode))
    else:
        # APPEND-ONLY chat: one persistent conversation per task. Each turn appends the new
        # conversation delta + current state JSON at the tail, then the guide's memory record
        # (per memory_mode), so the whole prior thread stays a stable prefix the provider caches.
        if not state.chat:
            state.chat = [SystemMessage(role="system", content=workflow.system_prompt)]
        delta_msgs = trajectory[state.hist_cursor:]
        state.hist_cursor = len(trajectory)
        delta = _format_history_shared(list(delta_msgs), full=True).strip() or "(no new messages)"
        msg = (f"=== AGENT TURN {state.turn} ===\n"
               f"New conversation since your last update:\n{delta}\n\n"
               f"Current tracked state (authoritative):\n{state.as_json()}")
        state.chat.append(UserMessage(role="user", content=msg))
        resp = generate(model=model, messages=state.chat, temperature=0.0, seed=seed,
                        call_name="guide_turn")
        call_metrics = _record(resp)
        if not (resp.content or "").strip():
            resp.content = "{}"   # keep the chat valid (every message needs content)
        reasoning_text, out = _extract_output(resp.content or "")
        out = out if isinstance(out, dict) else {}
        if memory_mode == "full" or not out:
            state.chat.append(resp)
        else:
            state.chat.append(AssistantMessage(role="assistant",
                                               content=_memory_text(resp, out, memory_mode)))

    reconcile = out.get("reconcile") if isinstance(out, dict) else None
    reconcile = reconcile if isinstance(reconcile, dict) else {}
    traverse = out.get("traverse") if isinstance(out, dict) else None
    traverse = traverse if isinstance(traverse, list) else []
    transfer = _truthy(out.get("transfer")) if isinstance(out, dict) else False

    # Merge the two steps into the flat roster the code-level state folds in: the
    # reconcile roster supplies id/request/intent, the traverse supplies each intent's
    # advanced node/status/authorize_tool + its directive.
    rec_by_id = {str(r.get("id")): r for r in (reconcile.get("intents") or [])
                 if isinstance(r, dict) and r.get("id")}
    roster: list[dict] = []
    directives: dict[str, str] = {}
    seen: set[str] = set()
    for tv in traverse:
        if not isinstance(tv, dict) or not tv.get("id"):
            continue
        iid = str(tv["id"]); seen.add(iid)
        rec = rec_by_id.get(iid, {})
        roster.append({"id": iid, "request": rec.get("request", ""),
                       "intent": rec.get("intent", ""), "node": tv.get("node"),
                       "status": tv.get("status", "open"),
                       "authorize_tool": tv.get("authorize_tool")})
        d = (tv.get("directive") or "").strip()
        if d and str(tv.get("status", "open")).strip().lower() != "done":
            directives[iid] = d
    # A reconciled intent the guide forgot to traverse: keep it (enters at the graph entry).
    for iid, rec in rec_by_id.items():
        if iid not in seen:
            roster.append({"id": iid, "request": rec.get("request", ""),
                           "intent": rec.get("intent", ""), "node": "",
                           "status": "open", "authorize_tool": None})

    # transfer_to_human_agents is the transfer SIGNAL, so a directive instructing it must set the
    # structured transfer flag the gate keys off — couple them here the way a WRITE directive sets
    # authorize_tool, so the guide's prose directive and the flag can never decouple (a flag-less
    # transfer directive otherwise dead-locks against the transfer-gate).
    if not transfer and any("transfer_to_human_agents" in (d or "") for d in directives.values()):
        transfer = True
    state.apply_guide_output(workflow, roster, prev_nodes, directives, transfer)
    directive = _assemble_directive(state, directives)

    return {"intents": state.intents_snapshot(),
            "directive": "" if state.transferred else directive,
            "transfer": state.transferred,
            "open_tools": sorted(state.open_tools),
            "reasoning": reasoning_text,
            "summary": out.get("summary") if isinstance(out, dict) else None,
            "call_metrics": call_metrics,
            "reconcile": reconcile, "traverse": traverse}


def _assemble_directive(state: SimplifiedState, directives: dict) -> str:
    """One directive verbatim when a single intent is blocked; a labelled list when
    several are blocked at once (empty when transferring — the patch substitutes the
    transfer directive)."""
    if state.transferred:
        return ""
    items = [(iid, d) for iid, d in directives.items() if d]
    if not items:
        return ""
    if len(items) == 1:
        return items[0][1]
    return "Handle each open request:\n" + "\n".join(
        f"- For '{state.intents.get(iid, {}).get('request', iid)}': {d}" for iid, d in items)
