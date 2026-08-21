# PolicyGuide: From Guarding One Action to Guiding the Whole Workflow for Policy-Compliant LLM Agents

[![arXiv](https://img.shields.io/badge/arXiv-2608.19861-b31b1b.svg)](https://arxiv.org/abs/2608.19861)
[![Hugging Face Papers](https://img.shields.io/badge/Hugging_Face-Paper-ffd21e.svg)](https://huggingface.co/papers/2608.19861)

Official implementation of **PolicyGuide**, a proactive verifier that compiles domain policies into workflow graphs, tracks requests across turns, and gives step-specific remediation along policy-compliant paths.

Across the airline, retail, and telecom domains of [tau2-bench](https://github.com/sierra-research/tau2-bench), PolicyGuide raises mean $\mathrm{Pass}^4$ from 0.42 to 0.62 with a GPT-5.4 agent and verifier. The largest gain is on telecom, from 0.19 to 0.61, and the same workflows transfer to Claude Sonnet 4.6 and Gemini 2.5 Pro agents.

## Coming soon

Code and workflow specifications will be released soon.

## Citation

```bibtex
@article{kang2026policyguide,
  title={PolicyGuide: From Guarding One Action to Guiding the Whole Workflow for Policy-Compliant LLM Agents},
  author={Kang, Seongjae and Yu, Taehyung and Hwang, Sung Ju},
  journal={arXiv preprint arXiv:2608.19861},
  year={2026}
}
```
