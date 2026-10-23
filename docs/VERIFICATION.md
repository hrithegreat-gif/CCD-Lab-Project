# Verification ledger

## Phase 4 delivery verification on 23 October 2026

Local final checks: **296 Python tests passed without skips** (10.58 s),
**81 frontend tests passed**, and cleanup guard behavioral checks passed.
The authenticated in-cluster Service smoke passed with real positive/negative
predictions, auth rejection, validation and metrics. Namespace cleanup preview
and deletion succeeded with the matching kubectl; the namespace absence check
returned no resources. Phase 3 evidence was preserved before deleting its Jobs.

Added [the Phase 4 guide](PHASE_4.md), [timed demo](DEMO.md) and
[final report](FINAL_REPORT.md). CI now includes experiment tests and PowerShell
cleanup guards. Hosted CI for the delivered review branch is pending until its
actual run completes; local tests do not establish that gate. Team attribution
is explicitly unassigned pending team confirmation. Optional extensions remain
deferred, and no AWS workflow was dispatched.

## Phase 3 measurements and final verification on 23 October 2026

**Phase 3 passed locally.** Three sequential experiments executed on 22 October
with unchanged HPA settings; each observed one, then three, then one ready
serving replica. Analysis was finalized on 23 October. See the
[report](../results/ANALYSIS.md), [CSV](../results/performance.csv) and
[manual verification guide](PHASE_3.md).

| Check | Actual result |
| --- | --- |
| Three bounded k6 Jobs | All completed successfully; original summaries and source snapshots preserved |
| Baseline | 17.533?17.600 successful rps; p95 15 ms |
| Sustained load | 104.000?117.239 successful rps; p95 96?99 ms; roughly 57?59% HTTP 429 |
| Ready scale-out | First additional ready Pod 70.732?84.360 s after sustained start |
| Recovery | First scale-in 350.768?364.404 s; return to one 410.775?424.405 s after traffic ends |
| Restarts/errors | Zero recorded serving-Pod restarts, completed 5xx or invalid-200 responses; boundary interruptions retained separately |
| Resource evidence | Sustained complete samples only 14/36, 16/36, 16/36; missing values and window lag explicitly disclosed |
| Full Python suite | **296 passed, no skips**, 11.31 s; includes real offline ONNX integration and five new experiment tests |
| CSV/report/plots | Nine measured rows, three scaling/resource figures and hash-linked compact evidence |
| Final cluster check | One ready serving Pod, zero restarts, HPA CPU 0%/70%; all three Jobs Complete |

The experiment snapshots predate two reporting fixes: aggregate attempts now
include interrupted requests, and the recorder reads the HPA target from spec.
Analysis uses the already-correct per-phase counters and captured HPA spec;
original measurements were not rewritten. Tests cover both corrections.
The CSV schema check now handles quoted commas in measured notes.

No fixed-replica high-load control was run, so this does not isolate a causal
throughput gain from autoscaling. Resource means use incomplete, windowed
retrieval-time observations and are not precise phase utilization estimates.
Hosted CI, final demonstration/cleanup acceptance and optional cloud/provider
execution remain outside this completed phase. Older unexecuted-performance
claims below describe earlier verification states and are superseded here.

## Phase 2 container and Kubernetes acceptance on 22 October 2026

**Phase 2 passed locally.** Docker Desktop was started, Minikube was installed
under `.validation/tools`, and the dedicated `ai-serving-local` cluster was
created. No AWS resources, paid providers, hosted CI or load benchmarks were used.

| Check | Actual result | Scope |
| --- | --- | --- |
| Minikube download | v1.39.0, published SHA-256 matched | `776386465ded2cf610ae397fe302de32c44d12134b5ce7ce98ab05bd7713b360` |
| Docker build | Initial `--no-cache` build passed; corrected entrypoint rebuilt successfully | Docker Linux engine 29.2.1; baked immutable ONNX model verified at build time |
| Hardened container | Real authenticated smoke, UID 10001 and `pip check` passed | Read-only root, dropped capabilities, 1 CPU/1 GiB, root-owned group-writable cache; temporary containers removed |
| Local cluster | Minikube v1.39.0, Kubernetes v1.37.0, containerd 2.3.4 | Requested Docker node limits 4 CPUs/6144 MiB, confirmed as 4,000,000,000 NanoCPUs and 6,442,450,944 bytes |
| Image import and rollout | Passed | Namespace/Secret ordering, rendered manifests, offline baked model, ready Pod |
| Service port-forward smoke | Passed | Real predictions, auth rejection, metadata, 404/422 validation and metrics |
| In-cluster DNS/Service smoke | Passed | Full smoke script piped into Pod, targeting `ai-model-serving.ai-serving.svc.cluster.local:8000` |
| Metrics Server | v0.9.0 ready; node and Pod resource readings returned | Observed fixed-image Pod at 9m CPU/180Mi memory; point-in-time observations, not a benchmark |
| HPA | `ScalingActive=True`, `ValidMetricFound`, CPU 1%/70%, one replica | Metrics availability only; no scale-out/in experiment performed |
| Fixed-image rollout recovery | Passed again after explicit restart, zero restarts | Replacement Pod ready and full in-cluster real-inference smoke passed |
| Follow-up stability check | Same replacement Pod still `1/1 Running`, zero restarts at approximately 8 hours | HPA CPU 0%/70%, Pod snapshot 3m CPU/178Mi memory; idle observations only |
| Manifest/workflow checks | 54 passed in 1.20 s | Includes regression guard for Kubernetes-like cache ownership in both CI smoke workflows |
| PowerShell helper syntax | Passed | Installer, container acceptance and local deployment scripts |

