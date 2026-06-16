# ── LCPT Scan Automation — AWS Lambda container image ──────────────────
# Build:  docker build --platform linux/amd64 -t lcpt-scan-automation .
# The AWS Lambda Python base image bundles the runtime interface client
# (for Lambda) AND the runtime interface emulator (for local testing).
FROM public.ecr.aws/lambda/python:3.12

# Install dependencies first so Docker layer-caches them between builds.
COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
COPY src/ ${LAMBDA_TASK_ROOT}/src/
RUN pip install --no-cache-dir "${LAMBDA_TASK_ROOT}[aws]"

# Runtime config files (Settings reads them relative to /var/task).
COPY config/ ${LAMBDA_TASK_ROOT}/config/

# No secrets are baked into this image. Runtime configuration comes from
# Lambda environment variables + AWS Secrets Manager (LCPT_SECRETS_ARN).

# Handler: module path + function name.
CMD ["lcpt_scan_automation.entrypoints.lambda_handler.handle_s3_event"]
