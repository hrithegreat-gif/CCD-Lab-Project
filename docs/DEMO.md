# Demonstration checklist

Target: 5–8 minutes **after the relevant execution gates pass**. Prepared code and mock/static checks are not deployment evidence.

1. Explain the request → FastAPI → real CPU ONNX model flow and fluctuating traffic.
2. Show real positive/negative sentiment responses, readiness/liveness and bounded metadata. With authentication enabled, show unauthenticated 401 without displaying the key.
3. Show a running non-root, read-only-rootfs container with offline cached weights.
4. Show the actual Deployment, Service, ready Pods, HPA and working Metrics Server CPU readings.
5. Run the bounded baseline/sustained k6 phases; capture ready-replica and CPU time series. Single-node replicas share the same 2 vCPU and do not add hardware or high availability.
6. Reduce load and wait for the configured 300-second scale-down stabilization. Present measured throughput, p95, 429s, server errors and hardware limitations, not just replica counts.
7. Show a real successful GitHub CI run. For the cloud demo, show protected-environment OIDC deployment, private ECR/S3, SSM-only access and CloudWatch logs.
8. Explain Mumbai cost estimates, the four-hour guest auto-stop, retained-storage billing, delayed budget alerts and the $100 credit limit. Stop is not full teardown.
9. Demonstrate optional generation/TTS/transcription only after the adapter's real enablement checklist passes; otherwise show disabled capabilities and explain why.
10. Preview namespace cleanup using an explicit context; perform cloud teardown only under the separately approved runbook. Record each member's contribution.

Record commit SHA, image digest, hardware, timestamps and raw artifacts alongside the [results](../results/README.md). Do not claim cloud deployment from local tests, Terraform mocks, emulator output or a written workflow.
