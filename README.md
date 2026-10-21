# CCD Lab: cloud-native AI serving on a $100 AWS budget

A real CPU sentiment API with opt-in text/speech adapters and a reproducible Mumbai (`ap-south-1`) deployment package. The baseline is **one EC2 `m7i-flex.large` running k3s**, not EKS, a GPU, a managed load balancer or a collection of unnecessary paid services.

[Implementation plan](docs/IMPLEMENTATION_PLAN.md) · [Verification ledger](docs/VERIFICATION.md) · [Budget](docs/BUDGET.md)

## What is included

- FastAPI + pinned, quantized DistilBERT ONNX inference; strict validation, bounded concurrency/native timeout, Bearer-key authentication and streamed request-byte limits.
- **Inference Studio**: polished, responsive browser playground with live model availability, real inference results, downloadable JSON/WAV and tab-only run history. Served by FastAPI, with no frontend build step or CDN dependencies.
- Health/readiness probes, sanitized structured logs and real Prometheus-format HTTP metrics.
- Non-root sentiment container with baked pinned model assets; Kubernetes Deployment/NodePort/HPA and a bounded k6 load test.
- Terraform for Mumbai EC2/k3s, private ECR/S3, SSM deployment/access, scoped IAM, short CloudWatch log retention and an on-boot auto-stop timer.
- GitHub Actions tests/image checks and an explicitly dispatched, environment-protected OIDC deploy workflow.
- Optional Qwen3.5-0.8B generation, Kitten Mini speech, Groq text and Groq Whisper transcription routes. **Disabled by default**; real adapter execution is a separate acceptance gate.

Source artifacts and mocked failure-path tests do not prove a Docker build, HPA event, GitHub run or cloud deployment. See the ledger for the actual executed checks. Benchmark results are deliberately not invented.

## Start locally (Python 3.12)

From the repository root, with [uv](https://docs.astral.sh/uv/) installed:

```powershell
# Do not overwrite an existing configured .env.
Copy-Item .env.example .env
uv run --python 3.12 --no-project --with-requirements requirements-dev.txt python scripts/prepare_model.py --verify
uv run --python 3.12 --no-project --with-requirements requirements-dev.txt python -m pytest tests/ -v
uv run --python 3.12 --no-project --with-requirements requirements.txt python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

In a second terminal:

```powershell
uv run --python 3.12 --no-project --with-requirements requirements-dev.txt python scripts/smoke_test.py
```

The first package/model preparation needs Internet access; the baseline model assets total approximately 68 MB. The example leaves `API_KEY` empty for **loopback-only local development**. For auth-enabled local use, configure a nonempty key and supply the same `API_KEY` environment variable to the smoke checker. Cloud deployment refuses an empty key. Keep one worker; scale with Kubernetes replicas rather than duplicating every model in multiple API workers.

Open **[Inference Studio](http://127.0.0.1:8000/)** for the demo, or [Swagger](http://127.0.0.1:8000/docs) for the API. Choose an example and click **Analyze sentiment** (or Ctrl/Cmd + Enter). With authentication enabled, use **Session access** to enter the server key; it is held only in this tab's memory. Optional adapters remain visibly disabled unless enabled and ready. See the [frontend demo guide](docs/FRONTEND.md) for capabilities, safety and browser checks.

POST `/api/inference` accepts:

```json
{"model":"sentiment","input":"I love this product. It is absolutely wonderful!"}
```

[Detailed setup](docs/PHASE_1.md) · [API contract](docs/API_CONTRACT.md) · [Model provenance](models/README.md)

## Container, Kubernetes and measurements

Follow [the Kubernetes guide](docs/KUBERNETES.md), then [the load-test instructions](loadtest/README.md). Render the image and optional pull-secret placeholders rather than applying an unresolved Deployment. Provide the `serving-secrets` Secret even for local mode. Metrics Server must report actual pod CPU for HPA to act.

Default HPA: 1–3 sentiment pods, conservative scale-up/down behavior. Three pods on one 2-vCPU node still share those two CPUs and are **not high availability**. Single-replica updates can briefly interrupt service. Record replica/CPU/memory observations and latency/throughput together; a source manifest is not scaling evidence.

## Optional models

[Model extension guide](docs/MODEL_EXTENSIONS.md) documents flags, dependencies, pinned sources and separate request contracts:

| Route | Purpose |
| --- | --- |
| `/api/capabilities` | Disabled/loading/unavailable/ready extension status |
| `/api/generate` | Local Qwen or Groq text generation |
| `/api/speech` | Local Kitten Mini WAV output |
| `/api/transcriptions` | Bounded raw audio upload to Groq Whisper |

Do not enable all local models in every 1-GiB sentiment pod. Their dependencies, images, memory limits and real CPU latency must be checked independently. Groq charges/quotas are outside AWS credits; the sentiment load test must not invoke it.

## Authorized AWS deployment

1. Review [budget assumptions](docs/BUDGET.md), account-plan eligibility and the remaining credits.
2. Follow [the AWS runbook](docs/AWS_RUNBOOK.md): review Terraform before apply, create SSM SecureStrings outside state, and use encrypted SSM port forwarding for access.
3. Configure/protect GitHub's `aws-demo` environment and the variables described in [CI/CD](docs/CI_CD.md).
4. Dispatch the deploy workflow only after local gates pass and billable resources have been explicitly authorized.
5. Verify a real authenticated prediction, deployment/log artifacts and actual HPA behavior; then stop/tear down promptly.

The default security group has **no inbound ports**. There is no public plaintext API. CI uses OIDC temporary credentials, immutable commit image tags and a checksum-verified S3 deployment bundle; it does not apply Terraform or start stopped instances automatically.

Illustrative estimate, not a cap:

```powershell
python scripts/cost_estimate.py --hours 200 --months 2
```

This estimates **$35.71 AWS** under the checked assumptions, including retained storage and a reserve. Stopping EC2 does not delete disks/images/objects/logs or stop their storage charges. Budget alerts are delayed notifications, not enforcement.

## Safety and acceptance

- No billable resources or paid inference calls are required for local validation.
- Never commit `.env`, keys, Terraform state/variable files or kubeconfig.
- Cleanup helpers require an explicit Kubernetes context and confirmation; they only delete the fixed namespace, not AWS resources.
- Keep [the phased gates](docs/PHASES.md) and [verification ledger](docs/VERIFICATION.md) honest; queues, durable jobs, DynamoDB, Floci, GPU/EKS and public HTTPS are not implemented prerequisites.
