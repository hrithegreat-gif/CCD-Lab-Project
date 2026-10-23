# Four phase implementation and manual verification plan

Updated: **21 October 2026 (Asia/Calcutta)**

This plan groups the work remaining after the [implementation review](IMPLEMENTATION_STATUS.md) into exactly four phases. It describes planned fixes and acceptance checks; it does not claim those fixes or deployments have already been completed. Existing application features will be preserved and repaired where verification exposes a defect.

The required target is a local sentiment-serving platform with demonstrated Kubernetes autoscaling. Optional AWS and model extensions are listed in Phase 4 but do not block MVP completion. This plan supersedes the earlier cloud-focused implementation stages; existing cloud contracts remain documented in [AWS_RUNBOOK.md](AWS_RUNBOOK.md), [CI_CD.md](CI_CD.md), and [MODEL_EXTENSIONS.md](MODEL_EXTENSIONS.md).

| Phase | Outcome | Completion evidence |
| --- | --- | --- |
| 1 | Reproducible local API and real model | Backend tests, real HTTP smoke and browser checks |
| 2 | Working container and local Kubernetes deployment | Clean image build, ready Pods, Service inference and resource metrics |
| 3 | Demonstrated scaling with measured results | Scale-out/in time series, load summaries and populated performance table |
| 4 | Repeatable CI and finished delivery | Green hosted CI, corrected documentation and reproducible demo |

## Before running the commands

All commands below use **PowerShell from the repository root**. Separate terminals must use the same repository directory. Execute each step in order and check its output before continuing; Windows PowerShell does not automatically stop after every failed native command. For native commands, `$LASTEXITCODE` should be `0` unless an expected failure is explicitly being tested.

Local test examples use a disposable API key, `local-review-only`, solely to make authentication tests reproducible. Do not use it for public or cloud deployments. Keep optional model flags disabled for the core acceptance runs. No commands in the core four-phase path provision AWS or call paid model providers.

