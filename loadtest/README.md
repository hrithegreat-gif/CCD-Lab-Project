# Load testing

For the implemented local runner, three-repeat workflow, analysis commands and
acceptance criteria, follow [Phase 3](../docs/PHASE_3.md). It uses an explicit
context and matching kubectl, preserves unique raw run folders, and waits for
idle recovery between runs.

Real, bounded k6 benchmark for the deployed service. Nothing in this directory
produces performance numbers by itself; results exist only after a measured run
and are recorded manually in results/performance.csv (or under results/raw/,
which is git-ignored).

## Files

| File | Purpose |
| --- | --- |
| k6-script.js | Bounded three-phase benchmark (see below) |
| ../k8s/load-generator.yaml | In-cluster k6 Job targeting the Service DNS name |
| ../scripts/record_scaling.py | Concurrent HPA/Pod scaling recorder (JSONL) |

## Scenario (one `k6 run`)

| Phase | Duration | VUs | Intent |
| --- | --- | --- | --- |
| warmup | 15s | 2 | fill caches, verify the service answers |
| baseline | 30s | 2 | one-replica capacity (2 inference slots) |
| sustained | default 180s, cap 600s | default 8, cap 32 | drive CPU toward the HPA target |

Warm-up and baseline now pause 100 ms between requests per virtual user. Sustained
think time is 0 by default so load can saturate CPU and trigger the
HPA; set `LOADTEST_THINK_TIME_MS` (cap 1000) for gentler sustained runs. 429 responses
are expected under saturation and are counted separately; `http_req_failed`
only counts real failures (threshold `rate<0.05`).

## Connection handling and metric separation

`noConnectionReuse: true` is deliberate. With keep-alive, a VU's connections
stick to the Pod that accepted them, so scale-out replicas can receive zero
load even though traffic targets the Service DNS name. A fresh connection per
request lets the Service re-balance every request across ready Pods
(tradeoff: one extra TCP handshake per request at the generator and node).

Every scenario uses `gracefulStop: '0s'` so warmup, baseline and sustained
can never overlap: requests still in flight at a phase boundary are
interrupted. Each attempt is counted BEFORE the request is sent, so the
summary can distinguish completed, invalid, and interrupted attempts
(`interrupted = attempts - completed` per phase).

Measured results are separated by construction:

- Latency trends (`latency_success_{warmup,baseline,sustained}_ms`) record
  ONLY body-validated successful (200) predictions; malformed 200 bodies
  (`latency_invalid_ms`), 429s (`latency_rate_limited_ms`) and 4xx/5xx
  (`latency_errors_ms`) have their own trends.
- A 200 that fails the response-shape validation is counted as
  `requests_200_invalid` / `invalid_200_*` — never as successful traffic.
  Unexpected statuses (e.g. redirects) are tracked as `requests_other` /
  `other_*`.
- Per-phase counters (`attempts_*`, `success_200_*`, `invalid_200_*`, ...)
  keep warmup data out of baseline/sustained results; each summary phase
  block carries `phase_duration_s`, `attempt_throughput_rps` and
  `successful_throughput_rps` so the CSV can use per-phase values instead of
  warmup-polluted aggregates.
- The aggregate request rate is reported as ATTEMPT throughput
  (`attempt_throughput_rps`, including 429/4xx/5xx), clearly distinguished
  from successful traffic; the successful (200, body-validated) rate is
  `successful_throughput_rps`.
- Every 200 body is validated against the real `/api/inference` response
  shape (status, prediction, confidence, tokens_processed,
  processing_time_ms) via the `valid sentiment response` check.

Every request POSTs synthetic English sentences to `/api/inference` with
`Authorization: Bearer $API_KEY` when `API_KEY` is set. The key is never
logged, echoed or written to summaries. There is no jobs endpoint and no
queue/database anywhere in this pipeline.

## Run in-cluster (representative multi-replica load)

    kubectl -n ai-serving create configmap k6-script \
        --from-file=k6-script.js=loadtest/k6-script.js
    kubectl -n ai-serving apply -f k8s/load-generator.yaml
    kubectl -n ai-serving logs job/load-generator -f

The Job targets `ai-model-serving.ai-serving.svc.cluster.local:8000` so
traffic is balanced across replicas, and with `noConnectionReuse: true` new
replicas actually receive connections. The Job is hard-bounded
(`activeDeadlineSeconds: 900`) and never retries (`backoffLimit: 0`).

## Run from a laptop through the SSM tunnel

    aws ssm start-session --target <instance-id> \
        --document-name <SSM-port-forward-document> \
        --parameters '{"portNumber":["30080"],"localPortNumber":["30080"]}'
    TARGET_URL=http://127.0.0.1:30080 API_KEY=<key> k6 run loadtest/k6-script.js

Note: an SSM port-forward to the NodePort does NOT pin one Pod — kube-proxy
balances NodePort traffic across ready Pods, unlike a
`kubectl port-forward svc/...` which pins a single Pod. Even so, use the
in-cluster Job for performance measurements: it avoids tunnel latency and
runs where the service lives. Use the tunnel for functional/smoke checks.

## Record scaling during the run (second terminal)

Cover the whole experiment: recording must span warmup + baseline + sustained
PLUS the 300s scale-in stabilization window and margin, otherwise the
scale-in observation is cut short:

    # 15 + 30 + 180 + 300 + margin:
    python scripts/record_scaling.py --duration 660 --interval 5

Use `--kubectl <matching-client-path> --context ai-serving-local` for this local
cluster. Existing output files are refused to prevent accidentally concatenating
different experiments. Writes one JSON sample per line (HPA replicas, ready counts, per-Pod CPU/mem
from Metrics Server) to results/raw/scaling-<timestamp>.jsonl.

## Documenting results

Use the [Phase 3 runner and analyzer](../docs/PHASE_3.md) to preserve k6 stdout,
phase timing, resource samples and source provenance automatically. Completed
containers do not support reliable `kubectl cp`; the runner parses the summary
from retained Job logs. It produces separate baseline, sustained and recovery
rows in `results/performance.csv`, with figures and compact evidence.

Use per-phase successful throughput and latency, excluding warm-up and fast
rejections. Recovery has no generated requests. The executed local experiments
and their limitations are in [results/ANALYSIS.md](../results/ANALYSIS.md).
