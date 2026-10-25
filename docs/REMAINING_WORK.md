# Remaining project implementation and delivery work

Implementation follow-up: 25 October 2026. This checklist preserves the
original audit items and marks work verified in this follow-up. Review baseline:
`8408e13`.

The local object detection MVP is substantially implemented. The remaining local delivery work is mainly reproducibility, hosted CI evidence, measurement completeness, and the final report/demo. AWS infrastructure code exists but cloud execution and acceptance remain unverified. AWS and the other extensions below are optional under the original build specification.

This checklist distinguishes missing code, documentation fixes, validation work, and optional extensions. Passing tests do not prove every deployment path works. Cloud accounts and hosted CI were not inspected, and Docker, browser, Terraform, and cluster experiments were not rerun during this review; their historical status comes from the evidence ledger.

## What is already implemented

| Area | Existing implementation and evidence |
| --- | --- |
| Real inference | Pinned YOLOv8n ONNX CPU model, verified asset digest, warm-up and readiness; `app/inference.py`, `app/model_assets.py`, `app/model_registry.py` |
| API | Raw image detection, model metadata, health, OpenAPI, input validation, bounded concurrency, timeout handling; `app/main.py`, `app/imaging.py`, `app/postprocess.py` |
| Security and observability | Bearer authentication, bounded bodies, sanitized logs, Prometheus endpoint, cancellation/capacity tests |
| Studio | Upload/drop/sample images, thresholds, boxes/table, JSON and annotated PNG export, tab-memory key/history; `app/static/`, `app/studio.py` |
| Container | Model baked into non-root hardened image; historical clean-build and authenticated container acceptance recorded in [EVIDENCE.md](EVIDENCE.md) |
| Local Kubernetes | Deployment, Service, probes, resource limits, Metrics Server setup helper and CPU HPA from one to three Pods |
| Experiments | k6 load generator, recorder, runner, analyzer, three recorded local scale-out/scale-in runs, CSV and plots; [results](../results/ANALYSIS.md) |
| CI implementation | Python, frontend, manifest, container and Terraform jobs plus aggregate gate; [workflow](../.github/workflows/ci.yml) |
| AWS implementation | Terraform, OIDC, SSM deployment, ECR/S3, CloudWatch configuration, budgets, operator scripts and manual workflow |
| Cost estimator | Decimal-based offline estimate, usage validation and tests; [implementation](../scripts/cost_estimate.py), [tests](../tests/test_cost_estimate.py). It is implemented, not a missing feature. |

## Required local project completion

Priorities: P1 means complete before final handoff; P2 means improve evidence or maintainability after the P1 items. Each checkbox remains open until its stated completion evidence exists.

### P1 Reconcile the specification with the detection implementation

- [x] Formally supersede the original [build specification](../AI_Model_Serving_Platform_LLM_Build_Specification.docx) for implementation scope. It still specifies text sentiment, `POST /api/inference`, and positive/negative-text demo examples. Current code intentionally serves images through `POST /api/detect`.
- [x] Map the original functional requirements to the current image API, including validation, timing, metadata, health, Docker, HPA, experiments and CI. Identify the changed input/output contract explicitly in [SCOPE_RECONCILIATION.md](SCOPE_RECONCILIATION.md).
- [x] Record optional deferred extensions. Do not restore sentiment inference or add a second model solely to satisfy an obsolete example.

**Done when:** the specification, [API contract](API_CONTRACT.md), README and final demonstration describe the same detection application and accepted scope.

### P1 Make fresh machine local setup complete

The Deployment requires a local cluster, image, and `serving-secrets` Secret.
The [runbook](RUNBOOK.md) now walks through the existing helper's cluster
creation, image loading, Secret setup, Metrics Server, rollout, and smoke test.

Fresh deployment acceptance was not rerun on 2026-10-25: Docker Desktop's
Linux engine pipe was unavailable, and the `ai-serving-local` Minikube profile
does not exist in this environment. Do not count the documented procedure as a
fresh-machine deployment test.

