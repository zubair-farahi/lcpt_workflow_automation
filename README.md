# LCPT Scan Automation

> Automates the LCPT scanning workflow (AIP-388). A rep drops a scanned packet
> in S3; the pipeline OCRs the cover sheet, finds the right work request in
> CP Suite, attaches the packet, ticks the right checklist items, and writes
> an audit note — all in a few seconds, no human in the loop.

## What it replaces

```
TODAY (manual, ~10 min per packet)            TOMORROW (automated, ~30 sec)

Rep scans packet                              Rep scans packet
        ↓                                            ↓
Saves to personal SharePoint folder           Lands in S3 bucket
        ↓                                            ↓
Manually renames file                         Pipeline picks it up
        ↓                                            ↓
Manually finds the WR in CP Suite             Reads WR number from cover sheet
        ↓                                            ↓
Manually attaches the PDF                     Looks up WR + tasks in CP Suite
        ↓                                            ↓
Manually ticks checklist items                Attaches packet (internal)
        ↓                                            ↓
Maybe writes a status note                    Marks checklist items complete
                                                     ↓
                                              Posts audit note
```

## Status at a glance

| Layer | Status |
|---|---|
| S3 ingest + watcher | ✅ Working |
| PDF split (cover sheet + packet) | ✅ Working |
| HaulSafe OCR + field extraction | ✅ Working |
| CP Suite — auth, read WR, list tasks, list checklist | ✅ Working |
| CP Suite — PATCH checklist, POST note, POST attachment | ✅ Working (proven on STC-WR-154) |
| SharePoint review queue | ✅ Working |
| AWS Lambda deployment (Docker + Terraform) | ✅ Ready, waiting on deploy access |

## The pipeline

```
S3 (fw-ocr-project)
   │
   ▼
[Watcher / Lambda] ── handles one PDF
   │
   ▼
PDF split  ─►  cover sheet (page 1) + packet (pages 2+)
   │
   ▼
Render cover sheet → PNG  (HaulSafe rejects PDFs)
   │
   ▼
HaulSafe OCR  ── extract WR number, checkbox states, "Completed By"
   │
   ▼
Validate    ─►  if anything's off, route to review queue
   │
   ▼
CP Suite
  • GET work request (by displayId)
  • GET tasks under the WR
  • POST packet as internal attachment
  • PATCH the right checklist items complete
  • POST audit note ("processed by automation at <time>")
   │
   ▼
Write S3 state marker  (idempotency — same scan won't re-run)
```

## Run it locally

### 1. One-time setup

```bash
git clone git@github.com:zubair-farahi/lcpt_workflow_automation.git
cd lcpt_workflow_automation

# Python 3.11+
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -e .[dev,aws]

# Copy and fill in .env (secrets come from Keeper — never commit them)
cp .env.example .env
# Edit .env: HAUL_OCR_API_KEY, CP_SUITE_USERNAME/PASSWORD/CLIENT_SECRET,
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
```

### 2. Sanity-check

```bash
# Run unit tests (no network, ~2 sec)
python -m pytest tests/unit -q

# Confirm S3 access works
python -m lcpt_scan_automation.entrypoints.local_cli check-s3-access

# Confirm CP Suite credentials work
python -m lcpt_scan_automation.entrypoints.local_cli cp-suite-token
```

### 3. Run the pipeline on a test scan

```bash
# Generate a synthetic cover-sheet PDF and upload to S3
python scripts/make_e2e_test_scan.py
# → prints something like: s3://fw-ocr-project/test-uploads/e2e-demo-abc123.pdf

# Full end-to-end, including CP Suite writes against STAGING
python -m lcpt_scan_automation.entrypoints.local_cli process-s3 \
   --key test-uploads/e2e-demo-abc123.pdf
```

Open `https://cp3-staging.itscomply.com/work-requests/STC-WR-154` in your
browser after the run — you'll see a new attachment, ticked checklist items,
and an audit note.

### 4. Watch S3 like the deployed Lambda would

```bash
# One pass — process any new PDFs and exit
python scripts/watch_s3_for_scans.py --once

# Loop forever, polling every 60 seconds (Ctrl+C to stop)
python scripts/watch_s3_for_scans.py

# Limit to specific reps from the whitelist
python scripts/watch_s3_for_scans.py --once --include cpegg lmcnair
```

### 5. (Optional) Run inside a Docker container

```bash
docker build --platform linux/amd64 -t lcpt-scan-automation:latest .

# Terminal 1 — run the Lambda emulator
docker run --rm -p 9000:8080 --env-file .env lcpt-scan-automation:latest

# Terminal 2 — fire an S3 event at it
curl -s "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{
  "Records": [{
    "eventSource": "aws:s3",
    "s3": {
      "bucket": {"name": "fw-ocr-project"},
      "object": {"key": "test-uploads/e2e-demo-abc123.pdf", "eTag": "local-test"}
    }
  }]
}'
```

## Project structure

```
src/lcpt_scan_automation/
├── application/      # use cases (process_scan, ocr callback, checklist mapper)
├── domain/           # models, enums, errors, validation
├── ports/            # interfaces (CpSuiteClient, OcrClient, Storage, ReviewQueue)
├── infrastructure/   # real adapters (S3, HaulSafe, CP Suite HTTP, pypdf)
├── entrypoints/      # how the world talks to us (CLI, Lambda, webhook)
└── config/           # Settings (env vars) + YAML mappings

scripts/              # ops scripts (S3 watcher, test scan generator)
infra/                # Terraform (ECR, Lambda, IAM, CloudWatch, Secrets Manager)
docs/                 # deployment guide, SharePoint setup guide
tests/                # unit + integration tests
```

Architecture: **ports & adapters**. Production runtime uses real S3, HaulSafe
OCR, CP Suite, and SharePoint adapters; tests keep their own fakes outside `src/`.

## Common commands

| Goal | Command |
|---|---|
| List S3 scans | `lcpt-scan list-s3 --prefix UploadedFromSharedcifs/cpegg/` |
| Fetch one WR from CP Suite | `lcpt-scan cp-suite-get-wr --wr-number STC-WR-154 --show-tasks` |
| Submit a doc to HaulSafe OCR + poll | `lcpt-scan test-ocr --document-url https://...` |
| Get a CP Suite bearer token | `lcpt-scan cp-suite-token` |
| Run S3 watcher once | `python scripts/watch_s3_for_scans.py --once` |
| Unit tests | `python -m pytest tests/unit -q` |

(`lcpt-scan` is the entry point installed by `pip install -e .` — equivalent
to `python -m lcpt_scan_automation.entrypoints.local_cli`.)

## Deeper docs

- **`docs/aws-deployment-guide.md`** — build the image, push to ECR, deploy
  with Terraform, test the live Lambda. Includes the AWS profile validation
  steps (run `aws sts get-caller-identity` before applying!).
- **`docs/sharepoint-review-queue-setup.md`** — how the SharePoint "Failed
  Scans" library is set up and what permissions IT needs to grant.

## What's still open

1. **David Lorge mapping table** — cover-sheet checkboxes ↔ CP Suite checklist
   item names. Currently a temporary STC entry in `config/checklist_mapping.yaml`.
2. **AWS deploy access** — Matt to confirm `fw-ocr-project` account ownership
   and grant deploy rights.
3. **SharePoint site grant** — IT ticket open; one Graph API call from them
   unblocks the SharePoint adapter build.
4. **System-tab GUID for audit notes** — currently notes land in the Public
   tab; we want the System tab. Need GUID from James.

## License / ownership

Internal Fleetworthy / Bestpass tooling. Not open source.
