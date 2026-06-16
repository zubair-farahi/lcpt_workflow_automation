# AWS Deployment Guide — LCPT Scan Automation

Architecture: **S3 (existing bucket) -> [optional SQS] -> Lambda (container
image from ECR)**, provisioned with Terraform. No secrets in code, image,
or Terraform state — runtime secrets live in AWS Secrets Manager.

```
 scanner upload                         manual test
      |                                      |
      v                                      v
 s3://fw-ocr-project ──(event)── SQS ── Lambda <── aws lambda invoke
                                          |
                          IAM role (no access keys anywhere)
                          Secrets Manager (OCR + CP Suite creds)
                          CloudWatch Logs (structured JSON)
```

---

## 0. Prerequisites

| What | Why |
|---|---|
| AWS account + admin-ish credentials for the person running Terraform | Creates ECR/Lambda/IAM/SQS/Secrets. Confirm with Matt which account owns `fw-ocr-project` — the Lambda should live in the same account, otherwise the S3 trigger needs cross-account setup. |
| Docker Desktop | Build the image |
| Terraform >= 1.6 | `winget install Hashicorp.Terraform` |
| AWS CLI v2, `aws configure`d | Push image, set secret, invoke |

The deploy user needs rights to create: ECR repos, Lambda functions, IAM
roles/policies, SQS queues, Secrets Manager secrets, CloudWatch log groups.

## 0b. AWS profile and account validation

You may have more than one AWS identity configured locally — for example a
**deployment profile** (with broad rights to create Lambda/ECR/IAM/etc.) and
a **scoped S3 profile** (`fw-ocr-s3`) that can only read/write the scan
bucket. They live in different AWS accounts and they are NOT interchangeable.

Confirm which identity Terraform will use BEFORE running `terraform apply`:

```bash
# 1. What identity does the default credential chain resolve to?
#    This is what Terraform uses when aws_profile = null.
aws sts get-caller-identity

# 2. Sanity-check the scoped S3 profile (different account, by design).
aws sts get-caller-identity --profile fw-ocr-s3

# 3. Confirm the scoped profile actually has bucket access.
aws s3 ls s3://fw-ocr-project --profile fw-ocr-s3

# 4. Init Terraform and look at the plan WITHOUT applying.
cd infra
terraform init
terraform plan
```

What to look for in the output:

- `aws sts get-caller-identity` should print the **deployment** account
  (e.g. `arn:aws:iam::284748446641:user/zfarahi`), **not** the S3 account
  (`arn:aws:iam::715841350674:user/s3-ocr`). If it prints the wrong one,
  either set `AWS_PROFILE=<deploy-profile>` for your shell, or set
  `aws_profile = "<deploy-profile>"` in `terraform.tfvars`.
- `aws s3 ls s3://fw-ocr-project --profile fw-ocr-s3` should list objects.
  That confirms the scoped profile works for bucket access only — it does
  not imply that profile can deploy.
- `terraform plan` should propose creating ECR/Lambda/IAM/CloudWatch/
  Secrets Manager resources. If `plan` fails with `AccessDenied` on
  anything that isn't S3, you're running with the wrong profile — stop
  and fix it before applying.

### Warning — do NOT use the scoped S3 profile as the Terraform profile

The `fw-ocr-s3` profile (account `715841350674`) is a **read/write scoped**
identity for the scan bucket only. It does NOT have rights to create:
Lambda functions, ECR repositories, IAM roles, CloudWatch log groups,
Secrets Manager secrets, or SQS queues. Running Terraform with it will
fail partway through `terraform apply` and leave a half-created stack
that takes manual cleanup to recover from.

If you ever see `aws_profile = "fw-ocr-s3"` in `terraform.tfvars`, that's
a misconfiguration — that profile is **never** the right answer for the
deployment.

### Warning — S3 trigger must stay disabled

`enable_s3_trigger` must remain `false` until the bucket owner of
`fw-ocr-project` has explicitly approved enabling the S3 -> SQS -> Lambda
notification. Terraform's `aws_s3_bucket_notification` resource REPLACES
the bucket's entire existing notification configuration — if the owner
has other consumers wired up, flipping this on without coordination will
silently disconnect them.

## 1. Build the Docker image locally

```bash
cd /c/Users/zubair.farahi/Downloads/DEV/lcpt_workflow_automation
docker build --platform linux/amd64 -t lcpt-scan-automation:latest .
```

`--platform linux/amd64` matters: Lambda runs x86_64.

## 2. Test the container LOCALLY against a specific S3 file

The Lambda base image ships a local emulator — run the container, then POST
an S3 event to it. This is the closest-to-production local test.

```bash
# Terminal 1 — run the container with your AWS creds + config from .env
docker run --rm -p 9000:8080 \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION=us-east-1 \
  --env-file <(grep -v '^#' .env | grep -v '^$') \
  lcpt-scan-automation:latest
```

