################ Node (EC2 instance) role ################

resource "aws_iam_role" "node" {
  name = local.role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = { Name = local.role_name }
}

resource "aws_iam_instance_profile" "node" {
  name = local.profile_name
  role = aws_iam_role.node.name

  tags = { Name = local.profile_name }
}

# Baseline SSM agent channels (session, run command, ssmmessages) via the AWS
# managed policy — the standard prerequisite for keyless access.
resource "aws_iam_role_policy_attachment" "node_ssm_core" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Everything else is scoped inline: ECR pull on this repository, S3 deploy-read
# and results-write prefixes, SSM parameter reads under the specific prefix,
# and log writes to the three pre-created groups only.
resource "aws_iam_role_policy" "node_scoped" {
  name = "scoped-lab-access"
  role = aws_iam_role.node.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # GetAuthorizationToken cannot be scoped to a repository.
        Sid      = "EcrAuthToken"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = ["*"]
      },
      {
        Sid    = "EcrPullThisRepository"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:DescribeImages"
        ]
        Resource = [aws_ecr_repository.serving.arn]
      },
      {
        Sid      = "S3ReadDeployBundles"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.artifacts.arn}/deploy/*"]
      },
      {
        Sid      = "S3ListBundlesAndResults"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.artifacts.arn]
        Condition = {
          StringLike = {
            "s3:prefix" = ["deploy/*", "results/*"]
          }
        }
      },
      {
        Sid      = "S3WriteResults"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = ["${aws_s3_bucket.artifacts.arn}/results/*"]
      },
      {
        Sid      = "SsmReadOperatorParameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = ["arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${var.ssm_parameter_prefix}/*"]
      },
      {
        # Required for `ssm get-parameter --with-decryption` on SecureStrings.
        # KMS evaluates the KEY ARN (alias ARNs are not valid decrypt
        # resources), so Resource is scoped to the account's keys only, and
        # every call is constrained to SSM with the parameter-ARN encryption
        # context. key/* also matches customer-managed keys, but a CMK only
        # works when its KEY POLICY grants this role Decrypt; for tighter
        # control prefer replacing the wildcard with that CMK's explicit key
        # ARN. There is deliberately no alias/data-source lookup here (a
        # plan-time AWS call).
        Sid      = "KmsDecryptForSsmSecureStrings"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = ["arn:aws:kms:${var.aws_region}:${local.account_id}:key/*"]
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com"
          }
          StringLike = {
            "kms:EncryptionContext:PARAMETER_ARN" = "arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${var.ssm_parameter_prefix}/*"
          }
        }
      },
      {
        # AmazonSSMManagedInstanceCore grants ssm:GetParameter(s) on Resource:*;
        # the prefix Allow above does NOT restrict that managed grant. Explicitly
        # deny reads of any parameter OUTSIDE the lab prefix (NotResource inside
        # a Deny). Residual accepted and documented: Patch Manager snapshot
        # access (GetDeployablePatchSnapshotForInstance) granted by the managed
        # policy is out of scope for this lab.
        Sid         = "DenySsmParameterReadsOutsidePrefix"
        Effect      = "Deny"
        Action      = ["ssm:GetParameter", "ssm:GetParameters"]
        NotResource = ["arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${var.ssm_parameter_prefix}/*"]
      },
      {
        Sid    = "LogsWriteLabGroups"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = [
          "${aws_cloudwatch_log_group.k3s_pods.arn}:*",
          "${aws_cloudwatch_log_group.k3s_service.arn}:*",
          "${aws_cloudwatch_log_group.bootstrap.arn}:*"
        ]
      },
      {
        # Read-only group metadata so the agent can verify group existence;
        # CreateLogGroup is intentionally NOT granted (groups are Terraform-managed).
        Sid      = "LogsDescribeGroups"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups"]
        Resource = ["*"]
      }
    ]
  })
}

################ GitHub OIDC deploy role ################

# Opt-in provider creation: leave create_oidc_provider=false and pass the ARN of
# the account's existing GitHub OIDC provider to avoid a duplicate-entity collision.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = local.github_oidc_thumbprints

  tags = { Name = "${var.project_prefix}-github-oidc" }

  lifecycle {
    precondition {
      condition     = var.github_oidc_provider_arn == null
      error_message = "Set either create_oidc_provider = true OR github_oidc_provider_arn, not both."
    }
  }
}

data "aws_iam_policy_document" "deploy_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_sub]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = local.deploy_role
  assume_role_policy = data.aws_iam_policy_document.deploy_trust.json

  tags = { Name = local.deploy_role }
}

# Minimal CI deployment permissions: push to this ECR repository, upload bundles
# to deploy/, send + track this SSM document against this instance only.
resource "aws_iam_role_policy" "github_deploy_scoped" {
  name = "scoped-deploy-actions"
  role = aws_iam_role.github_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # GetAuthorizationToken cannot be scoped to a repository.
        Sid      = "EcrAuthToken"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = ["*"]
      },
      {
        Sid    = "EcrPushThisRepository"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage"
        ]
        Resource = [aws_ecr_repository.serving.arn]
      },
      {
        Sid      = "S3UploadDeployBundle"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = ["${aws_s3_bucket.artifacts.arn}/deploy/*"]
      },
      {
        Sid    = "SsmSendDeployCommand"
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = [
          aws_ssm_document.deploy.arn,
          "arn:aws:ec2:${var.aws_region}:${local.account_id}:instance/${aws_instance.k3s_node.id}"
        ]
      },
      {
        # Command-invocation IDs are not addressable ARNs, so this read-only
        # status call cannot be resource-scoped; blast radius is already bounded
        # by scoping SendCommand to this document + this instance.
        Sid      = "SsmTrackDeployCommand"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation"]
        Resource = ["*"]
      }
    ]
  })
}
