# Phase 3 load experiments and scaling evidence

This phase measures the real sentiment API under baseline and sustained load,
then records HPA recovery. It uses only the dedicated local Minikube cluster.
The runner does not alter replica counts or HPA policy to manufacture scaling.

## What changed

- The load test paces warm-up/baseline at 100 ms between requests per virtual
  user, so those phases represent low demand. Sustained load remains unpaced.
- A single k6 setup timestamp aligns the workload phases with resource samples.
- Successful responses must match the model and response schema; rejections and
  interrupted attempts remain separate from successful predictions.
- The recorder accepts an explicit context and a version-matched kubectl path,
  records restarts/termination, and correctly handles missing Ready conditions.
- The experiment runner creates unique, bounded Jobs, retains raw evidence,
  waits for genuine idle recovery and stops on failed acceptance.
- The analyzer validates counts and phase boundaries before producing CSV rows
  and plots. Missing measurements remain blank rather than becoming fake zeros.

## Prerequisites

Complete [Phase 2](PHASE_2.md). Docker Desktop and the `ai-serving-local` cluster
must be running. There must be one ready sentiment replica, working CPU metrics,
and no other active load Job. Close unrelated CPU-intensive workloads where
practical and record anything that may affect measurements.

PowerShell from the repository root:

```powershell
$mk = Join-Path (Get-Location) '.validation/tools/minikube.exe'
& $mk -p ai-serving-local kubectl -- version
& $mk -p ai-serving-local kubectl -- -n ai-serving get pods,hpa,jobs
& $mk -p ai-serving-local kubectl -- -n ai-serving top pods
$client = Join-Path $env:USERPROFILE '.minikube/cache/windows/amd64/v1.37.0/kubectl.exe'
Test-Path $client
```

The first command downloads Minikube's matching client if necessary. Use the
actual path if your Minikube home differs. Do not use the older global kubectl
for this v1.37 cluster. The benchmark obtains its API key through the existing
Kubernetes Secret; you do not need to put the key in the command or results.

## Run three experiments

```powershell
& ./.venv-review/Scripts/python.exe scripts/run_experiments.py --kubectl $client --prefix my-phase3-run --runs 3
```

Choose a new lowercase prefix for each experiment set. Existing result folders
are refused rather than overwritten. Use `--runs 1` for a single manual exercise.
Allow roughly 30–35 minutes for three full load/recovery cycles, plus any initial
image download. Keep the terminal open. The runner prints progress every 30
seconds and writes each sample immediately to disk.

| Stage per run | Workload | Purpose |
| --- | --- | --- |
| Warm-up | 15 seconds, 2 virtual users, 100 ms pauses | Exclude initialization/cache warm-up from measured baseline |
| Baseline | 30 seconds, 2 virtual users, 100 ms pauses | Measure low-demand successful inference with one ready replica |
| Sustained | 180 seconds, 8 virtual users, no pause | Drive CPU demand and observe real HPA response |
| Recovery | No generated requests; at least 300 seconds after Job completion, bounded at 660 seconds | Observe scale-in and return to one idle replica |

The load generator runs inside the cluster through Service DNS with connection
reuse disabled, allowing new connections to reach additional replicas. It shares
the node's CPU and has its own 500m CPU limit, so generator overhead is part of
this experiment's limitations. Payloads are synthetic English sentences from
`loadtest/k6-script.js`. No optional model/provider route is exercised.

The runner uses the existing HPA: 70% of a 500m CPU request, 1–3 replicas,
one-Pod-per-minute scaling policies and 300-second scale-down stabilization.
It waits for actual idle metrics before beginning the next run.

## Manually watch scaling

In another terminal:

```powershell
$mk = Join-Path (Get-Location) '.validation/tools/minikube.exe'
& $mk -p ai-serving-local kubectl -- -n ai-serving get hpa --watch
```

Periodically inspect ready Pods and resource usage:

```powershell
& $mk -p ai-serving-local kubectl -- -n ai-serving get pods
& $mk -p ai-serving-local kubectl -- -n ai-serving top pods
```

You should see low baseline CPU, higher CPU during sustained traffic, additional
**ready** Pods, and then falling replica counts after the stabilization window.
Desired replicas alone do not prove available capacity. Initial unknown metrics
for new Pods are retained as missing data, not treated as zero CPU.

Each successful run prints `PASS <run-id>: load completed and idle recovery
observed`. This means the Job succeeded and the cluster recovered; inspect the
derived delays to confirm scale-out actually happened. A workload that never
scales can recover to idle without proving autoscaling.

