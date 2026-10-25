variable "aws_region" {
  description = "AWS region for all resources. Pinned to ap-south-1 (Mumbai) so the budget-first lab stays reproducible."
  type        = string
  default     = "ap-south-1"

  validation {
    condition     = var.aws_region == "ap-south-1"
    error_message = "This lab is budget-scoped to ap-south-1 (Mumbai). Change another region only deliberately."
  }
}

variable "project_prefix" {
  description = "Short prefix used in resource names, tags and the SSM parameter prefix."
  type        = string
  default     = "ccd-lab"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,20}$", var.project_prefix))
    error_message = "project_prefix must be 3-21 chars: lowercase letters, digits, hyphens, starting alphanumeric."
  }
}

variable "ssm_parameter_prefix" {
  description = "SSM Parameter Store path prefix for operator-supplied SecureStrings (e.g. <prefix>/api-key). Terraform never stores these values; parameter reads are denied outside this prefix."
  type        = string
  default     = "/ccd-lab"

  validation {
    condition     = can(regex("^/[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)*$", var.ssm_parameter_prefix))
    error_message = "ssm_parameter_prefix must be an absolute SSM path such as /ccd-lab."
  }
}

variable "instance_type" {
  description = "Single k3s node instance type. m7i-flex.large = 2 vCPU / 8 GiB fixed performance (no burst credits to leak)."
  type        = string
  default     = "m7i-flex.large"
}

variable "root_volume_size" {
  description = "Encrypted gp3 root volume size in GiB."
  type        = number
  default     = 40

  validation {
    condition     = var.root_volume_size >= 20 && var.root_volume_size <= 200
    error_message = "root_volume_size must be between 20 and 200 GiB."
  }
}

variable "autostop_hours" {
  description = "Hours after boot before the node powers off (systemd timer -> systemctl poweroff; EC2 stops the instance, EBS is retained)."
  type        = number
  default     = 4

  validation {
    condition     = var.autostop_hours >= 1 && var.autostop_hours <= 24 && floor(var.autostop_hours) == var.autostop_hours
    error_message = "autostop_hours must be a whole number between 1 and 24."
  }
}

variable "k3s_version" {
  description = "Pinned k3s release installed at boot (validated GitHub release published 2026-09-30, metrics-server v0.9.0 embedded)."
  type        = string
  default     = "v1.37.1+k3s1"

  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+\\+k3s[0-9]+$", var.k3s_version))
    error_message = "k3s_version must look like v1.37.1+k3s1."
  }
}

variable "artifact_retention_days" {
  description = "S3 lifecycle expiry (days) for deploy/ bundles and results/ artifacts."
  type        = number
  default     = 30

  validation {
    condition     = contains([14, 30], var.artifact_retention_days)
    error_message = "artifact_retention_days must be 14 or 30."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention (days) applied to every log group this stack creates."
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 5, 7, 14], var.log_retention_days)
    error_message = "log_retention_days must be one of 1, 3, 5, 7, 14."
  }
}

variable "artifact_bucket_force_destroy" {
  description = "Opt-in: let terraform destroy delete a NON-EMPTY artifacts bucket. Default false: destroy fails until the bucket is emptied (retained objects are otherwise permanent)."
  type        = bool
  default     = false
}

variable "budget_limit" {
  description = "Optional monthly AWS Budgets limit in USD. Alerts only: delayed notifications, NOT a hard spending cap. Set null to skip the budget entirely."
  type        = number
  default     = 100

  validation {
    condition     = var.budget_limit == null || (var.budget_limit > 0 && var.budget_limit <= 10000)
    error_message = "budget_limit must be null or a positive number <= 10000."
  }
}

variable "budget_email" {
  description = "Optional email subscriber for budget alerts. When null (and budget_limit is set) the budget is created without alert subscribers."
  type        = string
  default     = null

  validation {
    condition     = var.budget_email == null || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_email))
    error_message = "budget_email must be null or a valid email address."
  }
}

variable "create_oidc_provider" {
  description = "Opt-in: create the IAM OIDC provider for token.actions.githubusercontent.com. Keep false when the account already has one (OIDC providers are account-global; creating a duplicate collides)."
  type        = bool
  default     = false
}

variable "github_oidc_provider_arn" {
  description = "ARN of an EXISTING IAM OIDC provider for token.actions.githubusercontent.com. Required when create_oidc_provider = false."
  type        = string
  default     = null
}

variable "github_repository" {
  description = "GitHub repository trusted to assume the deploy role. Set together with github_owner_id and github_repository_id when using a different repository."
  type        = string
  default     = "hrithegreat-gif/CCD-Lab-Project"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/repo."
  }
}

variable "github_owner_id" {
  description = "Immutable GitHub owner ID used in the OIDC subject claim."
  type        = string
  default     = "264411662"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "github_owner_id must contain only digits."
  }
}

variable "github_repository_id" {
  description = "Immutable GitHub repository ID used in the OIDC subject claim."
  type        = string
  default     = "1416389614"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "github_repository_id must contain only digits."
  }
}
