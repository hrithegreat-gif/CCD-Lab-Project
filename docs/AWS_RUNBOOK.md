# AWS runbook

## Status

Terraform and the deployment workflow are prepared in this repository. No AWS
apply, service deployment, authenticated cloud detection, CloudWatch delivery,
or AWS HPA acceptance is claimed. These steps require an owner-controlled AWS
account, GitHub administrator access, explicit spend approval, and credentials.

## Design

The target is one `m7i-flex.large` EC2 node in `ap-south-1`, running pinned k3s.
Terraform provisions private ECR/S3, scoped IAM, SSM, CloudWatch log groups,
and budget alerts. The security group has no inbound rules. GitHub Actions uses
OIDC and immutable commit-SHA image tags; SSM receives a checksum-verified
deployment bundle. Operators access the NodePort only through encrypted SSM
port forwarding.

## Owner-operated sequence

For the exact setup commands, GitHub environment variables, SSM key creation,
deployment, tunnel verification, and teardown instructions, follow
[the AWS setup guide](AWS_SETUP.md).

1. Review [budget notes](BUDGET.md), account/credit eligibility, and
   `infra/terraform/terraform.tfvars.example`. Set an approved budget email and
   existing GitHub OIDC provider ARN (or deliberately create the provider).
2. From `infra/terraform`, run `terraform init`, `terraform plan`, review the
   complete plan, and apply only after approval. Record outputs for the EC2
   instance, ECR repository, artifact bucket, deploy role, and SSM document.
3. Create `/ccd-lab/api-key` as an SSM SecureString outside Terraform. Do not put
   the key in git, Terraform variables/state, CI logs, or documentation.
4. Configure and protect the GitHub `aws-demo` environment with the role ARN,
   ECR repository name, bucket, SSM document, and instance ID. Require an
   authorized reviewer.
5. Start the instance using `scripts/aws-instance.sh` immediately before a
   deployment. Wait for bootstrap and verify the k3s node and Metrics Server.
6. Obtain successful CI for the exact commit SHA, then manually dispatch
   `.github/workflows/deploy-aws.yml` and approve the environment deployment.
7. Use `aws ssm start-session` with `AWS-StartPortForwardingSession` to forward
   NodePort 30080 to a local port. Run `scripts/smoke_test.py` against that
   forwarded URL with the API key supplied through the environment.
8. For cloud HPA acceptance, create the k6 ConfigMap from both the script and
   sample image, apply `k8s/load-generator.yaml`, and record replica/CPU/latency
   behavior. Store evidence in the approved results bucket without secrets.
9. Check CloudWatch log delivery, stop the instance, and perform the documented
   teardown when the owner no longer needs the deployment. A stopped instance
   can still incur EBS, ECR, S3, and log-storage charges.

Do not open inbound ports or expose the NodePort as plaintext public HTTP.
Never bypass checksum, archive-path, OIDC, or SSM secret protections. Confirm
region capacity and account-plan eligibility before applying infrastructure.