```bash
# Terminal 2 — upload a fresh test scan, then fire the event at the container
python scripts/make_e2e_test_scan.py     # prints the new S3 key

curl -s "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{
  "Records": [{
    "eventSource": "aws:s3",
    "s3": {
      "bucket": {"name": "fw-ocr-project"},
      "object": {"key": "test-uploads/<PASTE-KEY-HERE>.pdf", "eTag": "local-test"}
    }
  }]
}'
```

Expected: the container logs show the familiar pipeline stages
(`pdf_split` -> `ocr_submitted` -> `ocr_poll` -> `cp_*` -> `scan_success`)
and the curl returns `{"statusCode": 200, "body": "...SUCCESS..."}`.
Failed-validation scans appear in `s3://fw-ocr-project/review_queue/`.

Notes:
- Local container runs use YOUR AWS keys from the environment; the deployed
  Lambda uses its IAM role instead (no keys anywhere).
- `--mock` anything is gone: this is the full Mode 4 path. It will write to
  CP Suite STAGING per the env config — only feed it cover sheets pointing
  at safe WRs (the e2e test scan targets STC-WR-154).

## 3. Provision the infrastructure (Terraform)

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # adjust if needed
terraform init
terraform plan                                  # review what will be created
terraform apply
```

First apply creates: ECR repo, IAM role + least-privilege policy, Lambda
(it will show as failed-to-create image pull until step 4 pushes one — if
so, just re-apply after pushing), Secrets Manager secret (EMPTY), log group.
`enable_s3_trigger=false` means NO SQS and NO bucket notification yet.

### 3b. Set the secret value (one-time, never in code)

```bash
aws secretsmanager put-secret-value \
  --secret-id "$(terraform output -raw secrets_arn)" \
  --secret-string '{
    "HAUL_OCR_API_KEY": "<from Keeper>",
    "CP_SUITE_USERNAME": "<staging user>",
    "CP_SUITE_PASSWORD": "<from Keeper>",
    "CP_SUITE_CLIENT_SECRET": "<from Keeper>"
  }'
```

The Lambda reads these at cold start via `LCPT_SECRETS_ARN` (already set by
Terraform). Add `GRAPH_CLIENT_SECRET` etc. to the same JSON when SharePoint
lands. Rotation = put a new value; next cold start picks it up.

## 4. Push the image to ECR

```bash
cd infra && REPO=$(terraform output -raw ecr_repository_url) && cd ..

aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin "${REPO%%/*}"

docker tag lcpt-scan-automation:latest "$REPO:latest"
docker push "$REPO:latest"

# Point the function at the new image (needed on every new push of :latest)
aws lambda update-function-code \
  --function-name lcpt-scan-automation \
  --image-uri "$REPO:latest"
```

For real releases, tag immutably (`:v1.0.0`, or the git SHA) and set
`image_tag` in terraform.tfvars instead of reusing `latest`.

## 5. Test the DEPLOYED Lambda against a specific S3 file

```bash
python scripts/make_e2e_test_scan.py     # upload a fresh test scan

# Edit infra/test-event.json -> put the printed key in "key"
aws lambda invoke \
  --function-name lcpt-scan-automation \
  --payload file://infra/test-event.json \
  --cli-binary-format raw-in-base64-out \
  out.json && cat out.json

# Watch the logs live
aws logs tail /aws/lambda/lcpt-scan-automation --follow
```

Verify exactly like the local demo: SUCCESS in `out.json`, pipeline stages
in the logs, attachment/checkmark/note on the WR in CP Suite staging.

## 6. Turning on automatic processing (later)

1. Get sign-off from the bucket owner (the notification config on
   `fw-ocr-project` is replaced wholesale — see warning in
   `infra/trigger.tf`).
2. Set `enable_s3_trigger = true` in terraform.tfvars, `terraform apply`.
3. From then on: scanner upload -> SQS -> Lambda automatically. Failures
   retry 3x then land in the DLQ (14-day retention).

## Security summary

- **No AWS keys in the runtime** — the Lambda uses its IAM role.
- **IAM is prefix-scoped**: read scans from `UploadedFromSharedcifs/` +
  `test-uploads/`; read/write only `processing/`, `state/`, `review_queue/`;
  read ONE secret; write its own log group. Nothing else.
- **Secrets**: only in Secrets Manager (+ your local `.env`, gitignored).
  Not in the image, not in Terraform code or state, not in git.
- **ECR scan-on-push** flags CVEs in the image automatically.
- Logs carry scan IDs and reason codes — extracted customer field values
  are not logged.

## Open items / assumptions to confirm

1. Which AWS account owns `fw-ocr-project`, and do we deploy into it? (Matt)
2. Production HaulSafe + CP Suite URLs/credentials when we leave staging
   (today's tfvars point at staging).
3. Bucket-owner approval before `enable_s3_trigger = true`.