The selected local cluster path is **Minikube with the Docker driver**, using a dedicated profile named `ai-serving-local`. Install Minikube and have Docker Desktop running Linux containers before Phase 2. The allocation shown is a starting configuration, not a measured capacity claim. See the official [start options](https://minikube.sigs.k8s.io/docs/commands/start/), [image loading commands](https://minikube.sigs.k8s.io/docs/commands/image/), and [addon commands](https://minikube.sigs.k8s.io/docs/commands/addons/).

## Phase 1: Repair the local environment and verify the application

### Fixes and creation planned

1. Restore a working Python 3.12 environment. The existing `.venv` points to a missing interpreter; create a separate `.venv-review` instead of deleting it.
2. Resolve the uv cache-access problem through a writable project-local cache.
3. Install and check the pinned runtime/test dependencies. Repair demonstrated dependency problems rather than upgrading packages indiscriminately.
4. Prepare the immutable sentiment-model assets and rerun real inference tests.
5. Fix any backend, authentication, startup, validation or frontend regressions uncovered by the checks.
6. Update the environment and verification documentation with fresh outcomes, clearly separating real model tests from doubles.

Likely files: `requirements*.txt`, `.gitignore` for temporary environment/cache exclusions, relevant `app/` and `tests/` files if defects appear, `docs/ENVIRONMENT.md`, and `docs/VERIFICATION.md`.

### Manual test A: Environment and automated checks

The first model/dependency installation requires network access. Use a new `.venv-review` directory; if one already exists, inspect it before deciding to reuse it.

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.validation/uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path (Get-Location) '.validation/python'
$env:UV_LINK_MODE = 'copy'
uv python install 3.12 --no-bin --no-registry
uv venv --python 3.12 .venv-review
uv pip install --python .venv-review/Scripts/python.exe -r requirements-dev.txt
$env:MODEL_CACHE_DIR = Join-Path (Get-Location) 'models/cache'
$env:MODEL_LOCAL_FILES_ONLY = 'false'
$env:API_KEY = ''
$env:ENABLE_QWEN = 'false'
$env:ENABLE_KITTEN = 'false'
$env:ENABLE_GROQ = 'false'
$env:ENABLE_GROQ_TRANSCRIPTION = 'false'
& ./.venv-review/Scripts/python.exe scripts/check_dependencies.py
& ./.venv-review/Scripts/python.exe scripts/prepare_model.py --verify
$env:MODEL_LOCAL_FILES_ONLY = 'true'
& ./.venv-review/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider
node --test tests/frontend/studio-core.test.mjs
```

**Expected:** dependency checks succeed; real positive/negative predictions succeed; pytest finishes with no failures/errors; frontend tests pass. Test totals can change with fixes, so do not use an old count as the only pass criterion. The real-model tests must actually execute, not be skipped or replaced with stubs.

### Manual test B: Live API and UI

In terminal A, after the setup above:

```powershell
$env:API_KEY = 'local-review-only'
& ./.venv-review/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

In terminal B:

```powershell
$env:API_KEY = 'local-review-only'
& ./.venv-review/Scripts/python.exe scripts/smoke_test.py --base-url http://127.0.0.1:8000
curl.exe -s -o NUL -w '%{http_code}' http://127.0.0.1:8000/api/models
curl.exe -s -o NUL -w '%{http_code}' -H 'Authorization: Bearer local-review-only' http://127.0.0.1:8000/api/models
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

**Expected:** smoke checks pass; the unauthenticated metadata request returns `401`; the authenticated request returns `200`; readiness reports `ready`.

Open `http://127.0.0.1:8000/`, enter the disposable key through Session access, and analyze one clearly positive and one clearly negative sentence. Confirm actual labels and processing times appear. Open `/docs`, authorize if required, and try an unknown model (404) and blank input (422). Check `/metrics` with the Bearer header and confirm request counters exist. Stop the server with Ctrl+C when finished.

### Failure diagnosis

- Missing interpreter: use the new environment's explicit Python path.
- Cache access denied: confirm `UV_CACHE_DIR` points to the writable project directory.
- Model unavailable: run preparation online first, then enable offline mode.
- 401 with smoke tests: ensure the server and client have the same `API_KEY`.
- 503 readiness: inspect model initialization logs; do not treat process liveness as model success.

### Phase 1 completion gate

- [x] Dependencies and real-model backend tests pass.
- [x] Live smoke, authentication and UI checks pass.
- [x] Current environment and verification records are updated.
- [x] Temporary environments, caches and secrets are excluded from commits.

Completed on 21 October 2026; see [VERIFICATION.md](VERIFICATION.md). For repeat
setup, use the fail-fast `scripts/setup-phase1.ps1` command documented in
[PHASE_1.md](PHASE_1.md) rather than recreating an existing environment blindly.

## Phase 2: Verify Docker and establish local Kubernetes

### Fixes and creation

- Build and verify the existing non-root image with actual offline model inference.
- Add `scripts/test-container.ps1` for hardened container smoke checks and automatic test-container cleanup.
- Add `scripts/install-local-tools.ps1` for checksum-verified project-local Minikube installation.
- Add `scripts/deploy-local.ps1` for image import, namespace/Secret ordering, deployment and metrics checks.
- Use Kubernetes v1.37.0 and Minikube's matching kubectl; the global v1.34.1 client is too old for this cluster.
- Complete the local-first runbook and correct unsupported cloud verification claims.

### Manual testing

The detailed commands, expected output, recovery checks and troubleshooting are
in [PHASE_2.md](PHASE_2.md). That guide replaces the preliminary global-kubectl
examples from this plan. From the repository root in PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install-local-tools.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test-container.ps1 -NoCache
$env:API_KEY = 'local-review-only'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/deploy-local.ps1
```

Check each command succeeds before continuing. The container script prints its
PASS message only after real authenticated inference, validation, metrics,
dependency and UID checks. Deployment must show ready Pods, actual resource
readings and usable HPA metrics.

Keep this running in terminal A:

```powershell
& ./.validation/tools/minikube.exe -p ai-serving-local kubectl -- -n ai-serving port-forward service/ai-model-serving 8002:8000
```

In terminal B:

```powershell
$env:API_KEY = 'local-review-only'
& ./.venv-review/Scripts/python.exe scripts/smoke_test.py --base-url http://127.0.0.1:8002
```

Expected: all real inference and authentication checks pass. Follow the Phase 2
guide for cluster-DNS checks, rollout replacement and stop/resume. Phase 3
remains responsible for workload-driven replica changes and benchmarks.

### Phase 2 completion gate

- [x] Clean Docker build and hardened-container smoke pass.
- [x] Cluster Pods and Service serve real inference.
- [x] Metrics Server reports CPU/memory and HPA receives metrics.
- [x] Local setup/image-loading instructions are reproducible.

Completed on 22 October 2026. Live testing exposed and fixed a cache-copy
ownership/timestamp error; corrected Pods start with zero restarts. See
[VERIFICATION.md](VERIFICATION.md) for the tested image and evidence. The current Phase 3 runner explicitly selects the matching Minikube client and context.

## Phase 3: Demonstrate autoscaling and capture performance evidence

### Fixes and creation

- Pace warm-up and baseline separately from sustained traffic; validate actual
  inference responses and retain 429/interrupted-request accounting.
- Record workload start time, ready replicas, HPA decisions, restarts and resource
  samples using an explicitly selected local context.
- Run three bounded experiments with unique Jobs and genuine idle recovery.
- Preserve raw measurements and exact workload sources; derive CSV rows, scaling
  plots and a report that explains missing metrics and overloads.

### Manual verification

Follow [PHASE_3.md](PHASE_3.md) for prerequisites, executable PowerShell commands,
watch commands, analysis and failure diagnosis. The key sequence is:

```powershell
$client = Join-Path $env:USERPROFILE '.minikube/cache/windows/amd64/v1.37.0/kubectl.exe'
& ./.venv-review/Scripts/python.exe scripts/run_experiments.py --kubectl $client --prefix my-phase3-run --runs 3
# After installing requirements-analysis.txt as described in the guide:
& ./.venv-review/Scripts/python.exe scripts/analyze_experiments.py results/raw/my-phase3-run
Import-Csv results/performance.csv | Format-Table run_id,scenario,throughput_rps,p95_latency_ms,ready_replicas_min,ready_replicas_max
```

Use a new prefix each time. Expect successful predictions, ready scale-out above
one replica and eventual idle scale-in. A successful Job alone does not prove
scaling: inspect ready-replica samples and derived delays. Expect overload
rejections under unpaced load; report these alongside successful throughput.
Recovery generates no traffic, so its latency/throughput fields remain blank.

### Phase 3 completion gate

- [x] Real successful load requests and usable CPU/memory samples exist.
- [x] Scale-out and scale-in are observed, or a genuine measured limitation is documented without claiming scaling success.
- [x] Performance CSV contains actual data, not illustrative estimates.
- [x] Results analysis explains throughput, latency, overloads, scaling delays and single-node limitations.

Completed: three local experiments on 22 October, analysis verified 23 October 2026.
See [the measured report](../results/ANALYSIS.md) for results and limitations.

## Phase 4: Finish CI, documentation and the demonstration

Detailed implementation and executable acceptance commands: [PHASE_4.md](PHASE_4.md).
Final submission narrative: [FINAL_REPORT.md](FINAL_REPORT.md).

### Fixes and creation planned

1. Run the existing hosted CI and fix demonstrated Linux, dependency, image-build, manifest or Terraform validation failures.
2. Reconcile README, Kubernetes instructions, environment records and the verification ledger with actual outcomes.
3. Create a complete demo sequence and final results narrative, including team contributions and limitations.
4. Verify cleanup for the dedicated local profile.
5. If selected separately, complete optional model/cloud/emulator/queue/dashboard work using the acceptance checks below. MVP delivery does not depend on these extensions.

Likely files: `.github/workflows/ci.yml`, affected tests/scripts, `README.md`, `docs/KUBERNETES.md`, `docs/VERIFICATION.md`, `docs/DEMO.md`, and `results/ANALYSIS.md`.

### Manual test A: Hosted CI

Publish the reviewed commit through your normal Git workflow, then open the repository's **Actions → CI** page. A push to `main`, a pull request, or the workflow's manual Run workflow action can trigger it. Select the correct branch/commit.

Confirm all jobs succeed: Python/real-model tests, manifest checks, Docker build/container smoke, Terraform checks, frontend tests, and the aggregate CI gate. Open logs and check that real tests ran rather than being skipped. Record the run URL and commit SHA in the ledger. A local green suite or workflow YAML file is not hosted-CI evidence.

### Manual test B: Reproducible demo

Follow the updated README from a fresh terminal, ideally on a clean checkout/environment. Confirm there are no undocumented caches or image-loading assumptions. Rehearse this sequence in 5–8 minutes, using preserved evidence for slow scale-in when necessary:

1. Explain the architecture and fixed single-node capacity.
2. Submit real positive/negative sentiment requests through UI or Swagger.
3. Show ready Pods, Service, HPA and numeric resource readings.
4. Show the controlled workload and replica changes, live or clearly labeled recorded evidence.
5. Show actual throughput, mean/p95 latency, errors and recovery measurements.
6. Show the successful same-commit CI run.
7. Explain limitations and cleanup.

Do not present prerecorded evidence as a live result. Every number shown should trace to a saved experiment.

### Manual test C: Local cleanup

Follow [PHASE_4.md](PHASE_4.md) for matching-client preview, namespace confirmation,
absence checks, redeployment and profile stop/resume. The cleanup helper accepts
`-Kubectl` and checks the current context before any deletion. Offline behavioral
checks exercise preview, wrong-context rejection, wrong-confirmation rejection
and correctly scoped deletion.

### Optional work and manual acceptance

| Optional work | Fix/create if selected | How to verify manually |
| --- | --- | --- |
| Qwen/Kitten/Groq | Resolve dedicated dependencies, fix real adapters, size resources | Follow `MODEL_EXTENSIONS.md`; enable one adapter at a time; `/api/capabilities` must report ready; generation must return real text, speech a playable WAV, and transcription recognizable supplied speech. Disable it and confirm it no longer accepts inference. Provider calls require separate configuration and may incur charges. |
| Prometheus/Grafana | Scrape deployment, dashboards and documented access | Generate requests, query actual request/latency series and confirm dashboard values change. An empty dashboard or only `/metrics` is not completion. |
| Floci | Useful S3/SQS/ECR integration and tests against verified supported operations | Run against the emulator endpoint with dummy credentials, perform upload/download or send/receive, and verify persisted payload/results. Clearly label emulation. |
| Async jobs | Queue, worker, durable status/results, retries, idempotency and failure handling | Submit a job, observe queued/processing/succeeded and retrieve the real result; test failure and duplicate delivery; restart worker and verify completed results are retained. Do not add endpoints before the pipeline exists. |
| AWS deployment | Execute prepared infrastructure/deployment only after separate cost review and authorization | Follow `AWS_RUNBOOK.md`; verify SSM access, real authenticated inference, image identity, CloudWatch logs, HPA evidence, stop/start behavior and cloud cleanup. Local Terraform tests do not count as deployment. |

These optional systems need their own concrete execution instructions when selected; they are not silently included in the default Minikube commands.

### Phase 4 completion gate

- [x] Same-commit hosted CI succeeds.
- [x] README/setup and final demo are repeatable.
- [x] Verification claims match actual evidence.
- [x] Cleanup works for the intended local namespace/profile.
- [x] Optional extensions are explicitly marked verified, pending or deferred.

Local cleanup/redeployment and hosted CI passed on 23 October 2026; see the
[verification ledger](VERIFICATION.md) for the exact implementation commit/run.
Confirm team names/contributions before submitting the final report.

The required project is complete when the four core phase gates are satisfied, with any unachieved scaling behavior transparently documented against the specification's limitation allowance. Optional infrastructure should never be used to obscure missing local acceptance evidence.
