# Invoice Bot

This project polls a mailbox, extracts invoice-related documents, saves them into dated output folders, and optionally submits PDFs to PrintNode for automatic printing.

The production deployment currently runs on Outlook / Microsoft Graph and watches the mailbox `info@ebnertransport.com`.

## High-Level Flow

1. Fetch recent Inbox messages from Microsoft Graph or IMAP.
2. Skip messages already present in local processed state.
3. Save PDF attachments to the daily output folder.
4. Convert image attachments (`jpg`, `jpeg`, `png`) to PDF before saving.
5. Scan matching links from the mail body.
6. Open matching links with Playwright and download linked PDFs when the CMR rule matches.
7. Skip saving duplicate items when either the normalized subject or normalized filename was already processed in the last 7 days.
8. Optionally submit resulting PDFs to PrintNode.
9. Track pending print jobs until they move to success or error buckets.
10. Mark the message as read in Graph only when the bot actually extracted at least one printable file from that email.

## Providers

`MAIL_PROVIDER` controls mailbox access:

- `graph`: Microsoft Graph / Outlook production path
- `imap`: IMAP fallback / prototype path

Current production usage is `graph`.

## Directory Layout

- `email_invoice_bot/`: main application code
- `tests/`: unit tests
- `deploy/`: systemd unit/timer files
- `state/processed_state.json`: dedup state for already processed messages
- `state/duplicate_history.json`: 7-day duplicate history for subject/filename suppression
- `state/pending_print_jobs.json`: pending PrintNode jobs waiting for reconciliation
- `output/Rechnungen/YYYY-MM-DD/`: saved and downloaded PDFs
- `output/Rechnungen/YYYY-MM-DD/druck_ausstehend/`: submitted to printer, not yet confirmed done
- `output/Rechnungen/YYYY-MM-DD/druck_erfolg/`: PrintNode reported `done`
- `output/Rechnungen/YYYY-MM-DD/druck_fehler/`: PrintNode reported `error` or submission failed

## Configuration

Copy `.env.example` to `.env` and adjust values.

Important variables:

### Mail / Graph

- `MAIL_PROVIDER`: `graph` or `imap`
- `GRAPH_TENANT_ID`
- `GRAPH_CLIENT_ID`
- `GRAPH_CLIENT_SECRET`
- `GRAPH_MAILBOX`
- `GRAPH_MESSAGE_LOOKBACK_HOURS`: fetch window for Graph polling
- `GRAPH_MARK_READ`: if `true`, PATCH `isRead=true` after successful processing

### IMAP Fallback

- `IMAP_HOST`
- `IMAP_PORT`
- `IMAP_USER`
- `IMAP_APP_PASSWORD`
- `IMAP_MAILBOX`

### Runtime

- `POLL_INTERVAL_SECONDS`
- `MAX_EMAILS_PER_CYCLE`
- `OUTPUT_ROOT`
- `LINK_SUBSTRING`: only links containing this substring are considered
- `LINK_DOMAIN_ALLOWLIST`: optional domain allowlist for web downloads
- `CMR_KEYWORD`: if any scanned candidate contains this keyword, all candidate PDFs are downloaded
- `PLAYWRIGHT_HEADFUL`: `false` means headless browser mode
- `DRY_RUN`: disables web download side effects when `true`

### Printing

- `PRINT_ENABLED`
- `PRINTNODE_API_KEY`
- `PRINTNODE_PRINTER_ID`
- `PRINT_NOT_BEFORE_UTC`: optional cutoff for printing only newer messages

### Retention

- `RETENTION_DELETE_AFTER_DAYS`: delete dated output folders older than this many days, but never while pending print jobs still exist for that day

### Logging

- `LOG_LEVEL`

## Running Locally

Install dependencies:

```bash
pip install -r requirements.txt
playwright install chromium
```

Run manually:

```bash
PYTHONPATH=. python -m email_invoice_bot.main
```

For safer verification, start with:

```bash
DRY_RUN=true
PRINT_ENABLED=false
```

## Systemd Service

Production service file:

- `deploy/prototype-invoice-bot.service`

The service runs with:

- working directory: `/root/prototype`
- environment file: `/root/prototype/.env`
- command: `/usr/bin/python3 -m email_invoice_bot.main`

