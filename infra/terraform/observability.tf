# CloudWatch log groups are pre-created with bounded retention (default 7 days)
# so the node role can be scoped to exactly these ARNs and the CloudWatch agent
# is never allowed to create new, unbounded groups.
resource "aws_cloudwatch_log_group" "k3s_pods" {
  name              = local.log_group_k3s_pods
  retention_in_days = var.log_retention_days

  tags = { Name = local.log_group_k3s_pods }
}

resource "aws_cloudwatch_log_group" "k3s_service" {
  name              = local.log_group_k3s_service
  retention_in_days = var.log_retention_days

  tags = { Name = local.log_group_k3s_service }
}

resource "aws_cloudwatch_log_group" "bootstrap" {
  name              = local.log_group_bootstrap
  retention_in_days = var.log_retention_days

  tags = { Name = local.log_group_bootstrap }
}
