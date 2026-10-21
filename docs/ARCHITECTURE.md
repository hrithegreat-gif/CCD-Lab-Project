# Budget-first cloud-native architecture

```text
Local client / SSM-encrypted tunnel
                  |
         k3s NodePort Service :30080
                  |
    ready FastAPI sentiment replicas
      | health | auth | metrics |
                  |
        pinned CPU ONNX sentiment

Optional, off by default:
  /api/generate -> local Qwen or fixed Groq text model
  /api/speech -> local Kitten Mini CPU speech
  /api/transcriptions -> fixed Groq Whisper model

GitHub approved environment -> OIDC -> ECR + S3 bundle
                                      |
                           scoped SSM Run Command
                                      |
                      one Mumbai EC2/k3s host
```

## Boundaries

- The baseline is one real CPU sentiment model per API process, not a fake AI endpoint. The allowlisted revision and warm-up determine readiness.
- Different generation/audio contracts are separate routes. They do not change sentiment into a catch-all schema. The capability endpoint reports optional adapters explicitly; a disabled/unavailable adapter is not a ready model.
- Local optional models consume CPU/RAM in every enabled replica. Do not turn them on for the sentiment HPA experiment or assume all replicas/models fit on an 8 GiB machine.
- HPA increases pod replicas using Metrics Server CPU readings. It does not add EC2 instances, create high availability or increase the machine's total compute. Queueing and durable job state are not implemented prerequisites.
- Prometheus-format application metrics and structured input-free logs support measurement. Exporting metrics does not imply an installed Prometheus/Grafana stack.

## AWS design

One public subnet uses an Internet Gateway for outbound package/model/container downloads. An ephemeral public IPv4 is allocated while the machine runs. There is **no inbound security-group rule** by default; no public Kubernetes API, SSH, HTTP or HTTPS endpoint. SSM port forwarding protects traffic to the NodePort; no ALB, NAT gateway or EKS control plane is required.

The EC2 host reads deployment bundles and SSM secrets with its scoped instance role. Pods are not intended to inherit that role: IMDSv2 is required with hop limit 1; host-side commands obtain ECR credentials and populate Kubernetes image-pull/serving secrets. Revisit pod-specific identity explicitly if future application code needs AWS SDK access.

CI obtains temporary AWS credentials through an environment-scoped GitHub OIDC role. It publishes an immutable full-commit-tagged image, uploads a commit-keyed bundle, and invokes a scoped SSM deployment document. The instance verifies the bundle checksum before extraction/execution. CI does not provision infrastructure or auto-start stopped instances.

Private S3 stores deployment/experiment artifacts, not a pretend database; private ECR stores images. CloudWatch log retention and artifact/image lifecycles bound retained storage. API/provider secrets are supplied outside Terraform, so sensitive Terraform values/state are not used as a secret distribution mechanism.

An on-boot auto-stop timer reduces forgotten compute usage but is not a spending cap. Stopping EC2 leaves disk and retained registry/object/log data billable. See [budget assumptions](BUDGET.md), [AWS deployment runbook](AWS_RUNBOOK.md) and [implementation gates](PHASES.md).

## Out of scope for the initial deployment

EKS, GPU inference, additional always-on workers, a managed load balancer, NAT gateway, DynamoDB, API Gateway, SQS/job APIs, Floci emulation, public-domain TLS and distributed availability. Add them only for a measured need, with separate budget and authorization.

[The verification ledger](VERIFICATION.md) distinguishes source-level/local test results from real Docker, cluster, provider and AWS execution.
