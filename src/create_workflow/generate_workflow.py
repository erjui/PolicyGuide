"""Policy -> PolicyGuide workflow-graph generation pipeline.

Steps (see README.md):
  0. tool specs (deterministic)        1. plan (1 LLM call)
  1b. plan review (1 call)             2. subflows (N calls, shared first)
  3. main (1 call)                     4. lint (deterministic)
  5. review rounds (up to --max-review-rounds calls)

Every LLM step is cached under outputs/generation/<run_id>/steps/; re-running with the same
--run-id skips completed calls. Cost is tracked per call and the run aborts
past --budget.

Usage:
  python generate_workflow.py --domain airline --model gpt-5.4
  python generate_workflow.py --domain airline --run-id <id>   # resume
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from .config import (ROOT, PROMPTS_DIR, RUNS_DIR, POLICY_FILES,
                     configure_data, load_policy_documents, model_tag)
from .llm import CostTracker, call_llm, make_client
from .lint import lint_bundle, format_findings, has_errors
from .tool_specs import (extract_tools, classify_tools, render_tool_specs,
                         extract_user_tools, render_user_tool_specs)
from .domain_vocab import extract_domain_vocab, render_domain_vocab
from policyguide.workflow_loader import validate_file, WorkflowSpecError


# ── parsing helpers ──────────────────────────────────────────────────────────

def last_json_block(text: str) -> dict:
    blocks = re.findall(r"```json\s*\n(.*?)```", text, re.DOTALL)
    if not blocks:
        raise ValueError("No ```json block found in response")
    return json.loads(blocks[-1])


def parse_file_blocks(text: str) -> dict[str, dict]:
    """Parse '--- FILE: path ---' + ```json fences into {path: spec_dict}."""
    out: dict[str, dict] = {}
    parts = re.split(r"---\s*FILE:\s*(\S+?)\s*---", text)
    for i in range(1, len(parts), 2):
        path = parts[i].strip()
        if path != "main.workflow.json" and not re.fullmatch(
            r"subflows/[a-z][a-z0-9_]*\.subflow\.json", path
        ):
            raise ValueError(f"Invalid workflow output path: {path!r}")
        if path in out:
            raise ValueError(f"Duplicate workflow output path: {path}")
        m = re.search(r"```json\s*\n(.*?)```", parts[i + 1], re.DOTALL)
        if not m:
            raise ValueError(f"FILE block {path!r} has no ```json fence")
        spec = json.loads(m.group(1))
        if not isinstance(spec, dict):
            raise ValueError(f"{path}: expected a JSON object")
        if path == "main.workflow.json":
            if spec.get("kind") != "main":
                raise ValueError("main.workflow.json must have kind=main")
        elif spec.get("kind") != "subflow" or path != f"subflows/{spec.get('name')}.subflow.json":
            raise ValueError(f"{path}: subflow kind/name does not match its filename")
        out[path] = spec
    return out


# ── prompt assembly ──────────────────────────────────────────────────────────

def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text()


def fill(template: str, **tokens: str) -> str:
    for k, v in tokens.items():
        template = template.replace(f"<<{k}>>", v)
    return template


def subflow_interface(spec: dict) -> str:
    return (f"- `{spec['name']}` — entry `{spec['entry']}`, "
            f"exits {spec['exits']}: {spec.get('description', '')}")


def validate_plan(plan: dict) -> None:
    if not isinstance(plan, dict) or not isinstance(plan.get("subflow_skeletons"), list):
        raise ValueError("Plan must contain a subflow_skeletons list")
    names = set()
    for spec in plan["subflow_skeletons"]:
        name = spec.get("name") if isinstance(spec, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"Invalid subflow name: {name!r}")
        if name in names:
            raise ValueError(f"Duplicate subflow name: {name}")
        names.add(name)
    for spec in plan.get("shared_subflows", []):
        if not isinstance(spec, dict) or spec.get("name") not in names:
            raise ValueError("Every shared subflow needs a corresponding skeleton")


def validate_for_publication(main_spec, subflows, write_tools, agent_tool_names, report_path):
    """Check the final pruned bundle before writing any runnable output."""
    findings = lint_bundle(main_spec, subflows, write_tools, agent_tools=agent_tool_names)
    report_path.write_text(format_findings(findings) + "\n")
    if has_errors(findings):
        raise RuntimeError(f"Workflow still has validation errors; see {report_path}")


# ── cached LLM step ──────────────────────────────────────────────────────────

