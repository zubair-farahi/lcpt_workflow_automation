data "aws_caller_identity" "current" {}

locals {
  scan_bucket_arn = "arn:aws:s3:::${var.scan_bucket_name}"
}

# ── ECR ─────────────────────────────────────────────────────────────────
resource "aws_ecr_repository" "app" {
  name                 = var.project_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "keep_recent" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# ── Secrets Manager ─────────────────────────────────────────────────────
# The secret RESOURCE is managed here; the secret VALUE is never in code
# or Terraform state. Set it once via CLI (see deployment guide):
#   aws secretsmanager put-secret-value --secret-id <arn> --secret-string '{...}'
resource "aws_secretsmanager_secret" "runtime" {
  name        = "${var.project_name}/runtime"
  description = "Runtime secrets for LCPT scan automation (OCR key, CP Suite credentials)"
}

# ── CloudWatch Logs ─────────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project_name}"
  retention_in_days = var.log_retention_days
}

# ── IAM (least privilege) ───────────────────────────────────────────────
resource "aws_iam_role" "lambda" {
  name = "${var.project_name}-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  statement {
    sid       = "ReadIncomingScans"
    actions   = ["s3:GetObject"]
    resources = [for p in var.input_prefixes : "${local.scan_bucket_arn}/${p}*"]
  }

  statement {
    sid       = "ReadWritePipelineArtifacts"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = [for p in var.work_prefixes : "${local.scan_bucket_arn}/${p}*"]
  }

  statement {
    sid       = "ListScopedPrefixes"
    actions   = ["s3:ListBucket"]
    resources = [local.scan_bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = concat(var.input_prefixes, [for p in var.work_prefixes : "${p}*"])
    }
  }

  statement {
    sid       = "ReadRuntimeSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.runtime.arn]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${var.project_name}-lambda"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

# ── Lambda (container image) ────────────────────────────────────────────
resource "aws_lambda_function" "app" {
  function_name = var.project_name
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
  architectures = ["x86_64"]

  memory_size = var.lambda_memory_mb
  timeout     = var.lambda_timeout_seconds

  environment {
    variables = merge(var.non_secret_env, {
      LCPT_SCAN_BUCKET = var.scan_bucket_name
      LCPT_SECRETS_ARN = aws_secretsmanager_secret.runtime.arn
    })
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}
