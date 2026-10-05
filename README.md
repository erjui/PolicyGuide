# PolicyGuide: From Guarding One Action to Guiding the Whole Workflow for Policy-Compliant LLM Agents

[Project page](https://policyguide.github.io/) · [Paper](https://arxiv.org/abs/2608.19861)

PolicyGuide tracks open requests through a frozen policy workflow and gives the
agent step-specific remediation. This release contains the final runtime,
workflow-generation code, and a single tau2-bench runner. Generated workflow
graphs are not stored in the repository.

## Setup

The reported runs used Python 3.13. From this repository directory:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
git clone https://github.com/sierra-research/tau2-bench.git external/tau2-bench
git -C external/tau2-bench checkout c42db6cc223ef37c02ef2fb2f605ae0a4ca9afd6
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env`. Other agent models can use LiteLLM provider keys;
`--user-model` defaults to `gpt-4.1`. Benchmark data is read from
`external/tau2-bench/data`; set `TAU2_DATA_DIR` to use another data directory.
The benchmark revision and Python packages are pinned to the original
PolicyGuide environment.

## Run

Generate one workflow for each domain before running the benchmark:

```bash
python generate_workflow.py --domain airline --model gpt-5.4 \
  --run-id airline_gpt54 --output-dir outputs/workflows/airline
python generate_workflow.py --domain retail --model gpt-5.4 \
  --run-id retail_gpt54 --output-dir outputs/workflows/retail
python generate_workflow.py --domain telecom --model gpt-5.4 \
  --run-id telecom_gpt54 --output-dir outputs/workflows/telecom
```

Run the original four-seed protocol:

```bash
for seed in 300 301 302 303; do
  python run.py --domain airline --workflow-dir outputs/workflows/airline \
    --model gpt-5.4 --guide-model gpt-5.4 \
    --user-model gpt-4.1 --seed "$seed" --num-trials 1 \
    --max-concurrency 4 --output-dir "outputs/airline/seed_${seed}"
  python run.py --domain retail --test-split --workflow-dir outputs/workflows/retail \
    --model gpt-5.4 --guide-model gpt-5.4 --user-model gpt-4.1 \
    --seed "$seed" --num-trials 1 --max-concurrency 4 \
    --output-dir "outputs/retail/seed_${seed}"
  python run.py --domain telecom --test-split --workflow-dir outputs/workflows/telecom \
    --model gpt-5.4 --guide-model gpt-5.4 --user-model gpt-4.1 \
    --seed "$seed" --num-trials 1 --max-concurrency 4 \
    --output-dir "outputs/telecom/seed_${seed}"
done
```

Airline uses the 50-task base set. Retail and telecom use their 40-task test
splits. The four one-trial runs reproduce the original seed protocol; a single
`--num-trials 4` run does not substitute seeds 300 through 303 in the agent,
guide, and user-model arguments.

`--guide-model` defaults to `--model`. `--workflow-dir` is required and must
point to a compatible generated workflow.

The default runtime matches the final research runner: user-turn guidance,
persisted request state, full conversation plus the latest summarized state
record, cumulative developer-role remediation, and a one-shot corrective gate
for unauthorized writes. Unauthorized transfers also trigger corrective
guidance. This is advisory execution: an immediate write retry is allowed after
the corrective intervention.

Use `--task-ids 8` for specific tasks, `--test-split` for the benchmark test
split, or omit both for all tasks. `--num-tasks` limits the run. Other settings
are listed by `python run.py --help`.

Results, guide usage, and runtime logs are written under `outputs/` and ignored by Git.
Use a separate `--output-dir` for each configuration. Reusing a nonempty
directory requires `--resume` and the same run settings.

## Generate a workflow graph

The generator extracts tool specifications, plans and reviews the workflow,
generates and reviews subflows, wires the main graph, then runs structural and
policy reviews. It uses the simplified generation rulebook and prompts that
produced `simplified_gpt54_20260621_autogen`, the workflow snapshot used by the
PolicyGuide runs. Generation uses the OpenAI API and `OPENAI_API_KEY` from `.env`.

```bash
python generate_workflow.py --domain retail --model gpt-5.4 \
  --run-id retail_v1 --output-dir outputs/workflows/retail
python run.py --domain retail --workflow-dir outputs/workflows/retail \
  --model gpt-5.4 --num-tasks 1
```

Use `--domain airline` or `--domain telecom` for the other domains. The generated
bundle contains `_manifest.json`, `main.workflow.json`, and `subflows/*.subflow.json`.
The runtime loader validates and composes these into the flat workflow graph;
no visualization package is needed.

Intermediate prompts, responses, and validation reports stay under
`outputs/generation/<run-id>/`. Repeat a failed command with the same `--run-id`
to reuse completed calls; changed requests require a new run ID or `--force`.
`--budget` defaults to an estimated $5 using LiteLLM's model cost map and includes
previous calls when resuming. It stops new calls once reached; a single call can
exceed the remaining estimate. Raise it explicitly when resuming if needed.
Final output is written only after validation passes and never overwrites a
nonempty directory.

## Files

- `run.py`: main entry point with the final runtime settings.
- `generate_workflow.py`: workflow graph generation from domain policies.
- `src/create_workflow/`: generation pipeline, prompts, and validation.
- `src/policyguide/`: workflow loading, request state, prompts, agent, and orchestrator integration.

## Citation

```bibtex
@article{kang2026policyguide,
  title={PolicyGuide: From Guarding One Action to Guiding the Whole Workflow for Policy-Compliant LLM Agents},
  author={Kang, Seongjae and Yu, Taehyung and Hwang, Sung Ju},
  journal={arXiv preprint arXiv:2608.19861},
  year={2026}
}
```
