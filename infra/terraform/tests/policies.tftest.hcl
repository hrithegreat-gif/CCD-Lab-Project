# Offline semantic regression tests for the IAM/SSM/compute configuration.
#
# Run: terraform test   (from infra/terraform/)
#
# Uses mock_provider: the plan is evaluated against mocks and makes ZERO AWS
# API calls. These tests complement the repository-level checks (e.g.
# tests/test_terraform_safety.py owned by the parent) — they assert PLAN-TIME
# semantics such as JSON policy shape, which `terraform validate` cannot see.
# Each provider configuration needs its own mock declaration.
# Pin the mocked trust document too: the real aws_iam_policy_document rendering
# is not what these tests assert.
mock_provider "aws" {
  # Give the ARN-bearing resources realistic shapes so the scoping assertions
  # below can check ARN patterns instead of opaque mock ids.
  mock_resource "aws_ssm_document" {
    defaults = {
      arn = "arn:aws:ssm:ap-south-1:123456789012:document/ccd-lab-deploy-k3s"
    }
  }

  mock_resource "aws_ecr_repository" {
    defaults = {
      arn = "arn:aws:ecr:ap-south-1:123456789012:repository/ccd-lab-serving"
    }
  }

  override_data {
    target = data.aws_caller_identity.current
    values = {
      account_id = "123456789012"
    }
  }

  override_data {
    target = data.aws_iam_policy_document.deploy_trust
    values = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"sts:AssumeRoleWithWebIdentity\",\"Principal\":{\"Federated\":\"arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com\"}}]}"
    }
  }
}

mock_provider "aws" {
  alias = "global"
}

# Keep the trust-policy identifier non-null in the mock context (no OIDC
# provider is created here, so the ARN must come from the existing-provider var).
variables {
  github_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
}

run "github_oidc_subject_uses_immutable_ids" {
  command = plan

  assert {
    condition     = local.github_sub == "repo:hrithegreat-gif@264411662/CCD-Lab-Project@1416389614:environment:aws-demo"
    error_message = "GitHub OIDC trust must use immutable owner/repository IDs and the exact deployment environment."
  }
}

# Note: .tftest.hcl supports no locals blocks — the SSM runCommand expression
# join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand)
# is inlined in each assertion below.

# ---- IAM policies: AWS JSON uses singular Resource; `Resources` is invalid
# and fails at apply with MalformedPolicyDocument. ----

run "node_policy_statements_use_singular_resource" {
  command = apply

  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.node_scoped.policy).Statement : contains(keys(s), "Resource") || contains(keys(s), "NotResource")])
    error_message = "node policy: a statement has neither singular 'Resource' nor 'NotResource'"
  }

  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.node_scoped.policy).Statement : !contains(keys(s), "Resources")])
    error_message = "node policy: statement uses invalid plural 'Resources'"
  }
}

run "deploy_policy_statements_use_singular_resource" {
  command = apply

  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.github_deploy_scoped.policy).Statement : contains(keys(s), "Resource")])
    error_message = "deploy policy: a statement is missing singular 'Resource'"
  }

  assert {
    condition     = alltrue([for s in jsondecode(aws_iam_role_policy.github_deploy_scoped.policy).Statement : !contains(keys(s), "Resources")])
    error_message = "deploy policy: statement uses invalid plural 'Resources'"
  }
}

# ---- Parameter access is restricted to the lab prefix despite the broad
# AmazonSSMManagedInstanceCore grant (explicit Deny with NotResource). ----

run "node_policy_denies_parameters_outside_prefix" {
  command = apply

  assert {
    condition = length([
      for s in jsondecode(aws_iam_role_policy.node_scoped.policy).Statement :
      s if s.Sid == "DenySsmParameterReadsOutsideApiKey" && s.Effect == "Deny" && length(lookup(s, "NotResource", [])) > 0 && alltrue([for arn in lookup(s, "NotResource", []) : endswith(arn, "/ccd-lab/api-key")])
    ]) == 1
    error_message = "node policy: missing explicit Deny (NotResource) for parameter reads outside the prefix"
  }

  assert {
    condition     = strcontains(join(" ", [for s in jsondecode(aws_iam_role_policy.node_scoped.policy).Statement : join(" ", lookup(s, "Action", []))]), "ssm:GetParameter")
    error_message = "node policy: expected ssm:GetParameter actions in the Allow/Deny pair"
  }
}

