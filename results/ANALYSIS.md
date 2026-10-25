# Detection scaling results

Date: 2026-10-23

These are three local Minikube runs of the YOLOv8n detection service. They are
not AWS measurements and do not establish production capacity or availability.
Raw run records are under `results/raw/detection-20261023/`; the measured phase
rows are in `results/performance.csv`, with scaling plots in `results/figures/`
and compact evidence in `results/evidence/detection-20261023/`.

## Environment and scenario

- Windows 11 with Docker Desktop/WSL2; Minikube node limited to 4 CPU and 6 GiB.
- Serving containers requested 500m CPU/512 MiB and were limited to 1 CPU/1 GiB.
- YOLOv8n ONNX CPU model; HPA target 70% CPU, minimum 1 and maximum 3 replicas.
- Each run used one baseline VU and eight sustained VUs for 180 seconds, with
  no connection reuse. The baseline and sustained phases are reported
  separately; warm-up is excluded.

## Measured requests

| Run | Baseline goodput | Baseline successful p95 | Sustained goodput | Sustained successful p95 | Sustained 429s |
| --- | ---: | ---: | ---: | ---: | ---: |
| detection-20261023-01 | 1.900 req/s | 139.8 ms | 11.761 req/s | 491 ms | 41,286 |
| detection-20261023-02 | 1.933 req/s | 123.45 ms | 9.506 req/s | 494 ms | 44,896 |
| detection-20261023-03 | 1.933 req/s | 124.3 ms | 10.911 req/s | 464 ms | 42,365 |
| Mean | 1.922 req/s | 129.2 ms | 10.726 req/s | 483.0 ms | 42,849 |

All completed 200 responses passed the detection response-shape check; there
were no invalid 200s, unexpected 4xx, or 5xx responses. The 429s are capacity
rejections during sustained load, not successful inference throughput. Across
the sustained phases, 5,792 requests succeeded and 128,547 were rate-limited.
There were 134,363 sustained attempts total, so the unsuccessful-attempt rate
was 95.689%, including the 24 interrupted attempts. Baseline had 173 successes
from 175 attempts (1.143% unsuccessful). These rates describe the bounded
saturation workload; they are not service reliability or SLA estimates.

## Autoscaling and resources

Each run started with one ready replica and reached three. Recorded scale-out
delays were 39.188, 84.251, and 84.310 seconds from sustained-load start. The
first scale-in from three replicas occurred 379.289, 364.245, and 364.316
seconds after load ended. Return to one ready replica took 439.283, 424.242,
and 424.310 seconds. These results reflect the configured 300-second HPA
scale-down stabilization window plus sampling and controller timing; they are
not a promise of exact response times.

Scaling samples were taken about every five seconds. CPU and memory metrics
were incomplete during sustained load: complete serving-Pod metric coverage was
14/36, 16/36, and 16/36 samples. Do not treat these partial resource averages
as representative peak usage. Baseline coverage was 6/6 in all runs and
recovery coverage was 90/90, 85/87, and 85/87.
These historical runs did not record node CPU/memory or host RAM, so node
resource values are intentionally blank. The recorder now captures timestamped
node and Pod metrics with explicit unavailable/partial reason codes; subsequent
runs can populate both views without changing these historical observations.

## Container calibration

Separate hardened-container calibration used a one-CPU/1-GiB limit and tested
one versus two inference slots. In the sustained calibration snapshot, the
50-request sequential p50/p95 was 109.2/169.5 ms with one slot and 113.8/194.2
ms with two. Process high-water RSS under the tested concurrent 12-megapixel
uploads was 313.7 MiB and 473.0 MiB, respectively. During the separate
10-second two-client small-image load, successful throughput was 6.67 req/s
with one slot and 8.26 req/s with two; the one-slot run also returned 1,305
429s, while the two-slot run returned none.

The Deployment is configured for two inference slots. The observed calibration
improved throughput at the cost of higher per-request p95 and memory. The
measured RSS remained below the spec's 700 MiB review threshold, but these
measurements do not guarantee behavior for every image or host. Image limits
and container limits remain authoritative.

## Limitations and status

This is a single local Minikube node, not high availability. Replicas share
node CPU, metric-server values are windowed, and newly scheduled Pods can be
missing from individual samples. The measurements do not show linear throughput
growth with replica count. No AWS deployment, hosted CI run, or cloud HPA result
is claimed here. See `docs/EVIDENCE.md` for the verification ledger.

Replay the committed compact evidence without rerunning the workload:

```powershell
python scripts\analyze_experiments.py results\evidence\detection-20261023
```

This regenerates the same request/scaling rows and figures; node resource plots
are blank for these historical samples because node metrics were not captured.