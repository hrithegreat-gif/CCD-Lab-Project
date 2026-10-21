################ S3 artifacts: deploy/ bundles + results/ ################

resource "aws_s3_bucket" "artifacts" {
  bucket        = local.bucket_name
  force_destroy = var.artifact_bucket_force_destroy # default false: destroy requires an empty bucket

  tags = { Name = local.bucket_name }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ACLs disabled; buckets are IAM-only. No bucket policy needed (no cross-account).
resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-deploy-bundles"
    status = "Enabled"

    filter {
      prefix = "deploy/"
    }

    expiration {
      days = var.artifact_retention_days
    }
  }

  rule {
    id     = "expire-results"
    status = "Enabled"

    filter {
      prefix = "results/"
    }

    expiration {
      days = var.artifact_retention_days
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {
      prefix = ""
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

################ ECR: immutable commit-tagged serving image ################

resource "aws_ecr_repository" "serving" {
  name                 = local.ecr_name
  image_tag_mutability = "IMMUTABLE"

  # Basic scanning on push (no extra charge; enhanced scanning would add Inspector costs).
  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = { Name = local.ecr_name }
}

# Keep a modest number of images: expire everything beyond the 10 most recent.
resource "aws_ecr_lifecycle_policy" "serving" {
  repository = aws_ecr_repository.serving.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire images beyond the 10 most recent"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
