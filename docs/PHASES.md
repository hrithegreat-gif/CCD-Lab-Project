# Staged acceptance gates

The authoritative build specification prioritizes a real local model, containerization, Kubernetes/HPA and measurements before optional queues/cloud extensions. The Mumbai cloud target is self-managed k3s on EC2; EKS is not required. Preparing artifacts is not evidence of a live deployment.

## 1. Runtime and one real model

Use [the Phase 1 setup guide](PHASE_1.md) and [the API contract](API_CONTRACT.md). Run the full test suite including real sentiment integration tests; start the API and run the authenticated/settings-aware HTTP smoke test. Confirm health/readiness, positive/negative inference, invalid inputs, capacity handling, secret-safe errors and metrics.

Gate: actual ONNX inference succeeds; all tests pass; inputs/secrets are absent from logs. A failed model load is not a successful model demonstration.

## 2. Container image

Use [the Kubernetes/container guide](KUBERNETES.md). Build from a clean checkout without relying on a host model cache. Run the image non-root with one worker; check real inference, model assets, writable directories and probes. The default image is sentiment-only; enabling local model extensions requires their optional dependencies and measured memory capacity.

Gate: real Docker build and container HTTP smoke pass. Source validation alone does not satisfy this gate.

## 3. Kubernetes, HPA and measured evidence

Apply Namespace, Deployment, NodePort Service and HPA to a local cluster first. Confirm working Metrics Server and numeric pod CPU metrics. Run the [controlled load test](../loadtest/README.md), capture replica/CPU/memory observations, then wait for scale-in. Record hardware, limits, model revision, image digest and test parameters.

Gate: stable service routing, ready replicas, measured scale-out/scale-in and retained result artifacts. More pods on one machine do not provide more CPU, node scaling or high availability. Do not describe a smoke-test latency as a benchmark.

## 4. Optional model adapters

Use [the extension guide](MODEL_EXTENSIONS.md). Qwen and Kitten are opt-in CPU paths; Groq text/transcription require an explicit key and separately billed provider access. Capability status must distinguish disabled/loading/unavailable/ready. Test real adapters separately from mocked failure-path tests.

Gate: each enabled adapter produces genuine output on the intended host within measured resource limits. An import, unit mock or advertised CPU support does not establish practical throughput. Do not enable paid provider routes in the HPA load test.

## 5. CI and authorized Mumbai deployment

Use [CI/CD](CI_CD.md), [the AWS runbook](AWS_RUNBOOK.md) and [the budget](BUDGET.md). First run offline Terraform validation and review account eligibility/costs. Apply only after explicit authorization. Provide API keys separately through SSM Standard SecureString; protect the GitHub `aws-demo` environment and OIDC role. The deploy workflow updates existing infrastructure; it does not apply Terraform or start an instance automatically.

Gate: a real GitHub Actions run, verified immutable ECR image, SSM-driven rollout, actual authenticated cloud prediction and working cloud logs. Default access is an encrypted SSM tunnel; there are no public inbound ports. Validate stop/start recovery and teardown costs.

## 6. Optional future work

A second short-lived worker, public HTTPS/domain, async queue, durable job state, Floci integration, and richer dashboards are separate scoped extensions after the preceding gates pass. They are not current prerequisites and should not be represented as implemented. Do not add DynamoDB/API Gateway/SQS merely to inflate the architecture.

## Verification ledger

[VERIFICATION.md](VERIFICATION.md) records what was actually run and what remains unexecuted. Keep this ledger honest rather than marking Docker, Kubernetes, cloud deployment or performance complete from source files alone.
