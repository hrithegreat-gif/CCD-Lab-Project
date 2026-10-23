# Detection migration evidence

2026-10-23: pre-migration cached real sentiment preparation passed; full baseline
`python -m pytest tests/ -q -p no:cacheprovider`: 296 passed in 8.30 seconds.
These are historical baseline results, not detection acceptance.

The owner intentionally removed the previous docs and deferred the final report.
Detection export, inference, container, scaling and AWS acceptance are pending.

Migration phase 1: optional adapters and their tests removed; remaining full suite: 232 passed in 3.53 seconds. The reduction is the explicitly removed adapter coverage; core safety/lifecycle coverage remains.

Migration phase 2: real export with ultralytics 8.3.221 / torch 2.7.1 CPU / ONNX opset 17 passed; input images [1,3,640,640], output output0 [1,84,8400], 80 labels, finite class scores in [0,1]. File 12851044 bytes, SHA256 946ca666d84aaf682e348c80996b94be55a36408bfa8a718aaf3e3c758e35b01. Export environment: uv pip check passed, 45 distributions. Full application suite: 237 passed in 3.80 seconds. Registry activation is grouped with backend replacement in phase 3 to keep the intermediate application runnable.

Migration phase 3: public release downloaded anonymously and hash-verified; real CPU detections observed: cat 0.889, cup 0.925, person 0.730. Box previews visually checked; astronaut sample has an additional false-positive person around the helmet. These are functional observations, not accuracy/latency benchmarks. Full suite: 237 passed, 3 Studio-config failures intentionally deferred to the immediately following UI phase. Core auth/body-limit/lifecycle/timeout tests migrated and pass. Native cancellation holds capacity until the worker actually completes; global Pillow ceiling is 50 million pixels with per-instance limits checked before load. Magic mismatch is 415; matching-magic malformed image is 422 (resolves conflicting garbage-byte examples in spec).

Migration phase 4: detection Studio implemented; image upload/drop/samples, sliders, original-coordinate overlays, accessible table, JSON history and annotated PNG. Full Python suite 239 passed; Node Studio contract suite 60 passed, no skips. Browser acceptance pending phase 6. Keys remain tab-memory only; public requests strip Authorization in all header cases.
