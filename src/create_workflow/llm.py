"""OpenAI client wrapper with per-call logging, cost tracking, and a budget guard."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import BadRequestError, OpenAI

from .config import ROOT


class BudgetExceeded(RuntimeError):
    pass


class CostTracker:
    def __init__(self, budget_usd: float, log_path: Path):
        self.budget = budget_usd
        self.log_path = log_path
        self.total_usd = 0.0
        self.total_in = 0
        self.total_out = 0
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            for line in log_path.read_text().splitlines():
                entry = json.loads(line)
                self.total_usd += entry["cost_usd"]
                self.total_in += entry.get("prompt_tokens", 0)
                self.total_out += entry.get("completion_tokens", 0)

    def check_budget(self) -> None:
        if self.total_usd >= self.budget:
            raise BudgetExceeded(
                f"Estimated run cost ${self.total_usd:.4f} reached budget ${self.budget:.2f}. "
                "Resume the same --run-id with a higher --budget to continue."
            )

    def record(self, step: str, model: str, usage: dict, seconds: float) -> float:
        from litellm import cost_per_token

        cost = sum(cost_per_token(
            model=model, prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        ))
        self.total_usd += cost
        self.total_in += usage.get("prompt_tokens", 0)
        self.total_out += usage.get("completion_tokens", 0)
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "step": step, "model": model, "seconds": round(seconds, 1),
            **usage,
            "cost_usd": cost,
            "cumulative_usd": round(self.total_usd, 5),
        }
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"    [{step}] {usage.get('prompt_tokens', 0)} in / "
              f"{usage.get('completion_tokens', 0)} out — "
              f"${cost:.4f} (run total ${self.total_usd:.4f})")
        return cost

    def summary(self) -> str:
        return (f"Total: {self.total_in} in / {self.total_out} out tokens, "
                f"est. ${self.total_usd:.4f}")


def make_client() -> OpenAI:
    load_dotenv(ROOT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Set OPENAI_API_KEY in the environment or repository .env")
    return OpenAI()


def call_llm(client: OpenAI, model: str, system: str, user: str,
             temperature: float, tracker: CostTracker, step: str) -> str:
    """One generation using SDK retries; stop new calls at the estimated budget."""
    from litellm import cost_per_token

    tracker.check_budget()
    # Verify pricing is known before spending; do not guess for unknown models.
    cost_per_token(model=model, prompt_tokens=1, completion_tokens=1)
    kwargs = dict(model=model, messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=temperature)
    t0 = time.time()
    try:
        resp = client.chat.completions.create(**kwargs)
    except BadRequestError as exc:
        if "temperature" not in str(exc):
            raise
        kwargs.pop("temperature")
        resp = client.chat.completions.create(**kwargs)
    usage = {}
    if resp.usage:
        usage = {"prompt_tokens": resp.usage.prompt_tokens,
                 "completion_tokens": resp.usage.completion_tokens,
                 "total_tokens": resp.usage.total_tokens}
    tracker.record(step, model, usage, time.time() - t0)
    return resp.choices[0].message.content or ""
