# SharePoint Review Queue — Setup Guide

**Purpose:** when the LCPT scan pipeline cannot process a document (corrupted
PDF, no cover sheet, missing/ambiguous fields), the file and its diagnostics
are routed to a dedicated SharePoint space, and the responsible person gets an
email notification.

**Audience:** Zubair (steps marked YOU), IT / Entra ID admin (steps marked IT).

---

## Architecture at a glance

```
 Pipeline hits a validation failure
        |
        v
 Upload original PDF  ---->  SharePoint document library "Failed Scans"
 + set metadata columns       (Reason, WR Number, Rep, Status, Scan ID)
        |
        v
 Power Automate flow on the library
 "When a file is created -> send email to reviewer(s)"
```

Design choices (and why):

| Choice | Why |
|---|---|
| One **document library** with metadata columns (not a separate list + library) | The file IS the work item. Reviewers open the PDF and see the reason in the same row. Fewer moving parts. |
| **Power Automate** for email (not Graph Mail.Send from our app) | Zero mail permissions for our app (Mail.Send is tenant-wide by default — a big grant). The business team can edit recipients/wording themselves without code changes. |
| **Sites.Selected** Graph permission | Our app can touch ONLY this one site, nothing else in the tenant. Microsoft's recommended least-privilege model for app-only SharePoint access. |

---

## Part 1 — Create the SharePoint space (YOU, ~15 min)

1. Go to `https://<tenant>.sharepoint.com` -> **Create site** -> **Team site**.
   - Name: `LCPT Scan Review`
   - Privacy: **Private** — members only.
   - If site creation is disabled for you, ask IT to create it (2 min for them).
2. In the new site: **Site contents -> New -> Document library**.
   - Name: `Failed Scans`
3. Add these columns to the library (**+ Add column**):

   | Column | Type | Notes |
   |---|---|---|
   | Status | Choice: `New`, `In Progress`, `Resolved` | default `New` |
   | Reason Code | Choice: `NO_COVER_SHEET`, `MISSING_REQUIRED_FIELD`, `INVALID_WR_NUMBER_FORMAT`, `WORK_REQUEST_NOT_FOUND`, `BOTH_ROUTES_CHECKED`, `NEITHER_ROUTE_CHECKED`, `MISSING_CHECKLIST_ITEM`, `OCR_FAILED`, `SINGLE_PAGE_PDF`, `CORRUPTED_PDF`, `UNEXPECTED_ERROR` | |
   | Reason Details | Multiple lines of text | human-readable message |
   | WR Number | Single line of text | what OCR extracted (may be empty) |
   | Rep | Single line of text | from the S3 folder name |
   | Extracted Fields | Multiple lines of text | JSON dump of what OCR read |
   | Scan ID | Single line of text | traces back to logs |
   | Source S3 Key | Single line of text | original bucket location |
   | Reviewer | Person | who is handling it |
4. **Site permissions:** add the reviewer team (Jessica, Rachael, Cindy, ...)
   as **Members** (edit). Nobody else needs access. Do NOT add "Everyone".

## Part 2 — App registration (IT, ~15 min)

> Copy-paste request for IT is at the bottom of this doc.

1. Entra admin center -> **App registrations -> New registration**.
   - Name: `LCPT Scan Automation` ; single tenant; no redirect URI.
2. **API permissions -> Add a permission -> Microsoft Graph ->
   Application permissions -> `Sites.Selected`** -> Add -> **Grant admin
   consent**. (Do NOT use Sites.ReadWrite.All — that is every site in the
   company.)
3. **Certificates & secrets -> New client secret** — 12 months. Record the
   value immediately (shown once). Share with Zubair via **Keeper only** —
   never Slack/email.
