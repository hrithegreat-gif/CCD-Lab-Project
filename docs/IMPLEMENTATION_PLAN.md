# Mumbai credit-budget implementation plan

## Objective and boundaries

Deliver a reproducible cloud-native AI-serving lab for AWS Mumbai (`ap-south-1`) with a $100 total credit budget. Prepare and test locally first. Do not run `terraform apply`, launch/modify AWS resources, call paid model APIs, or publish images during implementation. Account eligibility and remaining credits must be checked by the operator before deployment.

## Deployment design

- One x86 Linux EC2 instance (`m7i-flex.large`, 2 vCPU / 8 GiB as the initial benchmark candidate), encrypted 40 GiB gp3 disk, ephemeral public IPv4 for outbound access.
- Self-managed k3s with Metrics Server. No EKS, NAT gateway, ALB, GPU, database, or node autoscaler.
- Default security group: no inbound access. Reach the Kubernetes NodePort through an encrypted AWS Systems Manager port-forwarding session; do not expose unencrypted authenticated traffic on the Internet.
- Private ECR repository for immutable commit-tagged images; private encrypted S3 bucket for deployment bundles and measured experiment artifacts; bounded CloudWatch log retention.
- EC2 instance role and GitHub Actions environment-scoped OIDC role; no permanent AWS keys. SSM Standard SecureString parameters are supplied separately by the operator, not stored as Terraform values.
- A configurable on-boot auto-stop timer limits forgotten compute sessions. AWS Budgets alerts are delayed notifications, not hard spending caps. EBS/S3/ECR/log storage survives instance stops until explicitly cleaned up.

## Implementation stages and acceptance gates

1. **Secure runtime and observability:** preserve sentiment API compatibility; add optional Bearer API-key authentication, byte-level request limits, real Prometheus request metrics, bounded inference, startup/readiness tests, and a settings-aware smoke test.
2. **Optional model extensions:** allowlisted Qwen3.5-0.8B text generation and Kitten Mini 0.8 CPU adapters; Groq text and Whisper transcription adapters. Disable these by default; use dedicated optional dependency files and explicit capability/readiness states. Do not substitute fake output or assert untested model performance.
3. **Containers and Kubernetes:** implement a non-root CPU image, Kubernetes Namespace/Deployment/NodePort Service/HPA, probes, requests/limits, secret references, and reproducible local image-loading instructions. Start with sentiment only for HPA measurement. Additional replicas on one node do not add compute.
4. **AWS infrastructure and delivery:** Terraform for Mumbai VPC/subnet/route/EC2/ECR/S3/IAM/SSM deployment document/log group/optional budget; bootstrap pinned k3s; manual GitHub Actions deployment after environment approval. Make resource creation an explicit operator action.
5. **Validation and evidence:** tests, YAML/manifest checks, Terraform formatting and provider validation, CI checks, repeatable load tests and recording scripts. Preserve empty performance results until measurements actually run. Docker/Kubernetes/AWS execution are separate gates if unavailable locally.
6. **Runbook:** local development, secret preparation, cost estimate, deploy/rollback, SSM access, HPA experiments, stop/start and guarded teardown; document verified versus unexecuted steps.

## Shared implementation contracts

- Existing `/api/inference` remains sentiment-only and backwards compatible; do not force generation/audio into binary sentiment response schemas.
- `app.extensions.install_extensions(application, settings)` installs optional routes. It owns its configuration and lifecycle; core calls this once from `create_app`. Endpoints: `/api/generate`, `/api/speech`, `/api/transcriptions`, `/api/capabilities`. All `/api/*` requests use the core authentication and body-limit middleware.
- Extension configuration uses `ENABLE_QWEN`, `ENABLE_KITTEN`, `ENABLE_GROQ`, `ENABLE_GROQ_TRANSCRIPTION`, `GROQ_API_KEY`; local extensions remain disabled in the default deployment. No remote URLs or model repository IDs are accepted from clients.
- Main namespace: `ai-serving`; Deployment/Service/HPA: `ai-model-serving`. NodePort: `30080`, container HTTP port: `8000`.
- Kubernetes Secret: `serving-secrets` with keys `api-key` and optional `groq-api-key`; deployment sets `API_KEY` and optional `GROQ_API_KEY` through secretKeyRef. Local documented secret creation may use an empty API key; cloud deployment must require a nonempty API key.
- `scripts/deploy-k3s.sh --image <ECR-image:immutable-tag>` renders and applies manifests on the instance, refreshes ECR pull credentials without printing them, and waits for rollout/readiness. Avoid shared RWO model caches across rolling/HPA replicas; use per-pod bounded writable storage.
- Terraform directory: `infra/terraform`. Outputs: `aws_region`, `instance_id`, `ecr_repository_url`, `artifact_bucket_name`, `github_deploy_role_arn`, `ssm_deploy_document_name`, `ssm_parameter_prefix`.
- GitHub environment: `aws-demo`. Variables: `AWS_ROLE_ARN`, `ECR_REPOSITORY`, `EC2_INSTANCE_ID`, `ARTIFACT_BUCKET`, `SSM_DEPLOY_DOCUMENT`; region is `ap-south-1`. Deployment bundle: `deploy/<commit-sha>.tar.gz`, verified by SHA-256. The SSM document accepts deployment object key, bundle checksum, and immutable image URI; IAM scopes access to this document, instance, bucket prefix, and repository.
- CI deploys existing infrastructure only. No automatic Terraform apply or instance start; no production claim until an authorized AWS smoke test succeeds.
- File ownership during parallel work: runtime agent owns core app/config/observability and runtime requirements/tests; extension agent owns app/extensions.py, app/adapters/, requirements-models.txt and extension tests; packaging agent owns Docker/Kubernetes/CI/load testing/deploy-k3s; infrastructure agent owns infra/terraform and AWS helper scripts. Parent owns plan, top-level docs, integration review and fixes.

## Budget assumptions

Previously checked Mumbai public rates: EC2 $0.10075/hour, public IPv4 $0.005/hour, gp3 $0.0912/GiB-month. A small scheduled lab with 100–200 running hours over 1–2 months was estimated at roughly $20–40 including modest usage reserve; 24/7 hosting is roughly $85/month before taxes/high traffic. These are estimates, not account-specific quotes or spending caps. AWS credits do not cover Groq billing.