- [x] Document prerequisites and exact Windows sequence: Python, Docker Desktop, Node for frontend tests, `uv` if using the setup helper, and Minikube/kubectl. Explain the project-local tools path when applicable.
- [x] Choose one consistent image tag across build, smoke, import and deployment examples.
- [x] Document cluster creation, image import, Metrics Server activation, nonempty local test key setup, Secret creation, rendering, rollout and authenticated Service smoke verification. Reuse the existing helper rather than duplicating its implementation unnecessarily.
- [x] State that the supported fresh-machine Kubernetes workflow is Windows/Minikube; macOS/Linux Kubernetes deployment is not currently maintained.
- [x] Document service access for the Studio, common rollout/HPA/model failures, and local cleanup using the existing guarded scripts.
- [ ] Run the documented sequence from a fresh checkout/environment without depending on `.validation`, cached model files or an existing Secret. Record versions and results.

**Done when:** a new developer can reach a ready deployment, perform real authenticated detection, inspect HPA metrics, run a bounded experiment, and clean up using only the instructions.

### P1 Correct stale references and contradictory status claims

- [x] Replace `docs/PHASE_1.md` in `scripts/setup-phase1.ps1` and `docs/PHASE_2.md` in `scripts/deploy-local.ps1`; those files do not exist.
- [x] Replace the deleted `docs/KUBERNETES.md` reference in `k8s/deployment.yaml` and current source documentation.
- [x] Update [FINAL_REPORT.md](FINAL_REPORT.md) to reflect locally successful Terraform 1.9.8 checks and the unverified CI-configured 1.15.8 version.
- [x] Mark the early “container, scaling ... pending” statement in [EVIDENCE.md](EVIDENCE.md) as historical.
- [x] Correct the roster statement: names were supplied in the original specification; actual individual contributions remain unconfirmed and are not inferred.
- [x] Clarify [loadtest/README.md](../loadtest/README.md): raw numbers are generated from measured runs, not manually transcribed or edited.
- [x] Check remaining repository relative Markdown links after these changes; all resolved. Command execution remains covered only where tools were available.

**Done when:** current status has one consistent interpretation and all referenced local guides exist.

### P1 Obtain hosted CI acceptance for the delivery commit

The workflow is implemented. [CI_CD.md](CI_CD.md) and the evidence ledger do not establish a successful hosted run.
The follow-up changes are not committed or pushed, so there is no delivery SHA
for an exact-commit hosted run. The Docker-backed local path is also unavailable
in this environment; hosted CI must be run after the implementation is committed
and pushed.

- [ ] Run or locate GitHub Actions for the exact delivery commit, using an existing configured trigger or manual dispatch.
- [ ] Confirm all five jobs and `ci-success` pass on a clean hosted checkout; resolve any actual failures.
- [ ] Verify the Terraform version configured in CI (`1.15.8`) as part of that run. Historical local validation used `1.9.8`, so it does not validate the CI toolchain itself.
- [ ] Preserve run URL, commit SHA, date and relevant outputs in the evidence ledger/final report.

**Done when:** a traceable hosted run passes the complete gate, including clean Docker build and real model preparation. A local pytest result alone does not close this item.

### P1 Complete the final academic report and demo

Sections 16, 18 and 21 of the original specification call for a reproducible
demo, architecture diagram, screenshots and contributions. The report, diagram,
screenshots, and timed outline are now present; rehearsal and confirmed
contributions remain team-dependent.

- [x] Add a diagram showing client, Service, serving Pods, HPA and Metrics Server, with optional AWS clearly separate. Do not depict unimplemented Floci or queue components as operational.
- [x] Preserve sanitized Studio screenshots durably in the evidence bundle. Historical ready-Pod/HPA screenshots and hosted CI artifacts are still not available.
- [x] Include measured mean and p95 latency, successful throughput, unsuccessful-attempt rate, separate 429 counts, replica timing and resource coverage, using recorded values only.
- [ ] Add confirmed individual member contributions. The report includes the
  roster, reproduction commands, documented environment facts, and limitations;
  the contributions need team confirmation before submission.