4. Grant the app access to ONLY the new site (requires admin; per
   https://learn.microsoft.com/graph/permissions-selected-overview):
   - In Graph Explorer (signed in as admin), run:
     ```
     POST https://graph.microsoft.com/v1.0/sites/{site-id}/permissions
     {
       "roles": ["write"],
       "grantedToIdentities": [{
         "application": {
           "id": "<APP CLIENT ID>",
           "displayName": "LCPT Scan Automation"
         }
       }]
     }
     ```
   - `write` is enough (upload files + set columns). Not `manage`, not
     `fullcontrol`.
   - PowerShell alternative: `Grant-PnPAzureADAppSitePermission -AppId <id>
     -DisplayName "LCPT Scan Automation" -Permissions Write -Site <site-url>`
5. Hand back to Zubair: **Tenant ID**, **Client ID**, **Client secret**
   (Keeper), confirmation that consent + site grant are done.

## Part 3 — Look up the IDs (YOU, ~5 min, after Part 2)

In Graph Explorer (https://developer.microsoft.com/graph/graph-explorer),
signed in with your work account:

1. Site ID:
   `GET https://graph.microsoft.com/v1.0/sites/<tenant>.sharepoint.com:/sites/LCPTScanReview`
   -> copy the full comma-separated `id` value.
2. Drive (library) ID:
   `GET https://graph.microsoft.com/v1.0/sites/{site-id}/drives`
   -> find the drive whose `name` is `Failed Scans`, copy its `id`.

## Part 4 — Configuration (.env)

```bash
# ── SharePoint review queue ──────────────────────────────────────────
REVIEW_QUEUE_BACKEND=sharepoint          # "local" = JSON files (dev default)
GRAPH_TENANT_ID=<from IT>
GRAPH_CLIENT_ID=<from IT>
GRAPH_CLIENT_SECRET=<from Keeper>        # NEVER commit; rotate yearly
SHAREPOINT_SITE_ID=<from Part 3 step 1>  # tenant.sharepoint.com,guid,guid
SHAREPOINT_DRIVE_ID=<from Part 3 step 2>
```

Credential handling rules:
- `.env` is gitignored — local dev only.
- Production (Lambda): secret lives in **AWS Secrets Manager**, injected at
  runtime. The `.env` file never ships anywhere.
- Rotation: IT sets a 12-month expiry; calendar reminder at 11 months.
- The app's blast radius if the secret leaks: write access to ONE SharePoint
  site. Nothing else (no mail, no other sites, no user data).

## Part 5 — Email notification (YOU or a reviewer, ~10 min, no code)

Power Automate flow owned by the team (not by the automation app):

1. https://make.powerautomate.com -> **Create -> Automated cloud flow**.
2. Trigger: **"When a file is created (properties only)"** — point it at the
   `LCPT Scan Review` site, `Failed Scans` library.
3. Action: **"Send an email (V2)"**:
   - To: the review team (or a shared mailbox / Teams channel address)
   - Subject: `Scan needs manual review: @{Reason Code} — @{WR Number}`
   - Body: include Reason Details, Rep, link to the file
     (`Link to item` dynamic content).
4. Optional later: a second flow that escalates items still `New` after N
   hours.

Why this beats sending mail from our code: no Mail.Send permission (which by
default allows an app to send as ANYONE in the tenant), no SMTP credentials
to manage, and the team can change recipients without a deploy.

## Part 6 — What the code does (already designed, built after IT delivers)

A `SharePointReviewQueue` adapter behind the existing `ReviewQueuePort`:
1. acquires an app-only token (client credentials, `https://graph.microsoft.com/.default` scope),
2. uploads the original PDF: `PUT /sites/{site}/drives/{drive}/root:/{filename}:/content`,
3. sets the metadata columns: `PATCH .../items/{item-id}/listItem/fields`.

The pipeline doesn't change — same `review_queue.enqueue(item)` call that
writes local JSON today. Switch backends via `REVIEW_QUEUE_BACKEND`.

---

## Copy-paste request for IT

> Hi — I need an Entra ID app registration for the LCPT scan automation.
> It uploads failed-scan PDFs to one dedicated SharePoint site for manual
> review. Least-privilege setup:
>
> 1. App registration named "LCPT Scan Automation" (single tenant).
> 2. Microsoft Graph **application** permission: **Sites.Selected** only,
>    with admin consent. (Deliberately NOT Sites.ReadWrite.All.)
> 3. Client secret, 12-month expiry, shared via Keeper.
> 4. Site-level grant: role **write** on the site
>    `https://<tenant>.sharepoint.com/sites/LCPTScanReview` for this app,
>    via POST /sites/{site-id}/permissions (or
>    Grant-PnPAzureADAppSitePermission). Docs:
>    https://learn.microsoft.com/graph/permissions-selected-overview
> 5. Send me the Tenant ID + Client ID along with the secret.
>
> The app needs no mail permissions — notifications are a Power Automate
> flow owned by the review team.
