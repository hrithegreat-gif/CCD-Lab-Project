locals {
  account_id = data.aws_caller_identity.current.account_id

  # Resource names (deterministic; bucket names are account-scoped unique).
  bucket_name  = "${var.project_prefix}-artifacts-${local.account_id}"
  ecr_name     = "${var.project_prefix}-serving"
  vpc_name     = "${var.project_prefix}-vpc"
  subnet_name  = "${var.project_prefix}-public-subnet"
  sg_name      = "${var.project_prefix}-node-sg"
  role_name    = "${var.project_prefix}-ec2-node-role"
  profile_name = "${var.project_prefix}-ec2-node-profile"
  deploy_role  = "${var.project_prefix}-github-deploy-role"
  doc_name     = "${var.project_prefix}-deploy-k3s"

  # CloudWatch log groups (bounded retention; pre-created so the node role can
  # be scoped to exactly these ARNs and the agent never creates new groups).
  log_group_k3s_pods    = "/${var.project_prefix}/k3s-pods"
  log_group_k3s_service = "/${var.project_prefix}/k3s-service"
  log_group_bootstrap   = "/${var.project_prefix}/bootstrap"

  # Repositories created after 2026-07-15 use immutable owner/repository IDs
  # in the subject claim. Keep the environment qualifier exact (StringEquals).
  github_owner     = split("/", var.github_repository)[0]
  github_repo_name = split("/", var.github_repository)[1]
  github_sub       = "repo:${local.github_owner}@${var.github_owner_id}/${local.github_repo_name}@${var.github_repository_id}:environment:aws-demo"

  # Classic GitHub token-signing thumbprints. AWS no longer strictly requires
  # thumbprints for GitHub OIDC (it validates via trusted CAs), but including
  # them is harmless and keeps older tooling compatible.
  github_oidc_thumbprints = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]

  # Use the operator-supplied provider ARN when given, else the one created here.
  # try() is required: the created-provider branch is indexed even when count = 0.
  oidc_provider_arn = var.github_oidc_provider_arn != null ? var.github_oidc_provider_arn : try(aws_iam_openid_connect_provider.github[0].arn, null)
}