Final corrected image manifest-list identity:
`sha256:65069a3d824af1e2eafa2c03f9a8381a1aa9aee088a58276a08bbd37081a10f4`.
The initial clean build was followed by a rebuild of the entrypoint-dependent
layers after fixing the live deployment bug; the final hardened smoke and
Kubernetes checks used the corrected image.

### Defect found and corrected

The original `cp -a` cache copy tried to preserve timestamps on the root-owned
`emptyDir` mount. Each new Pod initially exited with `Operation not permitted`,
then retried successfully because the completion marker had already been copied.
The entrypoint now copies entries without archive metadata and writes the marker
only after all copies succeed. Two fixed-image Pods were verified ready with
zero restarts. The container helper and both CI smoke workflows now use a
root-owned, group-writable cache mount to expose this regression. The local
deployment helper also rejects startup restarts even when a retry becomes ready.

### Capacity and evidence limits

Kubernetes reported node allocatable capacity as 16 CPUs and 12,087,488 KiB RAM,
despite Docker enforcing a 4-CPU/6-GiB node-container limit. Use the enforced
limits when interpreting Phase 3 capacity; `kubectl top nodes` percentages alone
can misrepresent available compute in this nested setup. Initial HPA unknown
metrics resolved after fresh Pod samples were collected; they were not counted
as successful readings.

Sanitized node/Pod/HPA JSON and image identity are kept under ignored
`.validation/phase2/`. Temporary port forwards were stopped after acceptance.
The dedicated cluster remains available for manual testing; stop it with the
command in [PHASE_2.md](PHASE_2.md) when finished. The old records below describe
earlier states and do not supersede this acceptance. Formal scaling experiments,
performance rows, hosted CI and optional AWS/provider acceptance remain pending.

## Phase 1 revalidation on 21 October 2026

The local runtime gate now passes in `.venv-review` using project-local Python
3.12.14. The original `.venv` was left untouched because it references a missing
system interpreter. `scripts/setup-phase1.ps1` creates the replacement using
ignored `.validation/python` and `.validation/uv-cache` directories. Its final
form was rerun successfully offline; it installs no global Python registry/bin
entries and preserves `.env`. PowerShell's script policy required a child-process
execution-policy option, documented in `PHASE_1.md`; no persistent policy changed.

| Check | Actual result | Scope |
| --- | --- | --- |
| Dependency metadata before browser tooling | 42 distributions, 91 active requirements; all baseline/dev pins match | Fresh declared requirements installation |
| Pinned model preparation, offline | Passed; 68,293,328 asset bytes; positive and negative labels verified | Actual cached ONNX weights, immutable registry revision |
| Full Python suite | **291 passed in 11.39 s**, no skips | Includes real-model integration and controlled fault/extension tests |
| Frontend contract suite | **81 passed**, no failures/skips | Node v24.14.0 |
| Authenticated live HTTP smoke | Passed on loopback port 8883 | Health, metadata, rejected unauthenticated request, real positive/negative predictions, 404/422 errors, metrics |
| Studio/OpenAPI HTTP | Both returned usable responses | Studio HTTP 200 and expected API title |
| Edge browser acceptance | **22 checks passed**, Playwright 1.58.0 | Real sentiment desktop/mobile/truncation; optional provider/auth/error cases remain HTTP fixtures |
| Visual inspection | Desktop and 390px mobile screenshots inspected | Real successful inference visible, no page overflow observed |
| Final setup rerun, offline | Passed; 45 distributions, 94 active requirements | Includes optional browser test tooling; all baseline/dev pins still match |