Useful commands:

```bash
systemctl status prototype-invoice-bot.service
systemctl restart prototype-invoice-bot.service
journalctl -u prototype-invoice-bot.service -f
journalctl -u prototype-invoice-bot.service --since "2026-04-18 00:00:00" --no-pager
```

## Processing Semantics

### Dedup

A message is considered already processed when its composite state key exists in `state/processed_state.json`.

The key is built from:

- Graph message id / IMAP UID
- `internetMessageId` / `Message-ID` when available

Printable duplicate suppression is tracked separately in `state/duplicate_history.json`.

Current duplicate rules:

- if the normalized email subject matches a processed record from the last 7 days, the email is skipped entirely
- otherwise, individual printable files are skipped when the normalized output filename matches a processed record from the last 7 days
- duplicate items are skipped before saving, so they do not overwrite existing files

### Mark As Read

When `GRAPH_MARK_READ=true`, the bot calls:

- `PATCH /users/{mailbox}/messages/{id}`
- body: `{ "isRead": true }`

This requires Microsoft Graph `Mail.ReadWrite` on the app registration.

Current behavior is intentionally narrow:

- mark as read only when at least one printable file was extracted from the email
- leave the email unread when nothing printable was extracted or when the email was skipped as a duplicate

### Printing

If printing is enabled:

1. PDFs are submitted to PrintNode.
2. Files move into `druck_ausstehend` immediately.
3. Pending jobs are tracked in `state/pending_print_jobs.json`.
4. On later reconciliation:
   - `done` -> move file to `druck_erfolg`
   - `error` -> move file to `druck_fehler`

## Important Operational Notes

### Inbox Scope

Graph production fetches from the Inbox folder, not the whole mailbox:

- `/users/{mailbox}/mailFolders/inbox/messages`

This is intentional to avoid processing other folders such as Sent Items.

### Durability Fix

The bot now flushes local state immediately when:

- a message is finalized into processed state
- duplicate history is updated
- a pending print job is added
- a pending print job is removed

This reduces duplicate processing after crashes or restarts.

### Performance

Graph can be slow in production. Long cycle times are usually caused by:

- token acquisition latency
- Inbox list latency
- attachment fetch latency

This can make cycles take several minutes even without failures.

## Testing

Useful targeted tests:

```bash
python3 -m pytest -q tests/test_print_reconcile.py tests/test_state_store.py
python3 -m pytest -q
```

## Troubleshooting

### Messages processed but still unread

Check:

- service logs for `Marked message read` or `Mark read failed`
- whether the Graph app really has `Mail.ReadWrite`
- whether the email actually produced at least one printable file
- whether unread Inbox messages also exist in `state/processed_state.json`

### Files skipped as duplicates

Check:

- `state/duplicate_history.json`
- service logs for `Skipping duplicate email subject`, `Skipping duplicate attachment`, or `Skipping duplicate download`
- whether a generic subject or filename is matching a prior processed record within the last 7 days

### `druck_ausstehend` not clearing

Check:

- `state/pending_print_jobs.json`
- PrintNode job state lookup
- whether the service was interrupted mid-cycle previously

### Duplicate processing

Check:

- service restarts or OOM kills in `journalctl`
- whether processed state was written before interruption
- whether the same message id appears in multiple print submissions

### Graph GET failures

Typical causes observed so far:

- read timeouts
- very large or slow attachment responses
- malformed Graph payloads during attachment fetch

## Files Worth Knowing

- `email_invoice_bot/main.py`: main cycle, print reconciliation, state finalization
- `email_invoice_bot/graph_client.py`: Graph fetch, attachments, mark-as-read
- `email_invoice_bot/duplicate_store.py`: 7-day duplicate subject/filename history
- `email_invoice_bot/storage.py`: dated output layout
- `email_invoice_bot/print_job_store.py`: pending print job persistence
- `email_invoice_bot/state_store.py`: processed message persistence

## Caution

This repository is used on production infrastructure.

Before changing behavior, especially around:

- mailbox scope
- print submission
- file moves
- mark-as-read behavior
- state persistence

validate changes on a dry run or with explicit approval.
