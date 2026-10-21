# Custom deployment document. Parameters are strictly pattern-validated so the
# CI role can only reference: bundles under deploy/ named by a full 40-hex
# commit SHA, a lowercase SHA-256 checksum, and an immutable commit-SHA image tag.
resource "aws_ssm_document" "deploy" {
  name            = local.doc_name
  document_type   = "Command"
  document_format = "JSON"

  tags = { Name = local.doc_name }

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "ccd-lab deployment: download + SHA256-verify the bundle, extract to a fixed path, run scripts/deploy-k3s.sh --image, sync results/ to S3."
    parameters = {
      ObjectKey = {
        type           = "String"
        description    = "S3 key of the deploy bundle inside the artifact bucket (deploy/<commit-sha>.tar.gz)"
        allowedPattern = "^deploy/[0-9a-f]{40}\\.tar\\.gz$"
      }
      BundleSha256 = {
        type           = "String"
        description    = "Lowercase SHA-256 hex digest of the bundle"
        allowedPattern = "^[0-9a-f]{64}$"
      }
      ImageUri = {
        type           = "String"
        description    = "ECR image URI with an immutable 40-hex commit-SHA tag"
        allowedPattern = "^[0-9a-zA-Z][0-9a-zA-Z._-]*\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/[0-9a-zA-Z._/-]+:[0-9a-f]{40}$"
      }
    }
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "DeployVerifiedBundle"
        inputs = {
          timeoutSeconds = 3600
          runCommand = [templatefile("${path.module}/ssm_deploy_document.tftpl", {
            aws_region           = var.aws_region
            artifact_bucket      = local.bucket_name
            ecr_repository       = local.ecr_name
            ssm_parameter_prefix = var.ssm_parameter_prefix
          })]
        }
      }
    ]
  })
}
