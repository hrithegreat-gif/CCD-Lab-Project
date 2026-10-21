# AWS Runbook — Mumbai credit-budget k3s lab

Infrastructure as code for the single-node k3s AI-serving lab in `ap-south-1` (Mumbai).
Terraform directory: [`infra/terraform`](../infra/terraform). Everything here describes **planned, offline-validated** infrastructure: no `apply`, no AWS resource creation, and no cloud calls were made during implementation (see [Verified vs unexecuted](#verified-vs-unexecuted)).

## What Terraform creates

| Resource | Details |
| --- | --- |
| VPC / subnet / IGW | `10.0.0.0/16`, one public subnet `10.0.1.0/24`, IGW egress route. No NAT, no ALB, no EKS, no database, no GPU. |
| EC2 `m7i-flex.large` | 2 vCPU / 8 GiB, Amazon Linux 2023 x86_64 (AMI id resolved at plan time from the public SSM parameter `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64`), ephemeral public IPv4, encrypted 40 GiB gp3 root volume, `instance_initiated_shutdown_behavior = stop`. |
| IMDSv2 | `HttpTokens=required`, `HttpPutResponseHopLimit=1` — deliberate: pods cannot reach IMDS, so workloads never inherit the node role credentials. |
| Security group | **No inbound rules at all.** Egress-only. All operator access is via Systems Manager (session, Run Command, port forwarding to NodePort `30080`). |
| ECR | Private repo `<prefix>-serving`: `IMMUTABLE` tags, `scanOnPush=true`, AES256, lifecycle keeps the 10 most recent images. |
| S3 | Private encrypted bucket `<prefix>-artifacts-<account-id>`: AES256, all public access blocked, `BucketOwnerEnforced`, lifecycle expires `deploy/` + `results/` after 14 or 30 days (variable), `force_destroy=false` by default. |
| IAM node role | `AmazonSSMManagedInstanceCore` + inline: ECR pull (repo-scoped; `GetAuthorizationToken` on `*`), S3 `GetObject` on `deploy/*`, `ListBucket` (deploy/results prefixes), `PutObject` on `results/*`, `ssm:GetParameter(s)` under the specific prefix, `kms:Decrypt` constrained to `kms:ViaService = ssm.ap-south-1.amazonaws.com` with the parameter-ARN encryption context (alias ARNs are not valid decrypt resources), log writes scoped to the three groups below, and an **explicit Deny for `ssm:GetParameter(s)` outside the prefix** (the managed policy grants those on `Resource: *`; an inline Allow does not restrict it). Accepted residual, documented: Patch Manager snapshot access granted by the managed policy is out of scope. |
| IAM GitHub deploy role | OIDC trust `sts:AssumeRoleWithWebIdentity` with `StringEquals` on `aud = sts.amazonaws.com` and `sub = repo:hrithegreat-gif/CCD-Lab-Project:environment:aws-demo` (exact match, no wildcards). Permissions: ECR push (repo-scoped), S3 upload to `deploy/*`, `ssm:SendCommand` scoped to this document + this instance, `ssm:GetCommandInvocation` (invocation IDs are not ARN-addressable; blast radius bounded by the SendCommand scope). No admin, no discovery. |
| SSM deploy document | `<prefix>-deploy-k3s`: parameters `ObjectKey` (`^deploy/[0-9a-f]{40}\.tar\.gz$`), `BundleSha256` (`^[0-9a-f]{64}$`), `ImageUri` (ECR URI with immutable 40-hex commit-SHA tag). Downloads the bundle, **verifies SHA-256 before extraction**, then fail-closed screens the archive in two passes to a listing file (no SIGPIPE-prone pipes; only a clean grep "no match" proceeds): entry names (absolute paths / `..` traversal) **and** entry types (**symlinks, hardlinks, devices, fifos rejected**); validates the 40-hex work-dir name and refuses a symlinked work path **before any `rm -rf`**; extracts with `--no-same-owner --no-same-permissions` to `/opt/ccd-lab/deploy-bundles/<sha>`; runs `scripts/deploy-k3s.sh --image <ImageUri>` from that directory; syncs results to the **per-commit prefix `results/<sha>/`**; an `EXIT` trap appends `status=failed` + best-effort result sync while preserving the original exit code. |
| CloudWatch log groups | `/<prefix>/k3s-pods`, `/<prefix>/k3s-service`, `/<prefix>/bootstrap` — bounded 7-day retention (variable), pre-created so the agent never creates unbounded groups. |
| Optional budget | `<prefix>-monthly-account-wide` in us-east-1 (Budgets is a global API): **account-wide** alert, deliberately not tag-filtered — the `$100` credit is shared by every resource, and a tag filter would report $0 until the tag is activated as a cost-allocation tag, hiding overspend. Disclosures: alerts evaluate about once per day (up to ~24 h delay) and never block spend (**not a hard cap**), and this is a **monthly rate alert, not a lifetime cap on the credit balance**. Email subscriber optional. |

Bootstrap (`infra/terraform/user_data.tftpl`) **arms the auto-stop timer FIRST — before any package install or download — so even a failed bootstrap still powers the node off on schedule** (no unattended, broken instance billing indefinitely). It then installs a **pinned k3s release** (`v1.37.1+k3s1`, latest non-prerelease GitHub release, published 2026-09-30; embedded metrics-server v0.9.0), disables Traefik and servicelb, and installs the CloudWatch agent + jq from the AL2023 repos. It deliberately does **not** install git, clone/pull code, pull the deploy bundle, or pull images — first deployment is an explicit operator action through the SSM document. AWS CLI v2 and SSM Agent are already present on AL2023.

## Prerequisites

- **Terraform ≥ 1.5** (validated with 1.15.8) and the pinned AWS provider `5.100.0` (checksums recorded in `infra/terraform/.terraform.lock.hcl`, which is committed).
- **AWS CLI v2** on the operator machine, authenticated with your **own operator identity** (SSO/`aws login`/profile). No permanent keys are used anywhere in the stack.
- **Session Manager plugin** for the CLI (needed for port forwarding sessions): <https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html>
- Optional: `uvx` (Python tool runner) if you want the plan-based IAM verification below.

## Operator gates before any deployment

1. **Account eligibility and remaining credits** — check in the Billing console (Credits page) and confirm the credits cover EC2/S3/ECR/CloudWatch usage. Credits do **not** cover Groq or other third-party API billing.
2. **Budget opt-in** — set `budget_limit` / `budget_email` in `terraform.tfvars` (see `terraform.tfvars.example`). Remember the delayed-notification disclosure above.

## Cost estimate (estimates, not account-specific quotes)

Previously checked Mumbai public rates (from `docs/IMPLEMENTATION_PLAN.md`; verify current numbers on the [EC2 pricing page](https://aws.amazon.com/ec2/pricing/on-demand/) and [EBS pricing page](https://aws.amazon.com/ebs/pricing/)):

| Usage pattern | EC2 | Public IPv4 | gp3 40 GiB-month | Total |
| --- | --- | --- | --- | --- |
| 100 running hours | $10.08 | $0.50 | $3.648 | **$14.22** |
| 200 running hours | $20.15 | $1.00 | $3.648 | **$24.80** |
| 24/7 for one month (730 h) | $73.55 | $3.65 | $3.648 | **$80.85** |

Notes:

- Free Tier eligibility (per operator review of the current AWS Free Plan): for accounts on the newer Free Plan (activated after 2025-07-15) `m7i-flex.large` is listed as eligible — but eligibility is **not unlimited free compute**: usage still draws down the account's credits, so the `$100` budget is consumed by this lab either way. Treat the table above as credit consumption, not a free allowance.
- S3/ECR/CloudWatch lines are small at lab volumes; EBS, S3, ECR and log storage **survive instance stops** and keep billing until deleted.
- The auto-stop timer (below) exists precisely because a forgotten session costs ~$2.42/day at these rates.

## Offline validation (already run, repeatable)

From `infra/terraform/`:

```bash
terraform fmt -recursive -check     # formatting
terraform init -backend=false       # pinned provider + lockfile only; no backend, no AWS calls
terraform validate                  # provider schema validation
```

Results at implementation time: `fmt -check` clean, `validate` clean against `hashicorp/aws 5.100.0`.

Optional operator-side IAM verification **after an authorized plan** (not run during implementation; uses the plan JSON, not invented values):

```bash
terraform plan -out=plan.tfplan      # requires AWS access; git-ignored
terraform show -json plan.tfplan > plan.json
uvx iam-policy-autopilot@latest generate-policies C:\absolute\path\to\plan.json --region ap-south-1 --pretty
```

## Deploying

### 1. Configure and review

```bash
copy infra\terraform\terraform.tfvars.example infra\terraform\terraform.tfvars   # git-ignored
# fill in project_prefix, ssm_parameter_prefix, budget options, OIDC choice
cd infra\terraform
terraform init
terraform plan
```

**Plan review is a hard gate**: read every `will be created` line before continuing.

**Bootstrap-change wipe disclosure**: `user_data_replace_on_change = true` means any change to bootstrap inputs (`k3s_version`, `autostop_hours`, log group names, …) **replaces** the instance on the next apply — the node's EBS state is discarded and the ephemeral public IPv4 changes.

### 2. OIDC choice (avoid collisions)

- If your account **already has** an IAM OIDC provider for `token.actions.githubusercontent.com`: set `github_oidc_provider_arn` (find it with `aws iam list-open-id-connect-providers`) and leave `create_oidc_provider = false`.
- Only set `create_oidc_provider = true` when no such provider exists (they are account-global; a duplicate create collides). A precondition blocks passing both.

### 3. Apply (explicit operator action)

```bash
terraform apply
```

Then record the outputs:

| Output | GitHub environment variable (`aws-demo`) |
| --- | --- |
| `aws_region` | (region fixed: `ap-south-1`) |
| `github_deploy_role_arn` | `AWS_ROLE_ARN` |
| `ecr_repository_url` | `ECR_REPOSITORY` (repository name only, e.g. `ccd-lab-serving`) |
| `instance_id` | `EC2_INSTANCE_ID` |
| `artifact_bucket_name` | `ARTIFACT_BUCKET` |
| `ssm_deploy_document_name` | `SSM_DEPLOY_DOCUMENT` |
| `ssm_parameter_prefix` | (used for secret prep below) |

Create the GitHub environment **aws-demo** on the repository and set those variables there. CI deploys existing infrastructure only — there is no automatic Terraform apply and no instance start from CI.

### 4. Prepare secrets (operator-supplied, never in Terraform)

```bash
aws ssm put-parameter --name /ccd-lab/api-key --type SecureString --value "<nonempty-api-key>" --region ap-south-1
# optional generation adapter:
aws ssm put-parameter --name /ccd-lab/groq-api-key --type SecureString --value "<groq-key>" --region ap-south-1
```

The node role may read parameters only under the configured prefix (default `/ccd-lab`; reads outside the prefix are explicitly denied because the managed policy grants them broadly). The cloud deployment requires a **nonempty** API key: `scripts/deploy-k3s.sh` reads these with the instance role and materializes the `serving-secrets` Kubernetes Secret.

### 5. Deploy a commit (CI or manual)

CI (packaging workflow): builds the non-root image, pushes `<ecr-url>:<commit-sha>`, uploads `deploy/<commit-sha>.tar.gz` to `s3://<bucket>/deploy/`, assumes `AWS_ROLE_ARN` in the `aws-demo` environment, and invokes the SSM document with `ObjectKey`, `BundleSha256` (lowercase hex), and `ImageUri`.

Manual equivalent:

```bash
COMMIT_SHA=<full-40-hex-sha>
aws s3 cp deploy/$COMMIT_SHA.tar.gz s3://<bucket>/deploy/$COMMIT_SHA.tar.gz
aws ssm send-command \
  --document-name <ssm_deploy_document_name> \
  --instance-ids <instance_id> \
  --parameters "ObjectKey=deploy/$COMMIT_SHA.tar.gz,BundleSha256=<64-hex-sha-of-bundle>,ImageUri=<ecr-url>:$COMMIT_SHA" \
  --region ap-south-1
aws ssm get-command-invocation --command-id <command-id> --instance-id <instance_id> --region ap-south-1
```

The document verifies the SHA-256 **before** extracting and refuses unsafe tar entries.

## Reaching the service (SSM port forwarding, no inbound ports)

```bash
aws ssm start-session \
  --target <instance_id> --region ap-south-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["30080"],"localPortNumber":["30080"]}'
```

Then `http://localhost:30080/` reaches the NodePort through the encrypted SSM channel. Requires the Session Manager plugin on the operator machine. Unencrypted exposure on the internet is never opened.

## Observability (real collection paths)

The node ships an installed CloudWatch agent with this configuration (written to `/opt/aws/amazon-cloudwatch-agent/etc/config.json` at boot):

| File on the node | CloudWatch group | Notes |
| --- | --- | --- |
| `/var/log/containers/*.log` | `/<prefix>/k3s-pods` | k3s container logs (CRI symlink layout); **single shared stream** `{instance_id}` — events from all matched files interleave (k3s CRI lines carry no pod name, so per-pod attribution is not preserved in CloudWatch; use `kubectl logs` on the node for per-pod inspection) |
| `/var/log/k3s/k3s-service.log` | `/<prefix>/k3s-service` | k3s systemd journal, persisted by the `ccd-lab-k3s-logs` unit (`journalctl -f -u k3s`), locally rotated (3 × 50 MB max, compressed) |
| `/var/log/cloud-init-output.log` | `/<prefix>/bootstrap` | full bootstrap output incl. errors |

Stream names use only the documented `{instance_id}` placeholder (optionally with a static suffix). Per the CloudWatch agent documentation, wildcard `file_path` globs are re-evaluated periodically so newly matching files are picked up automatically; all files collected under one `collect_list` entry share that entry's log stream, so their events interleave.

Retention is fixed at 7 days (variable) on all three groups; the agent's own log stays local; the config contains no secrets; the node role can only write to these three groups and cannot create new ones. **End-to-end delivery has not been observed yet** (no instance has run) — verify on first boot via CloudWatch console and `amazon-cloudwatch-agent-ctl -a status` on the node.

## Stop / start / auto-stop

- **Auto-stop**: systemd timer `ccd-lab-autostop.timer` powers the node off `autostop_hours` (default **4 h**) after boot via `systemctl poweroff` — and it is armed **before any install step** in the bootstrap, so a failed bootstrap still stops on schedule. Because `instance_initiated_shutdown_behavior = stop`, the guest-initiated shutdown **stops** the instance and retains the EBS volume; no AWS API call is needed from the host. Override per deployment with the `autostop_hours` variable, or live on the node with `systemctl edit ccd-lab-autostop.timer` / `systemctl list-timers`.
- **Manual stop/start helpers** (explicit id + region + confirmation; no discovery, no SDK):
  - PowerShell: `scripts/aws-instance.ps1 status|stop|start -InstanceId i-... -Region ap-south-1 [-DryRun|-Yes]`
  - Bash: `scripts/aws-instance.sh status|stop|start --instance-id i-... --region ap-south-1 [--dry-run|--yes]`
- Every start re-arms the auto-stop timer and assigns a **new** ephemeral public IPv4. Stopped instances still bill for the 40 GiB gp3 volume. HPA experiment guidance lives in `k8s/README.md` / `docs/DEMO.md` (owner: packaging agent).

## Teardown (guarded)

1. `terraform plan -destroy` and **read the plan** — this is the review gate.
2. **S3 bucket**: `artifact_bucket_force_destroy` defaults to `false`, so destroy **fails while the bucket is non-empty**. Either empty it first (`aws s3 rm s3://<bucket> --recursive` — permanently deletes all bundles/results) or, after consciously reviewing the contents, pass `-var artifact_bucket_force_destroy=true`. If you abandon the teardown without emptying it, the retained objects keep accruing S3 storage cost.
3. **ECR images**: deleted together with the repository on destroy (the lifecycle policy already caps the repo at 10 images).
4. Everything else is removed with the stack: instance (encrypted EBS deleted via `delete_on_termination`), VPC/IGW/SG, both roles and the deploy document, the three log groups, and the optional budget.
5. **Not managed by Terraform**: operator SSM SecureStrings under the parameter prefix — delete them manually (`aws ssm delete-parameter`); and an OIDC provider you supplied by ARN — untouched.

## Verified vs unexecuted

| Status | Item |
| --- | --- |
| Verified (offline) | `terraform fmt -check`; `terraform validate` with pinned `hashicorp/aws 5.100.0` + generated lockfile; `terraform test` (mock provider, zero AWS calls): singular `Resource` in every policy statement, explicit prefix Deny, KMS key/\*-only + via-SSM constraints (alias ARNs forbidden), raw `user_data` (no double-encoding) + replacement opt-in, auto-stop timer armed before installs, verified `{instance_id}` stream placeholders, SSM document contract (params/env exports/set flags/symlink-hardlink-device rejection/tar flags/trap/per-commit results prefix), storage and log bounds; `bash tests/deploy_doc_safety_test.sh` — functional safety suite (real tar, stubbed aws, zero AWS): safe bundle end-to-end, traversal/symlink/hardlink/corrupt bundles refused before extraction, SHA mismatch rejected with no sync; `bash -n` on both rendered shell templates and on `scripts/aws-instance.sh`; PowerShell guard tests (refusal without `-Yes`, `ValidateSet`, instance-id pattern) |
| Unexecuted (needs AWS) | `terraform plan/apply`; AMI resolution via the SSM parameter (validated only at plan time); **m7i-flex.large availability in ap-south-1** (verify with `aws ec2 describe-instance-type-offerings --location-type region --filters Name=instance-type,Values=m7i-flex.large` if capacity errors occur); first boot + pinned k3s install; `amazon-cloudwatch-agent` availability from AL2023 dnf repos and end-to-end log delivery; presence of `/var/log/containers` symlinks on k3s; SSM document execution; `scripts/deploy-k3s.sh` integration; OIDC assume from GitHub Actions; budget creation; SSM port forwarding |