Browser evidence is in ignored `.validation/phase1-browser/` (screenshots and
`browser-checks.json`). Optional Playwright tooling was installed only in the
local environment; production requirements were not changed. No application
logic changes were needed. Single-request timings remain functional observations,
not benchmarks. Temporary HTTP servers were stopped after validation.

Docker, Kubernetes, HPA experiments, hosted CI, optional real model/providers,
AWS and performance measurements remain outside this completed Phase 1 gate.
The older records below are retained as history, not fresh measurements.

Checked on **2026-10-13 (Asia/Kolkata)** in the user's working tree. This
supersedes the earlier 32-test Phase 1 record. These are local checks, **not**
a GitHub Actions run, successful AWS deployment, or performance benchmark.
The initial checks below predate the first implementation commit; the
frontend section records later local checks performed before its PR.
No application container image was built or published during these checks.

## Executed and passed

| Check | Actual result | Scope |
| --- | --- | --- |
| Full Python suite | **233 passed**, Python 3.12.14 / Windows, 9.49 s | Includes four real cached ONNX integration cases, 54 manifest/load-script cases and 62 extension cases; controlled doubles used where appropriate |
| Real HTTP smoke | Passed on temporary localhost port 8883 | Public health, authenticated metadata/metrics, unauthenticated rejection, actual positive/negative inference, 404/422 validation |
| Baseline dependency metadata | **43 visible distributions, 91 active requirements**, all direct pins match | Python-visible default markers and Requires-Python; not optional model-profile compatibility |
| Terraform formatting / initialization / validation | Passed, Terraform 1.15.8, AWS provider 5.100.0 | `init -backend=false -lockfile=readonly`; no AWS credentials or account calls |
| Terraform native policy/bootstrap tests | **8 passed** using mock providers | IAM resources/denies/KMS context, OIDC deployment scope, SSM document, early auto-stop, storage and logs |
| Functional archive safety | **17 passed** under Ubuntu WSL2 with real GNU tar and a stubbed AWS CLI | Safe extraction/deploy; traversal, symlink, hardlink, corrupt archive and SHA mismatch refusal; per-commit result prefix |
| Load-test accounting semantics | Passed through the real script imported under Node with stubbed k6 modules | Valid versus malformed 200, 429/5xx/other outcomes, interrupted attempts, disjoint phase boundaries and per-phase successful throughput; not a real k6 run |
| Shell / load-test syntax | Passed | Linux `bash -n` on shell helpers; Node syntax check of the load-test script |
| Required-file inventory / whitespace | Passed | All inventory entries exist; `git diff --check` clean |

The archive suite was also attempted under **Git Bash on Windows** and failed
on Unix permission/symlink operations. It was subsequently run successfully
under actual Ubuntu WSL2; the Windows failure is not recorded as a pass.
CI runs this suite on Ubuntu, not Git Bash.

The live smoke observed positive confidence `0.9998831748962402` and negative
confidence `0.9998059868812561`. Individual client-visible processing times
were approximately 10.294 ms and 8.742 ms respectively. These are **single
functional observations**, not throughput, p95, cold-start, or timing promises.
The temporary HTTP server is stopped after validation.

## Repeat local checks

From the repository root, with `uv` and the real model cache available:

```powershell
$env:MODEL_CACHE_DIR='<absolute path to the prepared model cache>'
$env:MODEL_LOCAL_FILES_ONLY='true'
$env:PYTHONDONTWRITEBYTECODE='1'
uv run --python 3.12 --no-project --with-requirements requirements-dev.txt python -m pytest tests/ -q -p no:cacheprovider
uv run --python 3.12 --no-project --with-requirements requirements-dev.txt python scripts/check_dependencies.py
uv run --python 3.12 --no-project python scripts/verify_structure.py
$env:AWS_EC2_METADATA_DISABLED='true'
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform init -backend=false -input=false -lockfile=readonly
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test -no-color
node --check loadtest/k6-script.js
```

On Linux (or from WSL with the repository root as the current directory):

```bash
bash infra/terraform/tests/deploy_doc_safety_test.sh
for f in scripts/*.sh infra/terraform/tests/*.sh; do bash -n "$f" || exit; done
```

A cold cache needs [model preparation](../scripts/prepare_model.py) and network
access before offline integration tests can pass. The permanent
[dependency checker](../scripts/check_dependencies.py) inspects metadata visible
to the running Python, including uv overlays. A native `uv pip check` that sees
zero packages is **not** dependency evidence; CI uses regular `pip check` in
its installed environment. Neither check proves optional-extra imports work.

For live acceptance, start the API with a disposable nonempty `API_KEY`, run
[the smoke script](../scripts/smoke_test.py) using that key in the environment,
then stop the server. Do not put a production key in command arguments or logs.

