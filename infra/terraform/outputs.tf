output "aws_region" {
  description = "Region the lab is deployed in."
  value       = var.aws_region
}

output "instance_id" {
  description = "EC2 k3s node instance id (target for SSM SendCommand / StartSession / stop-start helpers)."
  value       = aws_instance.k3s_node.id
}

output "ecr_repository_url" {
  description = "ECR repository URL; push/pull immutable <account>.dkr.ecr.ap-south-1.amazonaws.com/<repo>:<commit-sha> tags."
  value       = aws_ecr_repository.serving.repository_url
}

output "artifact_bucket_name" {
  description = "S3 bucket holding deploy/<commit-sha>.tar.gz bundles and results/ artifacts."
  value       = aws_s3_bucket.artifacts.bucket
}

output "github_deploy_role_arn" {
  description = "OIDC role for the GitHub Actions environment aws-demo (AWS_ROLE_ARN)."
  value       = aws_iam_role.github_deploy.arn
}

output "ssm_deploy_document_name" {
  description = "Custom SSM document taking ObjectKey + BundleSha256 + ImageUri (SSM_DEPLOY_DOCUMENT)."
  value       = aws_ssm_document.deploy.name
}

output "ssm_parameter_prefix" {
  description = "SSM parameter prefix for operator SecureStrings (api-key only)."
  value       = var.ssm_parameter_prefix
}
