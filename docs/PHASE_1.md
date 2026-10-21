# Phase 1 setup and acceptance guide

Phase 1 implements the REST API using FastAPI, one real pretrained CPU sentiment
model, an allowlisted registry, validation, lifecycle health checks and tests.
It needs Python 3.12 and internet access for package installation and the first
model download. Docker, Kubernetes and an AWS account are not needed for this
phase. EKS is excluded from the project.

Python 3.12 replaces the scaffold's tentative 3.11 selection. The baseline was
also audited using uv-managed Python 3.12.14 on Windows. Current executed checks,
including the expanded suite, are recorded in [VERIFICATION.md](VERIFICATION.md).
For a uv-based setup without a system Python registration, use the [README](../README.md).

## Windows PowerShell

Extract the archive to a new location and open a terminal in its
ai-model-serving-platform folder. For an existing scaffold, merge the extracted
files into it; preserve your own .env and local notes. Avoid nesting project folders.

    py -3.12 --version
    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    Copy-Item .env.example .env
    .\.venv\Scripts\python.exe scripts/prepare_model.py --verify
    .\.venv\Scripts\python.exe -m pytest tests/ -v
    .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

Only copy .env.example if you do not already have a configured .env. Using the
venv interpreter directly avoids PowerShell activation and execution policy changes.
Leave the server running; open another PowerShell terminal in the project folder:

    .\.venv\Scripts\python.exe scripts/smoke_test.py
    Invoke-RestMethod http://127.0.0.1:8000/health/live
    Invoke-RestMethod http://127.0.0.1:8000/health/ready
    Invoke-RestMethod http://127.0.0.1:8000/api/models

    $requestBody = @{
        model = "sentiment"
        input = "I love this product. It is absolutely wonderful!"
    } | ConvertTo-Json

    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/inference -ContentType "application/json" -Body $requestBody

## Linux or macOS Bash

From the extracted project root:

    python3.12 --version
    python3.12 -m venv .venv
    .venv/bin/python -m pip install -r requirements-dev.txt
    cp .env.example .env
    .venv/bin/python scripts/prepare_model.py --verify
    .venv/bin/python -m pytest tests/ -v
    .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

Do not overwrite an existing configured .env. In a second Bash terminal:

    .venv/bin/python scripts/smoke_test.py
    curl -i http://127.0.0.1:8000/health/ready
    curl -H 'Content-Type: application/json' \
      -d '{"model":"sentiment","input":"I love this product. It is absolutely wonderful!"}' \
      http://127.0.0.1:8000/api/inference

## Expected results

Preparation prints the pinned source, revision, asset byte count, real positive
and negative predictions, and:

    PASS: pinned model assets are available.

The full test suite must pass without skips; use [the verification ledger](VERIFICATION.md)
for the current count rather than the historical 32-test baseline. Integration
tests run the actual ONNX model. Fault-injection tests use a test-only double for
deterministic lifecycle/error checks; production uses the real model.

After startup, open http://127.0.0.1:8000/docs. Expand POST /api/inference, select
Try it out, and submit:

    {"model":"sentiment","input":"I hate this product. It is terrible and disappointing."}

Expect HTTP 200, status success and prediction negative, with actual confidence,
processing_time_ms, tokens_processed and truncated values. Timing and scores are
not fixed. Confidence is a softmax class score, not a calibrated guarantee of correctness.

The live HTTP smoke checker ends with:

    PASS: Phase 1 live HTTP acceptance checks.

It verifies health, metadata, positive/negative predictions and invalid requests,
discovering the configured character limit from metadata. For a protected server,
supply its API key through the `API_KEY` environment variable; never paste keys
into shared logs or screenshots.

## Validation and failure checks

| Check | Expected result | Verification |
| --- | --- | --- |
| Process alive | 200 /health/live | Smoke test |
| Model usable after warm-up | 200 /health/ready | Smoke test |
| Unknown model | 404 | Smoke and unit tests |
| Missing fields or malformed JSON | 422 | Tests |
| Empty, blank or over 2000 characters | 422 | Smoke and tests |
| Non-string fields or unexpected keys | 422 | Tests |
| Failed initialization | Live 200, ready/inference 503 | Fault-injection test |
| Inference slots occupied | 429 with Retry-After: 1 | Unit test |
| Native execution deadline exceeded | 504 | Real-model test |
| Unexpected backend failure | Safe 500 without private details | Fault-injection test |
| Over 256 tokens within character limit | 200 and truncated true | Real-model test |
| Repeated requests | One model load per process | Lifecycle test |