- [x] Write a 5–8 minute detection demo outline. The measured return-to-one interval alone is roughly seven minutes, so it directs presenters to recorded scaling evidence.
- [ ] Rehearse the 5–8 minute demo with the team.
- [x] Prepare the requested eight-slide presentation deck, `CCD_Detection_Serving_Project.pptx`.

**Done when:** another presenter can follow the demo, show traceable results and explain Pod scaling versus node capacity without relying on private local files.

## Measurement and tooling improvements

### P2 Complete resource measurements

The original specification asks for both node and Pod CPU/memory. The updated
recorder stores node and Pod metrics; the three earlier runs contain only Pod
metrics, with sustained complete-Pod coverage of 14/36, 16/36 and 16/36.

- [x] Add timestamped node CPU/memory sampling to the recorder and carry it through analysis and reporting; include tests for unavailable and partial metrics.
- [x] Record missing Pod/node metric reason codes for future measurements. Historical runs did not preserve enough diagnostics to identify why their missing samples occurred; gaps remain explicitly unknown, never zero.
- [x] Capture host CPU/RAM, Kubernetes/kubectl/Python/Docker/Minikube/Terraform and k6 image versions alongside cluster limits for future runs.
- [ ] Repeat the three scenarios if stronger resource/capacity conclusions are needed, preserving old runs under their original identifiers.
- [ ] If comparing throughput benefits, add controlled fixed-replica versus HPA workloads and an agreed sustainable-load target. Existing saturation runs prove replica changes, not proportional capacity growth or a service-level latency guarantee.

**Done when:** resource plots and summaries distinguish node totals from serving-Pod totals, show coverage, and support only the conclusions the samples justify. Existing limitations may remain explicitly documented for the minimal lab submission.

### P2 Make committed experiment evidence independently replayable

The analyzer originally expected a raw experiment directory containing
`environment.json` and per-run subdirectories. The tracked evidence uses
flattened filenames and `derived.json`; the compact replay mode now consumes
that bundle directly, while raw results remain ignored by Git.

- [x] Add compact-evidence replay mode and preserve a sanitized analysis-ready bundle with environment metadata and per-run observations.
- [x] Preserve the analyzer SHA-256 and workload/recorder source hashes.
- [x] Verify replay from the committed evidence directory without rerunning load.

**Done when:** an evaluator can independently reproduce the analysis with one documented command.

### P2 Automate browser regression acceptance

[test_studio_browser.py](../scripts/test_studio_browser.py) exists and the ledger records a successful browser run. Hosted CI currently runs frontend contracts, not this real browser acceptance.

- [x] Establish a documented repeatable manual browser release gate with explicit browser/dependency setup, server lifecycle and durable evidence output.
- [x] The historical browser acceptance exercised file upload, sample inference, overlay alignment, downloads, mobile layout and session-only key/history behavior.

**Done when:** either browser acceptance is an explicit repeatable manual release gate or the browser job passes in CI. This is additional regression protection, not a missing Studio implementation.

## Optional AWS deployment completion

These tasks are required only if cloud deployment is included in the final delivery. [AWS_SETUP.md](AWS_SETUP.md) and [AWS_RUNBOOK.md](AWS_RUNBOOK.md) already provide the operator workflow. No AWS changes were performed for this review.

| Order | Remaining action | Completion evidence |
| --- | --- | --- |
| 1 | Verify account eligibility, region, current rates, planned retention and approved spend; configure budget notifications | Dated owner cost decision and configuration, without secrets |
| 2 | Configure Terraform variables, review the plan and apply authorized infrastructure | Recorded outputs and successful bootstrap of EC2/k3s/SSM |
| 3 | Create the SSM SecureString API key and protect/configure the GitHub `aws-demo` environment and OIDC role | Environment and secret wiring verified without exposing values |
| 4 | Run successful exact-commit CI, manually deploy, push ECR image and S3 bundle, execute SSM deployment | Hosted run URL, image digest/tag, bundle checksum and SSM success |
| 5 | Verify readiness, real authenticated detection and rejection paths through the encrypted SSM tunnel | Sanitized cloud smoke results |
| 6 | Run bounded in-cluster baseline, sustained and recovery tests, ideally three runs | Cloud-specific summaries, resource/replica samples, CSV and plots |
| 7 | Verify actual CloudWatch log delivery and budget notification configuration | Sanitized delivered events and notification setup evidence |
| 8 | Exercise documented restart/redeployment recovery and capture any required fixes | Ready service and successful smoke after the lifecycle operation |
| 9 | Stop or destroy resources as agreed and inspect remaining billable resources | Teardown results and residual-resource/cost check |

