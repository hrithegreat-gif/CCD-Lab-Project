# Measured results and evidence

No formal container/Kubernetes/EC2 performance experiment has run yet. The [performance CSV](performance.csv) is intentionally header-only. Local real-model smoke checks and unit tests are functional evidence, not load benchmarks; see the [verification ledger](../docs/VERIFICATION.md).

For each real experiment, preserve:

- Commit SHA, image digest/tag, immutable model revision, timestamp/time zone.
- Actual machine/instance, CPU, RAM, container limits, Kubernetes/Metrics Server versions.
- Synthetic payload, phase, concurrency, duration and replica/HPA settings.
- Total attempts, successful predictions, 429 rate, other client/server/transport failures. **Attempt rate is not successful inference throughput.** Report successful-response latency separately from fast rejected requests, and keep warmup outside baseline/sustained comparisons.
- k6 summary and CPU/memory/HPA/ready-Pod JSONL time series under `results/raw/` (ignored). Record long enough to include at least 300 seconds of idle scale-down stabilization after the load ends.
- Replica timing and pending/failed Pods as well as ready replicas. More replicas on a single node are neither more hardware nor high availability.

Use the [load-test instructions](../loadtest/README.md) and [Kubernetes runbook](../docs/KUBERNETES.md). Add only actual measured rows to the CSV; leave unavailable fields blank and explain missing measurements in `notes`. Its `throughput_rps` should represent successful inference goodput for the reported phase. Include attempted rate, 429s and how `error_rate` treats rejections in `notes` or the accompanying summary. Preserve raw evidence before calculating scaling delays.

Never commit API keys, real user text/audio, kubeconfigs, Terraform state/plan JSON or unsanitized command logs. The S3 results copy is operator-controlled cloud evidence only after an authorized deployment; it is not a source of measurements until those experiments actually run.
