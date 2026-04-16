# Invoice Bot - Current Status

## Current implementation

- Mail ingestion supports both providers via `MAIL_PROVIDER`:
  - `graph` (Microsoft Graph / Outlook production path)
  - `imap` (Gmail prototype fallback)
- Current `.env` is configured for Graph API with:
  - tenant id, client id, client secret
  - mailbox: `info@ebnertransport.com`
- Processing pipeline implemented:
  - fetch unread emails
  - extract attachments + links
  - save PDF attachments
  - convert JPG/PNG attachments to PDF
  - open matching links with Playwright
  - if any candidate filename contains `CMR`, download all candidate files
  - convert downloaded JPG/PNG to PDF
- Storage behavior:
  - folder structure by day
  - overwrite files with same filename (no dedup state logic)
  - current storage path layout:
    - `output/Rechnungen/YYYY-MM-DD/...`

## Current infrastructure status

- SMB share is active and reachable:
  - share path: `\\45.154.207.113\EbnerTransport`
  - invoice subfolder for user: `\\45.154.207.113\EbnerTransport\Rechnungen`
  - credentials:
    - user: `ebner`
    - password: `EbnerTransport2026!`
- Samba service:
  - installed, enabled, running
  - listening on port `445`
- Disk space after cleanup:
  - approximately `3.2 GB` free on this server

## Test status

- Unit tests: passing (`7 passed`)
- SMB connectivity test: passing (share + files visible via authenticated access)
- Smoke processing run has been executed previously and Graph mailbox access is confirmed

## Next planned steps

1. Activation and monitoring
   - Start bot runtime at agreed production time
   - Monitor first live cycles and confirm files arrive in `Rechnungen`

2. Stabilize background operation
   - Run as a persistent service (systemd) with auto-restart
   - Add simple operational checks/log review cadence

3. Printing phase (planned)
   - Preferred low-friction path: PrintNode
   - IT one-time action:
     - install PrintNode client on the Windows VM
   - Developer action:
     - use PrintNode API key + printer id
     - optional mode:
       - auto-print every new PDF, or
       - print only files placed in a dedicated `to-print` folder

4. Capacity / platform
   - This server works for current phase
   - Medium-term recommendation remains migration to the larger 40 GB server

## Open decisions

- Final activation time reference (UTC vs local timezone)
- Printing trigger policy:
  - immediate auto-print
  - or controlled print queue folder