## Optional models: tested boundaries, not real-provider acceptance

The adapters' configuration, disabled/no-download behavior, route contracts,
error sanitization, timeouts, concurrency and shutdown are tested with
controlled engines/clients. The final isolated extension suite has **62 cases**
and is included in the full suite above.

Profiles include the core runtime. Qwen replaces exactly two baseline Hugging
Face pins in its dedicated environment; an inheritance regression guards this.
The CPython 3.12/Linux x86_64 CPU Torch wheel was found on its index. Non-Torch
Qwen and baseline + Kitten metadata graphs resolved, but **the complete graph
with the exact CPU Torch pin was not verified**. No heavy optional wheels or
weights were installed and no Groq request was made. See the
[profile details and operational limitations](MODEL_EXTENSIONS.md).

## Inference Studio frontend — 2026-10-13

The [frontend guide](FRONTEND.md) describes the demo at
`http://127.0.0.1:8000/`. FastAPI serves the UI directly. The locally verified
baseline uses the real cached quantized ONNX DistilBERT model; optional
adapters remain disabled.

| Check | Actual result | Scope |
| --- | --- | --- |
| Isolated frontend PR Python suite | **291 passed**, Python 3.12 / Windows, 9.72 s | Full suite on the actual publication tree, including 48 studio route/security cases and 10 shipped-asset structure checks; unrelated Floci tests are excluded |
| Frontend JavaScript | **81 passed**, both module syntax checks passed | Offline Unicode/UTF-8 limits, strict API schemas, same-origin/auth scoping, consent, errors/cancellation and result validation |
| Edge / Playwright acceptance | **22 checks passed**, Playwright 1.58.0 / installed Microsoft Edge | Real positive/negative/truncated/mobile ONNX inference, keyboard tabs, JSON download, navigation and in-memory history; optional-provider/auth/error cases are explicit HTTP fixtures |
| Visual verification | Desktop 1440 px and mobile 390 px; no mobile horizontal overflow | Actual successful inference shown in [desktop](../results/frontend/demo-desktop.png) and [mobile](../results/frontend/demo-mobile.png) screenshots |
| Publication source consistency | Passed, SHA-256 equality | UI, server integration and tests match the previously browser-verified implementation byte-for-byte |
| Patch formatting | `git diff --check` passed | Frontend commit is separate from the existing serving foundation |

Outputs and latencies are not seeded or fabricated. Single-run timings are
**not benchmarks**. Optional generation/speech/transcription, API-key rejection,
consent, 429/503/504 errors and cancellation are fixture contract checks, not
real optional-model loading, audio-quality or paid-provider acceptance.
Keys are kept out of application storage, URLs and result/history content.

The [frontend CI job](../.github/workflows/ci.yml) participates in the existing
CI gate. These are local pre-publication results, not proof of a GitHub Actions
run. No AWS provisioning or paid provider calls occurred. Unrelated local
Floci tooling is deliberately not part of this frontend PR.

## Still required before claiming a cloud demo

- **Docker build/run:** the Windows Docker client is present, but no working
  daemon was available during implementation. The image and hardened container
  smoke remain unexecuted locally.
- **Kubernetes:** no cluster/context was configured. Apply, admission/runtime
  behavior, Metrics Server readings, ECR pull credentials, readiness, resource
  measurements and HPA scale-out/scale-in remain unverified.
- **AWS:** no plan against a real account, apply, instance launch, SSM session,
  CloudWatch ingestion, OIDC exchange, ECR push or S3 deployment occurred.
  Budget alerts are delayed notifications, not a spending cap; stopped storage
  still costs money. See the [budget](BUDGET.md) and [runbook](AWS_RUNBOOK.md).
- **GitHub Actions:** at local validation time, workflows were authored and
  inspected, not run. Check the published commit's GitHub Actions result;
  a green same-commit CI gate is required before the protected manual deploy.
- **Performance:** no formal load/HPA/cold-start experiment was performed.
  [The results CSV](../results/performance.csv) remains header-only. Follow the
  [evidence requirements](../results/README.md); never fill rows from smoke
  observations, pricing estimates, or simulated outputs.
- **Optional models:** real Qwen/Kitten load/inference/audio playback and paid
  Groq text/Whisper acceptance require separate installation, resource sizing,
  security/cost review and operator approval.

No AWS resources were provisioned and no real Groq or Bedrock requests were
made during local validation. Publishing this implementation does not authorize
Terraform apply or the manual AWS deployment workflow. Follow the
[demo checklist](DEMO.md) and [implementation plan](IMPLEMENTATION_PLAN.md)
for the remaining execution gates.
