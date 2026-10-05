"""Run PolicyGuide on the airline, retail, or telecom domain of tau2-bench."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", choices=["airline", "retail", "telecom"], default="airline")
    parser.add_argument("--workflow-dir", type=Path, required=True,
                        help="Workflow directory produced by generate_workflow.py")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--guide-model", help="Defaults to the agent model")
    parser.add_argument("--user-model", default="gpt-4.1")
    tasks = parser.add_mutually_exclusive_group()
    tasks.add_argument("--task-ids", nargs="+")
    tasks.add_argument("--test-split", action="store_true")
    parser.add_argument("--num-tasks", type=int)
    parser.add_argument("--num-trials", type=int, default=1)
    parser.add_argument("--seed", type=int, default=300)
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    for name in ("num_tasks", "num_trials", "max_concurrency"):
        value = getattr(args, name)
        if value is not None and value < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    data_dir = Path(os.environ.get("TAU2_DATA_DIR", ROOT / "external/tau2-bench/data"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    os.environ["TAU2_DATA_DIR"] = str(data_dir.resolve())
    domain_dir = data_dir / "tau2/domains" / args.domain
    if not domain_dir.is_dir():
        parser.error("Benchmark data not found; follow the README setup or set TAU2_DATA_DIR")
    sys.path.insert(0, str(ROOT / "src"))

    import litellm
    from tau2.data_model.simulation import TextRunConfig
    from tau2.registry import registry
    from tau2.run import run_domain
    from policyguide.agent import create_policyguide_llm_agent
    from policyguide.guide import get_guide_usage, reset_guide_usage
    from policyguide.loader import SimplifiedWorkflow
    from policyguide.patch import patch_orchestrator_simplified

    litellm.modify_params = True
    task_ids = args.task_ids
    if args.test_split:
        ids = list(json.loads((domain_dir / "split_tasks.json").read_text())["test"])
        n = args.num_tasks
        # Match the research runner's deterministic, evenly spread test subset.
        if n and n < len(ids):
            ids = [ids[0]] if n == 1 else [ids[round(i * (len(ids) - 1) / (n - 1))] for i in range(n)]
        task_ids = ids
        num_tasks = None
    else:
        num_tasks = args.num_tasks

    policy_files = ("main_policy.md", "tech_support_manual.md") if args.domain == "telecom" else ("policy.md",)
    policy_doc = "\n\n".join((domain_dir / name).read_text() for name in policy_files)
    workflow = SimplifiedWorkflow(args.workflow_dir, args.domain, policy_doc=policy_doc)
    output_dir = (args.output_dir or ROOT / "outputs" / args.domain).resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not args.resume:
        parser.error("Output directory is not empty; choose another --output-dir or use --resume")
    output_dir.mkdir(parents=True, exist_ok=True)
    guide_model = args.guide_model or args.model
    patch_orchestrator_simplified(
        workflow, guide_model=guide_model, seed=args.seed,
        enforce=False, user_turn_only=True, write_gate=True,
        memory_mode="summary", prompt_mode="flat",
        guide_log_path=str(output_dir / "guide_log.jsonl"),
        debug_log_path=str(output_dir / "debug_log.jsonl"),
    )
    if registry.get_agent_factory("policyguide_agent") is None:
        registry.register_agent_factory(create_policyguide_llm_agent, "policyguide_agent")

    config = TextRunConfig(
        domain=args.domain, agent="policyguide_agent", user="user_simulator",
        llm_agent=args.model, llm_args_agent={"temperature": 0.0, "seed": args.seed},
        llm_user=args.user_model, llm_args_user={"temperature": 0.0, "seed": args.seed},
        task_ids=task_ids, num_tasks=num_tasks,
        num_trials=args.num_trials, seed=args.seed, max_steps=200, max_errors=10,
        max_concurrency=args.max_concurrency, log_level="WARNING", auto_resume=args.resume,
        save_to=str(output_dir / "result.json"),
    )
    reset_guide_usage()
    run_domain(config)
    (output_dir / "guide_cost.json").write_text(json.dumps({
        "guide_model": guide_model,
        "agent_model": args.model,
        **get_guide_usage(),
    }, indent=2) + "\n")
    print(f"Results: {output_dir}")


if __name__ == "__main__":
    main()