## Files produced

Under `results/raw/<prefix>/`:

- `environment.json`: versions, initial deployment/HPA/Pod identity, source
  hashes and Git commit/dirty-worktree status. No Secret value is fetched.
- One directory per run with the actual Job configuration, k6 logs and summary,
  workload timestamp, resource/replica JSONL samples and completion record.
- Failed runs retain diagnostics; they do not receive a successful completion
  record. The runner removes only its own failed/interrupted Job to stop load.

Completed Jobs remain available for inspection. Do not delete their logs before
preserving the raw folder. To stop a run, use Ctrl+C in the runner terminal and
verify its Job is no longer generating load. HPA should then recover normally.

## Generate the measured table and figures

Install optional analysis dependencies in the local environment:

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.validation/uv-cache'
uv pip install --python .venv-review/Scripts/python.exe -r requirements-analysis.txt
& ./.venv-review/Scripts/python.exe scripts/analyze_experiments.py results/raw/my-phase3-run
```

If the host package network fails, the same analyzer can run in a disposable
container with the project mounted at `/workspace`. The serving image does not
need to be rebuilt or modified to install plotting dependencies.

```powershell
docker run --rm --entrypoint sh -e PYTHONPATH=/tmp/plotdeps -e MPLCONFIGDIR=/tmp/matplotlib `
  --mount "type=bind,source=$((Get-Location).Path),target=/workspace" -w /workspace `
  ai-model-serving:phase2 -c 'python -m pip install --target /tmp/plotdeps -r requirements-analysis.txt && python scripts/analyze_experiments.py results/raw/my-phase3-run'
```

The analyzer writes measured baseline/sustained/recovery rows to
`results/performance.csv`, figures to `results/figures/`, and compact measurement
evidence with hashes to `results/evidence/<prefix>/`. Reanalysis replaces only
rows for the same run IDs and preserves unrelated experiments. Raw folders remain
Git-ignored; the compact evidence and figures are intended for the report.

## How to interpret the measurements

- **Throughput:** body-validated successful predictions divided by phase seconds.
  Attempted request rate is reported separately in the raw summary.
- **Mean/p95 latency:** successful responses only; rejects must not make valid
  inference look faster. Measurements use the k6 client's millisecond timing.
- **CSV error rate:** all unsuccessful attempts divided by attempts, including
  429 rejections and interrupted boundary requests. The notes show each category.
  k6's built-in failure rate intentionally accepts 429 and is a different metric.
- **CPU/memory:** mean of total serving-Pod usage per complete sample during the
  phase, not a per-Pod mean. Notes disclose sample coverage. These are metrics
  retrieved during the phase, not precisely phase-aligned CPU windows; sparse
  coverage must not be presented as representative whole-phase utilization.
- **Scale-out delay:** sustained-load start to the first sample with more ready
  replicas than the baseline's initial count.
- **Scale-in delay:** load end to the first sample with fewer ready replicas than
  the sustained phase's peak. Return-to-one delay is recorded separately.
- **Recovery rows:** no artificial latency/throughput values; those fields stay
  blank because no recovery workload is generated.

Scale timing has approximately a sample interval of resolution, plus small k6
setup-return and sequential API-read overhead. Metrics Server CPU samples are
windowed and may lag workload changes. The local Docker node is limited to
4 CPUs/6 GiB even though Kubernetes reports a larger WSL host capacity.

Comparing a paced low-load baseline with a high-load autoscaling run does **not**
prove a causal performance improvement from autoscaling. That would require an
additional fixed-one-replica high-load control under the same workload. This
phase demonstrates behavior and measures service outcomes, including overload.

## Failure diagnosis and acceptance

If scaling does not occur, inspect HPA events, actual CPU relative to requests,
successful traffic, overload rate, Pod readiness and node capacity. Preserve the
failed/limited run before changing workload or resources. Do not lower the HPA
target simply to claim a successful experiment without explaining the change.

```powershell
& $mk -p ai-serving-local kubectl -- -n ai-serving describe hpa ai-model-serving
& $mk -p ai-serving-local kubectl -- -n ai-serving get events --sort-by=.metadata.creationTimestamp
```

Phase 3 is accepted when real raw measurements exist, scale-out and scale-in are
observed (or a measured limitation is reported honestly), CSV rows trace to those
measurements, and the analysis explains errors, timing and single-node limits.
See [results/ANALYSIS.md](../results/ANALYSIS.md) for the executed experiment report.