# ---- kms:Decrypt must be constrained to SSM + the parameter-ARN encryption
# context (alias ARNs are not valid decrypt resources). ----

run "kms_decrypt_constrained_to_ssm_context" {
  command = apply

  assert {
    condition = length([
      for s in jsondecode(aws_iam_role_policy.node_scoped.policy).Statement :
      s if s.Sid == "KmsDecryptForSsmSecureStrings" && lookup(lookup(lookup(s, "Condition", {}), "StringEquals", {}), "kms:ViaService", "") == "ssm.ap-south-1.amazonaws.com" && lookup(lookup(lookup(s, "Condition", {}), "StringLike", {}), "kms:EncryptionContext:PARAMETER_ARN", "") != ""
    ]) == 1
    error_message = "node policy: kms:Decrypt statement must constrain kms:ViaService and the PARAMETER_ARN encryption context"
  }

  assert {
    condition = alltrue([for r in [
      for s in jsondecode(aws_iam_role_policy.node_scoped.policy).Statement :
      s if s.Sid == "KmsDecryptForSsmSecureStrings"
    ][0].Resource : strcontains(r, ":key/") && !strcontains(r, ":alias/")])
    error_message = "node policy: kms:Decrypt Resource must be account-scoped key/* only — alias ARNs are not valid decrypt resources"
  }
}

# ---- Deploy role: SendCommand scoped to this document AND this instance. ----

run "deploy_role_sendcommand_scoped" {
  command = apply

  assert {
    condition = length([
      for s in jsondecode(aws_iam_role_policy.github_deploy_scoped.policy).Statement :
      s if s.Sid == "SsmSendDeployCommand" && length(s.Resource) == 2 && anytrue([for r in s.Resource : strcontains(r, ":document/")]) && anytrue([for r in s.Resource : strcontains(r, ":instance/")])
    ]) == 1
    error_message = "deploy policy: ssm:SendCommand must be scoped to exactly the document and instance ARNs"
  }

  assert {
    condition = length([
      for s in jsondecode(aws_iam_role_policy.github_deploy_scoped.policy).Statement :
      s if s.Sid == "EcrPushThisRepository" && length(s.Resource) == 1 && strcontains(s.Resource[0], ":repository/")
    ]) == 1
    error_message = "deploy policy: ECR push must be scoped to the single lab repository"
  }
}

# ---- SSM deploy document contract ----

run "ssm_document_parameters_and_env_exports" {
  command = apply

  assert {
    condition     = length(keys(jsondecode(aws_ssm_document.deploy.content).parameters)) == 3
    error_message = "SSM document must declare exactly ObjectKey, BundleSha256, ImageUri"
  }

  assert {
    condition     = jsondecode(aws_ssm_document.deploy.content).parameters.ObjectKey.allowedPattern == "^deploy/[0-9a-f]{40}\\.tar\\.gz$"
    error_message = "ObjectKey allowedPattern drifted"
  }

  assert {
    condition     = jsondecode(aws_ssm_document.deploy.content).parameters.BundleSha256.allowedPattern == "^[0-9a-f]{64}$"
    error_message = "BundleSha256 allowedPattern drifted"
  }

  assert {
    condition     = jsondecode(aws_ssm_document.deploy.content).parameters.ImageUri.allowedPattern == "^[0-9a-zA-Z][0-9a-zA-Z._-]*\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/[0-9a-zA-Z._/-]+:[0-9a-f]{40}$"
    error_message = "ImageUri allowedPattern must require an ECR URI with an immutable 40-hex commit-SHA tag"
  }

  assert {
    condition     = strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "export AWS_REGION=") && strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "export AWS_DEFAULT_REGION=")
    error_message = "SSM document must export BOTH AWS_REGION and AWS_DEFAULT_REGION for the deploy script"
  }

  assert {
    condition     = strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "set -euo pipefail") && !strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "set -x")
    error_message = "SSM document must use 'set -euo pipefail' WITHOUT -x (secret privacy)"
  }

  assert {
    condition     = strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "sha256sum -c") && strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "tar -tzf")
    error_message = "SSM document must verify SHA256 and screen tar entries before extraction"
  }

  assert {
    condition     = strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "(^|/)\\.\\.(/|$)|^/") && strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "grep -E '^[lhbcp]'")
    error_message = "SSM document must fail-closed reject traversal/absolute paths AND symlink/hardlink/device entries"
  }

  assert {
    condition     = strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "--no-same-owner --no-same-permissions")
    error_message = "SSM document must extract with --no-same-owner --no-same-permissions"
  }

  assert {
    condition     = strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "trap finish EXIT") && strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "results/$BASE/")
    error_message = "SSM document must record failure artifacts via EXIT trap and sync to the per-commit results prefix"
  }

  assert {
    condition     = strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "$${#BASE}") && strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "-ne 40")
    error_message = "SSM document must validate the 40-hex commit-SHA work dir before any rm -rf"
  }
}

