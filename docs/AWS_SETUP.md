# Wire the detection service to AWS

This guide is for the repository owner to connect the existing detection
service to the AWS lab stack. AWS setup and deployment are intentionally
manual: the GitHub workflow does not create infrastructure, start EC2, or
change firewall rules.

The stack is pinned to `ap-south-1`. It creates one EC2/k3s node, private ECR
and S3 resources, CloudWatch log groups, IAM roles, an SSM deploy document, and
an optional budget alert. These resources cost money. A budget alert is not a
spending cap; stopping EC2 does not stop EBS, ECR, S3, or CloudWatch charges.
Review [the budget notes](BUDGET.md) and approve the expected spend before
proceeding.

## 1. Prerequisites and account checks

You need:

- An AWS account/region authorized for the selected instance type, with
  `ap-south-1` capacity available.
- AWS CLI v2 authenticated as an operator using your organization's approved
  SSO/identity flow. Do not create long-lived access keys for this project.
- Terraform `>= 1.5, < 2.0`.
- GitHub administrator access to `hrithegreat-gif/CCD-Lab-Project`, including
  permission to create a protected environment and configure its variables.
- Approval to create billable AWS resources.

Confirm the account identity and region before running Terraform:

```powershell
aws sts get-caller-identity
aws configure get region
```

The Terraform provider pins the deployment region to `ap-south-1`; an AWS CLI
default region does not change that.

## 2. Configure GitHub OIDC and Terraform inputs

GitHub Actions authenticates to AWS with OIDC; do not create or store AWS access
keys in GitHub. The Terraform role trusts only this exact subject:

```text
repo:hrithegreat-gif/CCD-Lab-Project:environment:aws-demo
```

Check whether the account already has the GitHub OIDC provider:

```powershell
aws iam list-open-id-connect-providers `
  --query "OpenIDConnectProviderList[].Arn" --output text
