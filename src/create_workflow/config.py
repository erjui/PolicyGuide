"""Repository-relative paths and policy inputs for workflow generation."""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
RUNS_DIR = ROOT / "outputs" / "generation"
POLICY_FILES = {
    "airline": ["policy.md"],
    "retail": ["policy.md"],
    "telecom": ["main_policy.md", "tech_support_manual.md"],
}


def configure_data() -> Path:
    load_dotenv(ROOT / ".env")
    data_dir = Path(os.environ.get("TAU2_DATA_DIR", ROOT / "external/tau2-bench/data"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    data_dir = data_dir.resolve()
    os.environ["TAU2_DATA_DIR"] = str(data_dir)
    return data_dir


def model_tag(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", model)


def load_policy_documents(domain: str) -> str:
    base = configure_data() / "tau2" / "domains" / domain
    return "\n\n".join(
        f"### Document: {name}\n\n{(base / name).read_text()}"
        for name in POLICY_FILES[domain]
    )
