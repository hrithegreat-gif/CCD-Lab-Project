# Verification ledger

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