# ---- Instance bootstrap: raw user_data (no double-encoding), replacement
# opt-in, IMDSv2 with hop limit 1, stop-on-guest-shutdown, no ingress. ----

run "instance_hardening_and_bootstrap_contract" {
  command = apply

  assert {
    condition     = startswith(aws_instance.k3s_node.user_data, "#!/bin/bash")
    error_message = "user_data must be the RAW rendered script (raw templatefile, no pre-encoding)"
  }

  assert {
    condition     = !can(base64decode(aws_instance.k3s_node.user_data))
    error_message = "user_data looks like base64 — double-encoding regression"
  }

  assert {
    condition     = aws_instance.k3s_node.user_data_replace_on_change == true
    error_message = "user_data_replace_on_change must be true (bootstrap changes replace the node)"
  }

  assert {
    condition     = aws_instance.k3s_node.metadata_options[0].http_tokens == "required"
    error_message = "IMDSv2 HttpTokens=required regression"
  }

  assert {
    condition     = aws_instance.k3s_node.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "IMDS hop limit must stay 1 (pods must not inherit host credentials)"
  }

  assert {
    condition     = aws_instance.k3s_node.instance_initiated_shutdown_behavior == "stop"
    error_message = "guest-initiated shutdown must STOP the instance (EBS retained)"
  }

  assert {
    condition     = length(aws_security_group.node.ingress) == 0
    error_message = "security group must have NO ingress rules"
  }

  assert {
    condition     = strcontains(split("dnf install -y jq", aws_instance.k3s_node.user_data)[0], "systemctl enable --now ccd-lab-autostop.timer")
    error_message = "auto-stop timer must be armed BEFORE any package install (failed boots must still power off)"
  }

  assert {
    condition     = strcontains(aws_instance.k3s_node.user_data, "\"log_stream_name\": \"{instance_id}\"") && !strcontains(aws_instance.k3s_node.user_data, "{file_path}")
    error_message = "CW stream names must use the verified {instance_id} placeholder only (no {file_path})"
  }
}

# ---- Storage + observability bounds ----

run "storage_and_log_bounds" {
  command = apply

  assert {
    condition     = aws_s3_bucket_public_access_block.artifacts.block_public_acls && aws_s3_bucket_public_access_block.artifacts.block_public_policy && aws_s3_bucket_public_access_block.artifacts.ignore_public_acls && aws_s3_bucket_public_access_block.artifacts.restrict_public_buckets
    error_message = "S3 public access must be fully blocked"
  }

  assert {
    condition     = aws_s3_bucket.artifacts.force_destroy == false
    error_message = "artifact bucket force_destroy must default to false"
  }

  assert {
    condition     = aws_ecr_repository.serving.image_tag_mutability == "IMMUTABLE" && aws_ecr_repository.serving.image_scanning_configuration[0].scan_on_push
    error_message = "ECR must be IMMUTABLE with scanOnPush"
  }

  assert {
    condition     = aws_cloudwatch_log_group.k3s_pods.retention_in_days == 7 && aws_cloudwatch_log_group.k3s_service.retention_in_days == 7 && aws_cloudwatch_log_group.bootstrap.retention_in_days == 7
    error_message = "all lab log groups must have bounded 7-day retention"
  }
}
