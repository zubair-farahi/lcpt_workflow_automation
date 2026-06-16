# ── Optional event-driven trigger: S3 -> SQS -> Lambda ──────────────────
# Disabled by default (var.enable_s3_trigger). Start with manual invokes;
# flip this on once the bucket owner approves the notification config.

resource "aws_sqs_queue" "dlq" {
  count                     = var.enable_s3_trigger ? 1 : 0
  name                      = "${var.project_name}-dlq"
  message_retention_seconds = 1209600 # 14 days to investigate poison scans
}

resource "aws_sqs_queue" "scans" {
  count = var.enable_s3_trigger ? 1 : 0
  name  = "${var.project_name}-scans"

  # Must exceed the Lambda timeout so a message isn't redelivered mid-run.
  visibility_timeout_seconds = var.lambda_timeout_seconds + 60

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[0].arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_policy" "allow_s3" {
  count     = var.enable_s3_trigger ? 1 : 0
  queue_url = aws_sqs_queue.scans[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.scans[0].arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = local.scan_bucket_arn }
      }
    }]
  })
}

# WARNING: this resource REPLACES the bucket's whole notification config.
# If the bucket owner has other notifications, they must be merged here
# or this will silently remove theirs. Coordinate before enabling.
resource "aws_s3_bucket_notification" "scans" {
  count  = var.enable_s3_trigger ? 1 : 0
  bucket = var.scan_bucket_name

  dynamic "queue" {
    for_each = var.input_prefixes
    content {
      queue_arn     = aws_sqs_queue.scans[0].arn
      events        = ["s3:ObjectCreated:*"]
      filter_prefix = queue.value
      filter_suffix = ".pdf"
    }
  }

  depends_on = [aws_sqs_queue_policy.allow_s3]
}

resource "aws_lambda_event_source_mapping" "sqs" {
  count            = var.enable_s3_trigger ? 1 : 0
  event_source_arn = aws_sqs_queue.scans[0].arn
  function_name    = aws_lambda_function.app.arn
  batch_size       = 1 # one scan per invocation: isolates failures, simplifies retries
}

# Lambda needs SQS consume rights only when the trigger exists.
resource "aws_iam_role_policy" "sqs_consume" {
  count = var.enable_s3_trigger ? 1 : 0
  name  = "${var.project_name}-sqs-consume"
  role  = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
      Resource = aws_sqs_queue.scans[0].arn
    }]
  })
}