class Runner:
    def __init__(self, args):
        self.args = args
        self.run_dir = RUNS_DIR / args.run_id
        self.steps_dir = self.run_dir / "steps"
        self.steps_dir.mkdir(parents=True, exist_ok=True)
        self.client = make_client()
        self.tracker = CostTracker(args.budget, self.run_dir / "calls.jsonl")
        self.system = load_prompt("00_system.md")

    def cached_call(self, step: str, user_prompt: str) -> str:
        resp_path = self.steps_dir / f"{step}.response.md"
        key_path = self.steps_dir / f"{step}.request.sha256"
        request = json.dumps([self.args.model, self.args.temperature, self.system, user_prompt])
        key = hashlib.sha256(request.encode()).hexdigest()
        if resp_path.exists() and not self.args.force:
            if not key_path.exists() or key_path.read_text() != key:
                raise ValueError("Cached request differs; choose a new --run-id or use --force")
            print(f"  [{step}] cached — skipping call")
            return resp_path.read_text()
        resp_path.unlink(missing_ok=True)
        key_path.write_text(key)
        (self.steps_dir / f"{step}.prompt.md").write_text(user_prompt)
        text = call_llm(self.client, self.args.model, self.system, user_prompt,
                        self.args.temperature, self.tracker, step)
        resp_path.write_text(text)
        return text

    def call_and_parse_file(self, step: str, user_prompt: str,
                            expect_path: str) -> dict:
        """Call, parse the single FILE block, validate; one repair retry."""
        text = self.cached_call(step, user_prompt)
        for attempt in (0, 1):
            try:
                files = parse_file_blocks(text)
                if expect_path not in files:
                    raise ValueError(
                        f"Expected file {expect_path!r}, got {list(files)}")
                spec = files[expect_path]
                validate_file(spec)
                if spec["domain"] != self.args.domain:
                    raise ValueError(f"Expected domain {self.args.domain!r}")
                return spec
            except (ValueError, WorkflowSpecError, json.JSONDecodeError) as e:
                if attempt == 1:
                    raise RuntimeError(f"[{step}] unrecoverable after retry: {e}")
                print(f"  [{step}] invalid output ({e}); retrying with feedback")
                retry_prompt = (user_prompt +
                                f"\n\n## Your previous output failed validation\n\n"
                                f"Error: {e}\n\nFix the problem and output the "
                                f"complete corrected file again, same format.")
                (self.steps_dir / f"{step}.retry.prompt.md").write_text(retry_prompt)
                text = call_llm(self.client, self.args.model, self.system,
                                retry_prompt, self.args.temperature,
                                self.tracker, f"{step}.retry")
                (self.steps_dir / f"{step}.response.md").write_text(text)

    def review_subflow_paths(self, step: str, name: str, spec: dict,
                             skeleton: dict) -> dict:
        """Step 2b: general control-flow path review of ONE subflow. Returns the
        corrected spec, or the original on NO_CHANGES / unparseable output."""
        prompt = fill(load_prompt("02b_subflow_review.md"), DOMAIN=self.args.domain,
                      SUBFLOW_NAME=name,
                      SKELETON_JSON=json.dumps(skeleton, indent=2),
                      SUBFLOW_JSON=json.dumps(spec, indent=2))
        text = self.cached_call(step, prompt)
        if "NO_CHANGES" in text and "--- FILE:" not in text:
            return spec
        try:
            files = parse_file_blocks(text)
            path = f"subflows/{name}.subflow.json"
            if path in files:
                validate_file(files[path])
                return files[path]
        except (ValueError, WorkflowSpecError, json.JSONDecodeError) as e:
            print(f"  [{step}] review output rejected ({e}); keeping original")
        return spec


