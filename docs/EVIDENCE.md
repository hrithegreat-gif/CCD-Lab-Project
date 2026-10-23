# Detection migration evidence

2026-10-23: pre-migration cached real sentiment preparation passed; full baseline
`python -m pytest tests/ -q -p no:cacheprovider`: 296 passed in 8.30 seconds.
These are historical baseline results, not detection acceptance.

The owner intentionally removed the previous docs and deferred the final report.
Detection export and inference are verified below. Container, scaling and AWS acceptance remain pending.

Migration phase 1: optional adapters and their tests removed; remaining full suite: 232 passed in 3.53 seconds. The reduction is the explicitly removed adapter coverage; core safety/lifecycle coverage remains.

Migration phase 2: real export with ultralytics 8.3.221 / torch 2.7.1 CPU / ONNX opset 17 passed; input images [1,3,640,640], output output0 [1,84,8400], 80 labels, finite class scores in [0,1]. File 12851044 bytes, SHA256 946ca666d84aaf682e348c80996b94be55a36408bfa8a718aaf3e3c758e35b01. Export environment: uv pip check passed, 45 distributions. Full application suite: 237 passed in 3.80 seconds. Registry activation is grouped with backend replacement in phase 3 to keep the intermediate application runnable.

Migration phase 3: public release downloaded anonymously and hash-verified; real CPU detections observed: cat 0.889, cup 0.925, person 0.730. Box previews visually checked; astronaut sample has an additional false-positive person around the helmet. These are functional observations, not accuracy/latency benchmarks. Full suite: 237 passed, 3 Studio-config failures intentionally deferred to the immediately following UI phase. Core auth/body-limit/lifecycle/timeout tests migrated and pass. Native cancellation holds capacity until the worker actually completes; global Pillow ceiling is 50 million pixels with per-instance limits checked before load. Magic mismatch is 415; matching-magic malformed image is 422 (resolves conflicting garbage-byte examples in spec).

Migration phase 4: detection Studio implemented; image upload/drop/samples, sliders, original-coordinate overlays, accessible table, JSON history and annotated PNG. Full Python suite 239 passed; Node Studio contract suite 60 passed, no skips. Browser acceptance pending phase 6. Keys remain tab-memory only; public requests strip Authorization in all header cases.

Migration phase 5: expanded image decoding, synthetic postprocessing, ONNX contract,
API privacy and cancellation/capacity tests. Full command
`.venv-review/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider --tb=short`:
276 passed in 6.52 seconds. `node --test tests/frontend/studio-core.test.mjs`:
60 passed, no skips. Real CPU model tests are included.

Migration phase 6 (2026-10-23): runtime pins regenerated with the 2026-10-09
release cutoff; no PyTorch, Ultralytics, Hugging Face or tokenizers in runtime.
Dependency checker: 35 distributions / 72 active requirements passed.
Real preparation verifies cat, cup and person. Full Python suite: 276 passed
in 5.77 seconds. Edge browser acceptance passed: three real samples, uploaded
image, canvas boxes, accessible table/tabs, annotated PNG, mobile layout and
session-only key/history. Desktop/mobile screenshots visually reviewed under
`.validation/detection-browser/` (local artifacts).

Clean Docker build succeeded; first hardened startup exposed owner-only model
file permissions. The bake step now explicitly makes baked assets readable by
the non-root runtime user. Rebuilt hardened container acceptance passed: real cat,
401/413/415/422/404, metrics, UID 10001 and pip consistency, read-only root,
1 CPU/1 GiB with root-owned group-writable cache. Tested image ID:
`sha256:db42fa6870a3061e1adeb9da93863651b6066c4d4a073931c7ae2b6744832321`.
Observed 150.3 MiB idle snapshot is not peak-memory calibration. AWS has not
been deployed; no cloud acceptance is claimed.

Migration phase 7 (2026-10-23): three local detection experiment Jobs completed
on the `ai-serving-local` Minikube context. `scripts/analyze_experiments.py`
generated nine phase rows, figures, and compact evidence. Every run went from
one ready replica to three and returned to one; scale-out was 39.188, 84.251,
and 84.310 seconds, and return-to-one was 439.283, 424.242, and 424.310
seconds. Mean sustained successful goodput was 10.726 requests/second and mean
successful-response p95 was 483.0 ms. Sustained resource metric coverage was
incomplete; details are in `results/ANALYSIS.md`. Two-slot calibration measured
473.0 MiB process high-water RSS for the tested concurrent 12-MP uploads and
improved local small-image throughput; Kubernetes is configured for two slots.

Migration phase 8 repository checks (2026-10-23):
`.venv-review/Scripts/python.exe -m pytest tests/test_terraform_safety.py
tests/test_manifests.py -q -p no:cacheprovider --basetemp=.validation/pytest-infra`:
69 passed. `bash infra/terraform/tests/deploy_doc_safety_test.sh`: 17 passed.
Terraform 1.9.8 checks: `terraform fmt -check -recursive`, `terraform init
-backend=false -input=false`, `terraform validate`, and `terraform test` all
passed; all 8 mock-provider policy tests passed. Init installed the locked AWS
provider v5.100.0 without configuring a backend. AWS provisioning/deployment
and hosted CI acceptance are owner/environment-dependent and were not executed.

Migration phase 9 documentation (2026-10-23): the detection API, architecture,
runbook, AWS/CI/budget notes, and measured local results are documented. The
final report makes no AWS or hosted CI acceptance claim.

Current-session test commands: `.venv-review/Scripts/python.exe -m pytest
tests/ -q -p no:cacheprovider --tb=short --basetemp=.validation/pytest-phase7`
(276 passed); `node --test tests/frontend/studio-core.test.mjs` (60 passed);
and `scripts/analyze_experiments.py results/raw/detection-20261023` (nine rows).
The first pytest attempt used Windows' inaccessible default temp directory and
failed during fixture setup; the workspace-local `--basetemp` rerun passed.

Post-validation check (2026-10-23): `scripts/prepare_model.py --verify`
downloaded/verified the pinned 12,851,044-byte ONNX asset and detected the
expected cat, cup, and person samples. The loopback API health endpoints
returned 200, while unauthenticated model/detection routes returned 401; the
live smoke script therefore was not accepted without an authorized API key.
No key was read from local environment files. See `docs/AWS_SETUP.md` for the
owner-operated safe SSM key retrieval and cloud smoke procedure.

Hardened-container acceptance on the current worktree (2026-10-23):
`scripts/test-container.ps1 -Port 8001` passed. It built image
`sha256:ef15d3731b7850b449e41daadb5ed14e66f250282a1f84819083d6ed70c101b3`,
ran read-only/non-root with a temporary generated API key, passed real
detection, authorization, validation, and metrics checks, and passed
`pip check`. The temporary test container was removed. The observed
164.3-MiB container snapshot is not a peak-memory measurement.