```

Use an existing ARN in `infra/terraform/terraform.tfvars`, or set
`create_oidc_provider = true` there only if no provider for
`token.actions.githubusercontent.com` exists in the account. Provider creation
is account-global; do not create a duplicate. Set the budget email and any
other approved overrides in `terraform.tfvars`. Never put API keys or other
secrets in this file.

```powershell
Set-Location infra/terraform
Copy-Item terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with the existing OIDC provider ARN (or the explicit
# create_oidc_provider choice), repository, and approved budget settings.
```

The default repository trust is the repository named above. If deploying a
fork or a renamed repository, set `github_repository` to the exact `owner/repo`
before planning.

## 3. Plan and apply infrastructure

Run Terraform from `infra/terraform`. Review the full plan and verify the
account, region, resources, and estimated spend before approving `apply`.
`terraform init` and `terraform plan` may call AWS APIs; `terraform apply`
creates billable resources.

```powershell
terraform fmt -check -recursive
terraform init
terraform validate
terraform test
terraform plan -out=tfplan
terraform show tfplan
# Only after reviewing and approving the plan:
terraform apply tfplan
```

Do not commit `terraform.tfvars`, `tfplan`, or Terraform state. Terraform
outputs contain resource identifiers, not the model API key:

```powershell
$InstanceId = terraform output -raw instance_id
$EcrUrl = terraform output -raw ecr_repository_url
$ArtifactBucket = terraform output -raw artifact_bucket_name
$DeployRoleArn = terraform output -raw github_deploy_role_arn
$SsmDocument = terraform output -raw ssm_deploy_document_name
```

The workflow needs the ECR repository **name**, not its full URL:

```powershell
$EcrRepository = ($EcrUrl -split '/')[-1]
```

Wait for the EC2 bootstrap to finish. In AWS Systems Manager, confirm the
instance is online as a managed node and that the k3s/CloudWatch bootstrap
completed before attempting deployment. The node security group intentionally
has no inbound rules; do not open SSH, the NodePort, or public HTTP.

## 4. Add the model API key to SSM Parameter Store

In AWS Systems Manager → Parameter Store, create:

- Name: `/ccd-lab/api-key`
- Tier: Standard
- Type: `SecureString`
- Value: a newly generated, high-entropy API key

Use the AWS-managed KMS key unless the deployment owner has deliberately
configured and verified a customer-managed key policy. Do not put this key in
Git, Terraform variables/state, a GitHub variable, a workflow input, or a log.
The key is only retrieved by authorized operators for local smoke tests; the
service receives it on the node as a Kubernetes Secret.

## 5. Protect and populate the GitHub environment

In the repository settings, create the GitHub **environment** named exactly
`aws-demo`. Configure a required reviewer, prevent self-review where available,
and restrict deployments to the intended protected branch. Add these as
environment **variables** (not secrets); the workflow references them as
`vars.*`:

| Variable | Value |
| --- | --- |
| `AWS_ROLE_ARN` | Terraform output `github_deploy_role_arn` |
| `ECR_REPOSITORY` | Repository name only, e.g. `ccd-lab-serving` |
| `ARTIFACT_BUCKET` | Terraform output `artifact_bucket_name` |
| `SSM_DEPLOY_DOCUMENT` | Terraform output `ssm_deploy_document_name` |
| `EC2_INSTANCE_ID` | Terraform output `instance_id` |

There is no AWS credential secret. The workflow requests a short-lived OIDC
token and assumes the role whose trust policy is bound to this repository and
the `aws-demo` environment.

## 6. Deploy a reviewed commit

Push the exact commit you intend to deploy and wait for the repository's
`ci.yml` workflow to succeed for that commit. The deployment workflow checks
for that exact successful CI run and fails closed if it cannot find one.

Start the stopped EC2 instance just before deployment. From Git Bash or WSL in
the repository root, use the guarded helper with the explicit instance ID:

```bash
bash scripts/aws-instance.sh start \
  --instance-id <instance-id> --region ap-south-1 --yes
bash scripts/aws-instance.sh status \
  --instance-id <instance-id> --region ap-south-1
```

Replace `<instance-id>` with the `instance_id` Terraform output.

In GitHub → Actions → `deploy-aws`, choose the reviewed branch and run the
manual workflow. Approve the `aws-demo` environment deployment when prompted.
The workflow builds and smoke-tests the image, pushes an immutable commit-SHA
tag to ECR, uploads a checksum-protected bundle to S3, invokes the scoped SSM
document, and waits for the command result. Review the workflow summary and SSM
command output; do not treat a queued run as a successful deployment.

## 7. Verify the service through SSM

Start an SSM port-forward in one terminal; keep it running for the smoke check.
The NodePort is reached through the SSM-managed node, not through an inbound
security-group rule:

```powershell
$InstanceId = terraform -chdir=infra/terraform output -raw instance_id
aws ssm start-session `
  --target $InstanceId `
  --document-name AWS-StartPortForwardingSession `
  --parameters '{"portNumber":["30080"],"localPortNumber":["30080"]}'
```

In a second PowerShell terminal, retrieve the key without writing it to the
console, run the contract checks, and clear it from the environment:

```powershell
$ApiKey = aws ssm get-parameter `
  --name /ccd-lab/api-key --with-decryption `
  --query Parameter.Value --output text
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ApiKey)) {
  throw "Could not retrieve /ccd-lab/api-key"
}
$env:API_KEY = $ApiKey.Trim()
Remove-Variable ApiKey
try {
  & .\.venv\Scripts\python.exe scripts\smoke_test.py `
    --base-url http://127.0.0.1:30080
  if ($LASTEXITCODE -ne 0) { throw "Detection smoke test failed" }
}
finally {
  Remove-Item Env:API_KEY -ErrorAction SilentlyContinue
}
```

The operator identity needs `ssm:GetParameter` with decryption permission for
this path. The smoke check verifies authenticated inference and API validation;
it does not establish a performance or HPA acceptance result.

## 8. Capture cloud acceptance evidence

For HPA/performance acceptance, run the bounded in-cluster load-generator Job
and observe replicas, resource metrics, and scale-in through the full
stabilization window. Follow the [load-test guide](../loadtest/README.md) for
the request contract and experiment procedure. Do not use the SSM port-forward
as a substitute for in-cluster throughput measurements.

Capture, without secrets:

- Commit SHA, workflow run URL, image URI/tag, and successful SSM command ID.
- Authenticated smoke-test result and UTC timestamp.
- HPA and pod replica samples spanning baseline, scale-out, sustained load,
  and return to one replica.
- CPU/memory metrics, successful inference latency/throughput, expected
  saturation responses, and CloudWatch log-delivery confirmation.
- Instance stop and Terraform teardown confirmation.

Store artifacts only in the approved project location. Do not include API keys,
OIDC tokens, access keys, or decrypted SSM values.

## 9. Stop resources and tear down deliberately

After evidence collection, stop the EC2 instance using the explicit ID:

```bash
bash scripts/aws-instance.sh stop \
  --instance-id "$INSTANCE_ID" --region ap-south-1 --yes
```

Stopping EC2 still leaves storage and logging charges. If the lab is no longer
needed, review `terraform plan -destroy`, then destroy only after confirming
that retained artifacts are either no longer needed or have been backed up.
The artifact bucket defaults to refusing destruction while it contains
objects; do not enable force-destroy without explicit data-owner approval.
Remove `/ccd-lab/api-key` separately in Parameter Store if it is no longer
needed. Confirm the final account resources and remaining costs in AWS.

For API behavior, architecture, and local operations see the [project
runbook](RUNBOOK.md). For workflow behavior see [CI/CD](CI_CD.md). Cloud
deployment remains owner-operated; local evidence does not imply AWS
acceptance.
