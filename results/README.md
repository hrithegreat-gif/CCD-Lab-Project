# Results and evidence

[ANALYSIS.md](ANALYSIS.md) reports three local Minikube detection experiments,
including successful goodput, successful-response latency, 429 rejections,
scale-out/scale-in timing, and resource-sample coverage. The nine phase rows are
in [performance.csv](performance.csv); plots and compact evidence are under
`results/figures/` and `results/evidence/detection-20261023/`.

These are local single-node results, not AWS measurements, proof of hosted CI,
or a claim of high availability. Do not present historical sentiment results
as detection measurements.

Reproduce local experiments using [the load-test guide](../loadtest/README.md)
and [the runbook](../docs/RUNBOOK.md). Preserve raw summaries and scaling
samples, include the idle scale-down window, and derive CSV data with
`scripts/analyze_experiments.py`. `throughput_rps` is successful inference
goodput; 429s and interrupted attempts are reported separately in `notes`.

Never commit API keys, kubeconfigs, Terraform state/plan files, or unsanitized
command logs. Cloud artifacts are valid evidence only after an authorized
deployment and an actually executed cloud test.
