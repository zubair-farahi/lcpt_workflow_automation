variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
  default     = "lcpt-scan-automation"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

# Which named AWS CLI profile Terraform should authenticate as.
#
# null (default)  -> use the standard AWS credential chain (env vars,
#                    AWS_PROFILE, ~/.aws/credentials default, IMDS, etc.)
# "<profile>"     -> force this profile, e.g. "lcpt-deploy".
#
# IMPORTANT — this profile must have permission to create:
#   ECR repos, Lambda functions, IAM roles/policies, CloudWatch log groups,
#   Secrets Manager secrets, and (when enable_s3_trigger=true) SQS queues.
#
# DO NOT use a scoped read-only S3 profile here (e.g. "fw-ocr-s3"). That
# profile is meant only for `aws s3` access to the scan bucket and lacks
# rights to provision the rest of the stack. Running Terraform with it
# will fail partway through and leave a half-created stack.
variable "aws_profile" {
  description = "Optional AWS CLI profile for the deployment. null = default credential chain."
  type        = string
  default     = null
}

variable "scan_bucket_name" {
  description = "EXISTING S3 bucket the scanner uploads to (not created here)"
  type        = string
  default     = "fw-ocr-project"
}

variable "image_tag" {
  description = "Tag of the container image in ECR to deploy"
  type        = string
  default     = "latest"
}

variable "lambda_memory_mb" {
  description = "Rendering 40MB+ scans with PDFium needs headroom"
  type        = number
  default     = 2048
}

variable "lambda_timeout_seconds" {
  description = "Covers OCR polling (up to ~2 min) + CP Suite calls + retries"
  type        = number
  default     = 600
}

variable "input_prefixes" {
  description = "Bucket prefixes the Lambda may READ scans from"
  type        = list(string)
  default     = ["UploadedFromSharedcifs/", "test-uploads/"]
}

variable "work_prefixes" {
  description = "Bucket prefixes the Lambda may READ AND WRITE (pipeline artifacts)"
  type        = list(string)
  default     = ["processing/", "state/", "review_queue/"]
}

variable "enable_s3_trigger" {
  description = <<-DESC
    When true, wires S3 ObjectCreated events -> SQS -> Lambda so scans are
    processed automatically on upload. Default false: start with manual
    invokes. IMPORTANT: aws_s3_bucket_notification REPLACES the bucket's
    entire existing notification config — coordinate with the bucket owner
    before enabling.
  DESC
  type        = bool
  default     = false
}

variable "non_secret_env" {
  description = "Plain (non-secret) environment variables for the Lambda"
  type        = map(string)
  default = {
    HAUL_OCR_BASE_URL        = "https://haul-safe-document-api-staging.haulwith.us"
    CP_SUITE_BASE_URL        = "https://cp3-staging.itscomply.com/task-manager-api"
    CP_SUITE_IDENTITY_SERVER = "https://stg-id.itscomply.com"
    LCPT_SKIP_USERS          = "acetera,bmyers,cwilt,klemke,wmcneese"
    LCPT_INCLUDE_USERS       = "aschuh,alatsch,cpegg,gploog,lmcnair,mwalker,mschnick,nlee,skrull"
  }
}

variable "log_retention_days" {
  type    = number
  default = 30
}
