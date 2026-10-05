"""Surface the domain's possible tool-output enum values.

tau2 tool docstrings declare return TYPES but never enumerate the enum VALUES a
field can take (e.g. ``get_flight_status -> str`` documents "the status of the
flight" but never lists available/flying/landed/cancelled/...). Those literals
gate policy decisions, so we surface them to BOTH the graph generator
(create_workflow tool specs) and the runtime guide (policy_doc) — node criteria
can then quote the exact value (R4) and the guide can judge against it.

Purely structural introspection of the tau2 ``data_model`` — no LLM, no I/O.
"""
from __future__ import annotations

import enum
import importlib
import inspect
from typing import Literal, Union, get_args, get_origin

_DOMAIN_MODELS = {
    "airline": "tau2.domains.airline.data_model",
    "retail": "tau2.domains.retail.data_model",
    "telecom": "tau2.domains.telecom.data_model",
}


def _literal_values(ann) -> list[str] | None:
    """String values if ``ann`` is ``Literal[...]`` / ``Optional[Literal[...]]``.

    Returns None if any member is not a literal (i.e. not a pure enum field).
    """
    origin = get_origin(ann)
    if origin is Literal:
        return [str(a) for a in get_args(ann)]
    if origin is Union:
        vals: list[str] = []
        for arg in get_args(ann):
            if arg is type(None):
                continue
            sub = _literal_values(arg)
            if sub is None:
                return None
            vals.extend(sub)
        return vals or None
    return None


def extract_domain_vocab(domain: str) -> dict[str, list[str]]:
    """``{field-or-type name: ordered unique possible values}`` for the domain.

    Collects (a) ``enum.Enum`` subclasses (telecom), (b) module-level
    ``Literal`` aliases (airline/retail), and (c) pydantic model fields typed
    ``Literal``, aggregated by FIELD NAME across models — so a discriminated
    union like flight ``status`` unions to its full value set.
    """
    if domain not in _DOMAIN_MODELS:
        return {}
    mod = importlib.import_module(_DOMAIN_MODELS[domain])
    vocab: dict[str, list[str]] = {}

    def add(key: str, values: list[str]) -> None:
        bucket = vocab.setdefault(key, [])
        for v in values:
            if v not in bucket:
                bucket.append(v)

    for name, obj in vars(mod).items():
        if name.startswith("_"):
            continue
        if inspect.isclass(obj) and issubclass(obj, enum.Enum):
            add(name, [str(m.value) for m in obj])
            continue
        lit = _literal_values(obj)
        if lit:
            add(name, lit)
            continue
        fields = getattr(obj, "model_fields", None)
        if inspect.isclass(obj) and fields:
            for fname, field in fields.items():
                fl = _literal_values(field.annotation)
                if fl:
                    add(fname, fl)
    return {k: v for k, v in sorted(vocab.items()) if len(v) > 1}


def render_domain_vocab(
    vocab: dict[str, list[str]],
    *,
    heading: str = "## Domain value vocabulary (exact tool-output enum values)",
) -> str:
    """Markdown block listing each field/type and its possible values verbatim."""
    if not vocab:
        return ""
    lines = [
        heading,
        "",
        "Possible values a field/output can take, verbatim from the environment "
        "(aggregated by field name across record types). When a node gates on one "
        "of these, quote the value EXACTLY and require exact-string equality.",
        "",
    ]
    for key, values in vocab.items():
        lines.append(f"- `{key}`: " + ", ".join(f'"{v}"' for v in values))
    return "\n".join(lines) + "\n"
