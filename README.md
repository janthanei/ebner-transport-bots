# Gmail IMAP Invoice Prototype

## What It Does
- Polls Gmail IMAP inbox.
- Saves PDF attachments into `output/YYYY-MM-DD/`.
- Converts JPG/JPEG/PNG attachments to PDF and saves in same day folder.
- Detects target links in email body and conditionally downloads page PDFs:
  - if any PDF candidate contains `CMR`, download all candidate PDFs
  - otherwise download none

## Quick Start
1. Create a virtual env and install dependencies:
   - `pip install -r requirements.txt`
2. Install Playwright browser:
   - `playwright install chromium`
3. Copy env template:
   - `cp .env.example .env`
4. Export env vars and run:
   - `PYTHONPATH=. python -m email_invoice_bot.main`

Use `DRY_RUN=true` initially to validate behavior without web downloads.

