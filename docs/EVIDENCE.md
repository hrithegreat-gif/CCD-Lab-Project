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