# ── main pipeline ────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", required=True, choices=list(POLICY_FILES))
    ap.add_argument("--model", default="gpt-5.4")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--budget", type=float, default=5.0,
                    help="Stop new calls at this estimated USD cost; a call may overshoot (default 5)")
    ap.add_argument("--run-id", default=None,
                    help="reuse a run dir to resume cached steps")
    ap.add_argument("--max-review-rounds", type=int, default=4)
    ap.add_argument("--no-plan-review", action="store_true",
                    help="skip the Step 1b structural plan review")
    ap.add_argument("--output-dir", type=Path, default=None,
                    help="New output directory (default: outputs/workflows/<run-id>)")
    ap.add_argument("--force", action="store_true",
                    help="ignore cached step outputs")
    args = ap.parse_args()

    if args.budget <= 0 or args.max_review_rounds < 1:
        ap.error("--budget must be positive and --max-review-rounds must be at least 1")
    if args.run_id is None:
        args.run_id = f"{args.domain}_{model_tag(args.model)}_" + \
                      datetime.now().strftime("%Y%m%d_%H%M%S")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.run_id):
        ap.error("--run-id must contain only letters, digits, underscores, and hyphens")
    out_dir = (args.output_dir or ROOT / "outputs" / "workflows" / args.run_id).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        ap.error("Output directory is not empty; choose a new --output-dir")
    configure_data()
    r = Runner(args)
    print(f"Run: {r.run_dir}")

    # Step 0 — deterministic tool specs
    tools = extract_tools(args.domain)
    tool_classes = classify_tools(tools)
    user_tools = extract_user_tools(args.domain)
    tool_specs_md = render_tool_specs(tools)
    user_md = render_user_tool_specs(user_tools)
    if user_md:
        tool_specs_md = (f"## AGENT TOOLS (the only tools usable in tool_call "
                         f"/ tool_authorization nodes)\n\n{tool_specs_md}\n\n"
                         f"{user_md}")
    vocab_md = render_domain_vocab(extract_domain_vocab(args.domain))
    if vocab_md:
        tool_specs_md = f"{tool_specs_md}\n{vocab_md}"
    (r.run_dir / "tool_specs.md").write_text(tool_specs_md)
    (r.run_dir / "tool_classes.json").write_text(json.dumps(tool_classes, indent=2))
    agent_tool_names = [t["name"] for t in tools]
    print(f"Step 0: {len(tools)} agent tools "
          f"({len(tool_classes['write'])} WRITE / {len(tool_classes['read'])} READ), "
          f"{len(user_tools)} user-side tools")

    policy_docs = load_policy_documents(args.domain)

    # Step 1 — plan
    print("Step 1: plan")
    plan_prompt = fill(load_prompt("01_plan.md"), DOMAIN=args.domain,
                       POLICY_DOCUMENTS=policy_docs, TOOL_SPECS=tool_specs_md)
    plan = last_json_block(r.cached_call("01_plan", plan_prompt))
    validate_plan(plan)

    # Step 1b — plan review (structural critic; returns the corrected plan).
    # Catches topology errors (document-mirroring, nested classifiers, one-shot
    # troubleshooting) BEFORE they propagate into every generated file — the
    # per-file review (Step 5) is node-level and cannot restructure across files.
    if not args.no_plan_review:
        print("Step 1b: plan review")
        (r.run_dir / "plan_pre_review.json").write_text(json.dumps(plan, indent=2))
        pr_prompt = fill(load_prompt("01b_plan_review.md"), DOMAIN=args.domain,
                         POLICY_DOCUMENTS=policy_docs, TOOL_SPECS=tool_specs_md,
                         PLAN_JSON=json.dumps(plan, indent=2))
        try:
            reviewed_plan = last_json_block(r.cached_call("01b_plan_review", pr_prompt))
            validate_plan(reviewed_plan)
            plan = reviewed_plan
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  plan review output unparseable ({e}); keeping original plan")
    (r.run_dir / "plan.json").write_text(json.dumps(plan, indent=2))

    skeletons = {s["name"]: s for s in plan["subflow_skeletons"]}
    shared_names = [s["name"] for s in plan.get("shared_subflows", [])
                    if s["name"] in skeletons]
    ordered = shared_names + [n for n in skeletons if n not in shared_names]

    # Step 2 — subflows (shared first so later prompts can cite their interfaces)
    print(f"Step 2: {len(ordered)} subflows")
    subflows: dict[str, dict] = {}
    plan_json = json.dumps(plan, indent=2)
    for i, name in enumerate(ordered):
        shared_ifaces = "\n".join(
            subflow_interface(subflows[n]) for n in shared_names if n in subflows
        ) or "(none yet)"
        prompt = fill(load_prompt("02_subflow.md"), DOMAIN=args.domain,
                      SUBFLOW_NAME=name, POLICY_DOCUMENTS=policy_docs,
                      TOOL_SPECS=tool_specs_md, PLAN_JSON=plan_json,
                      SKELETON_JSON=json.dumps(skeletons[name], indent=2),
                      SHARED_SUBFLOW_INTERFACES=shared_ifaces)
        spec = r.call_and_parse_file(f"02_subflow_{i:02d}_{name}", prompt,
                                     f"subflows/{name}.subflow.json")
        # Step 2b — structural path review, branching subflows only (a single-path
        # flow cannot skip-and-exit). Catches premature success exits, unconditional
        # edges that should branch, and dead tails — generation-introduced flow bugs
        # the node-level Step 5 review misses.
        if any(n["type"] == "decision" for n in spec["nodes"]):
            spec = r.review_subflow_paths(f"02b_review_{i:02d}_{name}", name, spec,
                                          skeletons[name])
        subflows[spec["name"]] = spec

    # Step 3 — main
    print("Step 3: main")
    ifaces = "\n".join(subflow_interface(s) for s in subflows.values())
    main_prompt = fill(load_prompt("03_main.md"), DOMAIN=args.domain,
                       POLICY_DOCUMENTS=policy_docs, PLAN_JSON=plan_json,
                       SUBFLOW_INTERFACES=ifaces)
    main_spec = r.call_and_parse_file("03_main", main_prompt, "main.workflow.json")

    # Steps 4+5 — lint / review loop. Review repairs lint ERRORS as many rounds
    # as allowed, but runs at most ONE review pass beyond lint-clean (the
    # policy-completeness check) — endless clean-pass reviews churn files.
    clean_reviews = 0
    for round_no in range(args.max_review_rounds + 1):
        findings = lint_bundle(main_spec, subflows, tool_classes["write"],
                               agent_tools=agent_tool_names)
        report = format_findings(findings)
        (r.run_dir / f"lint_round{round_no}.md").write_text(report)
        print(f"Step 4 (round {round_no}): "
              f"{sum(f['level'] == 'ERROR' for f in findings)} errors / "
              f"{sum(f['level'] == 'WARN' for f in findings)} warnings")

        if round_no >= args.max_review_rounds:
            if has_errors(findings):
                print("WARNING: lint errors remain after final review round.")
            break
        if not has_errors(findings) and clean_reviews >= 1:
            break

        wf_files = "\n\n".join(
            [f"--- FILE: main.workflow.json ---\n```json\n"
             f"{json.dumps(main_spec, indent=2)}\n```"] +
            [f"--- FILE: subflows/{n}.subflow.json ---\n```json\n"
             f"{json.dumps(s, indent=2)}\n```" for n, s in subflows.items()])
        review_prompt = fill(load_prompt("04_review.md"), DOMAIN=args.domain,
                             POLICY_DOCUMENTS=policy_docs,
                             TOOL_SPECS=tool_specs_md,
                             WORKFLOW_FILES=wf_files, LINT_REPORT=report)
        text = r.cached_call(f"05_review_round{round_no}", review_prompt)
        if not has_errors(findings):
            clean_reviews += 1
        if "NO_CHANGES" in text and "--- FILE:" not in text:
            print(f"Step 5 (round {round_no}): reviewer reports NO_CHANGES")
            if not has_errors(findings):
                break
            continue
        changed = parse_file_blocks(text)
        print(f"Step 5 (round {round_no}): reviewer changed {len(changed)} files")
        for path, spec in changed.items():
            try:
                validate_file(spec)
            except WorkflowSpecError as e:
                print(f"  REJECTED reviewer file {path}: {e}")
                continue
            if path == "main.workflow.json":
                main_spec = spec
            elif path.startswith("subflows/"):
                subflows[spec["name"]] = spec

    # Step 5b — prune subflows not reachable from main via anchors (orphans
    # never compose into the runtime graph; keeping them pollutes the artifact)
    referenced: set[str] = set()
    frontier = [main_spec]
    while frontier:
        spec = frontier.pop()
        for n in spec["nodes"]:
            if n["type"] == "subflow" and n["subflow"] in subflows and \
                    n["subflow"] not in referenced:
                referenced.add(n["subflow"])
                frontier.append(subflows[n["subflow"]])
    orphans = sorted(set(subflows) - referenced)
    if orphans:
        print(f"Pruning unreferenced subflows: {orphans}")
        subflows = {k: v for k, v in subflows.items() if k in referenced}

    if main_spec["domain"] != args.domain or any(s["domain"] != args.domain for s in subflows.values()):
        raise ValueError("Generated workflow domain does not match --domain")
    validate_for_publication(main_spec, subflows, tool_classes["write"],
                             agent_tool_names, r.run_dir / "lint_final.md")

    # Step 6 — write final output + manifest
    (out_dir / "subflows").mkdir(parents=True, exist_ok=True)
    (out_dir / "main.workflow.json").write_text(json.dumps(main_spec, indent=2) + "\n")
    for name, spec in subflows.items():
        (out_dir / "subflows" / f"{name}.subflow.json").write_text(
            json.dumps(spec, indent=2) + "\n")
    auth_tools = sorted({n["tool"] for s in subflows.values() for n in s["nodes"]
                         if n["type"] == "tool_authorization"} |
                        {n["tool"] for n in main_spec["nodes"]
                         if n["type"] == "tool_authorization"})
    manifest = {
        "schema_version": 1,
        "domain": args.domain,
        "author": "policyguide_simplified_pipeline",
        "author_model": args.model,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "policy_sources": [f"data/tau2/domains/{args.domain}/{f}"
                           for f in POLICY_FILES[args.domain]],
        "main_file": "main.workflow.json",
        "subflows": sorted(subflows),
        "mutating_tools_covered": auth_tools,
        "notes": f"Simplified pipeline, run {args.run_id}. Leaner rulebook "
                 f"(faithful + task-properties + valid-schema); 6 stages.",
    }
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"\nOutput: {out_dir}")
    print(r.tracker.summary())


if __name__ == "__main__":
    main()