By default, local startup completes the load attempt before accepting HTTP.
Set `BACKGROUND_MODEL_LOAD=true` to serve liveness while loading; readiness stays
503 until warm-up succeeds. Kubernetes enables background loading. If loading
fails, liveness remains available while readiness/inference stay 503; fix the
cause and restart. Automatic startup retries are not implemented.

A nonempty `API_KEY` requires Bearer auth on `/api/*` and `/metrics`; health is
public. With auth enabled, add the header to the manual requests above or use
Swagger's Authorize button. An empty key is for local loopback use only. The
byte-level request limit and optional adapters are described in [the API contract](API_CONTRACT.md).

For a manual failed-startup demonstration, use an actually empty cache:

PowerShell, in a separate terminal:

    $env:MODEL_CACHE_DIR = "models/empty-demo-cache"
    $env:MODEL_LOCAL_FILES_ONLY = "true"
    .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1

Bash:

    MODEL_CACHE_DIR=models/empty-demo-cache MODEL_LOCAL_FILES_ONLY=true \
      .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1

Check port 8001's /health/live and /health/ready from another terminal: expect 200
and 503. Stop with Ctrl+C. In PowerShell remove overrides before normal startup:

    Remove-Item Env:MODEL_CACHE_DIR
    Remove-Item Env:MODEL_LOCAL_FILES_ONLY

## Settings and offline operation

.env values are overridden by actual environment variables. Relative cache paths
resolve against the project root. Model assets are excluded from Git and the archive.

| Setting | Default | Meaning |
| --- | --- | --- |
| MODEL_CACHE_DIR | models/cache | Asset cache, not client-selectable |
| MODEL_LOCAL_FILES_ONLY | false | Use cache exclusively when true |
| MAX_INPUT_CHARS | 2000 | Reject longer text, including whitespace |
| MAX_INPUT_TOKENS | 256 | Truncate sequences including special tokens |
| INFERENCE_THREADS | 1 | Native CPU threads per ONNX session |
| MAX_CONCURRENT_INFERENCES | 2 | Reject excess active inference with 429 |
| INFERENCE_TIMEOUT_SECONDS | 15 | Native ONNX execution deadline |
| LOG_LEVEL | INFO | Structured application event level |

After preparation, set MODEL_LOCAL_FILES_ONLY=true to use cached weights without
downloads. Packages must already be installed. The inference timeout covers native
ONNX execution, not downloads, request parsing, tokenization or thread-pool waiting;
it is not a full HTTP timeout. Use one worker: each worker loads a separate model.

## Troubleshooting

| Symptom | Recovery |
| --- | --- |
| Python 3.12 missing | Install Python 3.12, then create a fresh venv |
| Import failure | Use the .venv interpreter; install requirements-dev.txt |
| Download fails or readiness stays 503 | Run prepare_model.py --verify; check network/proxy/certificates, writable cache and disk, then restart |
| Offline cache missing | Set MODEL_LOCAL_FILES_ONLY=false and prepare assets |
| Windows symlink warning | Usually a disk-use warning; cache may copy files. Keep the path short |
| Port busy | Use --port 8001 and smoke_test.py --base-url http://127.0.0.1:8001 |
| Swagger UI assets fail | Check internet access to its CDN; /openapi.json and smoke tests remain usable without that CDN |
| Memory pressure, high CPU or 429 | Keep one worker; reduce concurrent requests and input length; record hardware |
| Unexpected sentiment | This English binary classifier has no neutral class |
| ONNX DLL error on Windows | Use 64-bit Python; install Microsoft Visual C++ Redistributable if the runtime reports it missing |

## Exit checklist

- Model preparation verifies actual pinned weights.
- The current full test suite passes without skips.
- Live HTTP smoke checks pass.
- /docs lets you submit positive and negative text.
- Invalid requests return documented errors.
- Record your own OS, CPU/RAM and Python in ENVIRONMENT.md.

Stop the server with Ctrl+C. Confirm results before container/cluster validation.
Use [the phased gates](PHASES.md) for the budget-first progression. Queues and a
metadata database are not required prerequisites; Mumbai deployment is separately
authorized and EKS is excluded.