Cloud experiment tooling needs attention: `scripts/run_experiments.py` deliberately restricts its context to `ai-serving-local`, and the deployment bundle does not include that runner or the analyzer. Preserve this local guard. For cloud runs, document a separate capture/import procedure using the deployed load Job and recorder, or implement an explicitly scoped cloud runner with matching provenance and analysis inputs.

The offline cost calculator is already tested. Its rates are dated 2026-10-13 in the source and its monitoring allowance is a reserve, not metered CloudWatch pricing. Revalidate account-specific costs before deployment; calculator tests prove arithmetic, not current prices or billing accuracy.

## Optional features not currently implemented

These are extensions from the original specification, not missing MVP requirements. The evidence ledger records intentional removal of earlier optional adapters during detection migration.

| Extension | Work needed if selected | Definition of done |
| --- | --- | --- |
| Second model | Add pinned assets, model-specific metadata/adapter, selection/routing and tests. Registry exists, but `/api/detect` currently selects `yolov8n` explicitly. | Two real models can be selected and tested within declared CPU/RAM limits |
| Async queue and job API | Implement `POST /api/jobs`, status/result endpoint, queue, worker and durable state; define idempotency, retries, visibility timeout and dead-letter handling | Real job survives duplicate delivery and worker failure; results persist before acknowledgement |
| Floci integrations | Add disabled-by-default endpoint configuration and selected S3/SQS/ECR operations after checking supported operations | Repeatable emulator integration tests; emulator behavior clearly distinguished from AWS |
| Prometheus and Grafana | Add scraping/deployment configuration, authenticated metrics access, dashboards and any selected alert rules | Live API/resource graphs verified; `/metrics` alone is already implemented |

Production multi-node HA, EKS, GPU serving, node autoscaling, databases, API Gateway and multi-region deployment are not required by the original scope. Model training and a formal detection-accuracy benchmark are also not established lab requirements; sample images are functional checks only.

## Verification performed during this review

PowerShell from the repository root:

```powershell
.\.venv-review\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider --tb=short --basetemp=.validation/pytest-project-audit
node --test tests/frontend/studio-core.test.mjs
```

- Python: **276 passed**, 8.76 seconds, including the cost-estimator tests.
- Frontend contracts: **60 passed**, zero failed/skipped.
- Follow-up implementation validation used Python 3.12.14 and Node.js 24.14.0:
  **277 passed** across the full Python
  suite; **60 passed** in the frontend suite; guarded cleanup test passed;
  compact-evidence replay regenerated all nine phase rows without load.
- The requested PowerPoint deck validated with eight slides and two embedded
  screenshots; all slide shapes were within the canvas bounds.
- Docker-backed fresh deployment was not run: the Docker Desktop Linux engine
  was unavailable and the local Minikube profile was absent.
- Pylance Python syntax checks found no syntax errors in the three modified
  experiment scripts. Pylance reported `reportMissingModuleSource` for the
  lazy Matplotlib imports; Matplotlib is installed in `.venv-review`, and
  analysis/replay executed and generated all nine plots in that environment.
- Reviewed original specification, application and test structure, CI/deployment workflows, infrastructure configuration, operator guides and committed experiment evidence.
- The original audit was read-only; this dated follow-up implements the
  repository changes listed above. Neither audit nor implementation certifies
  that no undiscovered defects remain.

## Recommended completion order

1. Reconcile scope and repair documentation/status references.
2. Complete and execute fresh-machine local instructions.
3. Obtain hosted CI evidence for the exact delivery commit.
4. Close measurement/replay gaps needed for the report's claims.
5. Finish the report, screenshots, contributions and timed demo.
6. Execute AWS acceptance only if cloud delivery is selected and authorized.
7. Add optional features only when they serve an agreed project requirement.
