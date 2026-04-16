from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from dotenv import load_dotenv

from email_invoice_bot.attachment_processor import AttachmentProcessor
from email_invoice_bot.config import AppConfig
from email_invoice_bot.email_parser import ParsedEmail
from email_invoice_bot.graph_client import GraphClient
from email_invoice_bot.link_extractor import filter_target_links
from email_invoice_bot.storage import DailyPdfStorage
from email_invoice_bot.web_downloader import WebDownloader


def main() -> None:
    load_dotenv("/root/prototype/.env")
    config = AppConfig.from_env()
    if config.mail_provider != "graph":
        raise RuntimeError("MAIL_PROVIDER must be graph for replay.")

    client = GraphClient(
        tenant_id=config.graph_tenant_id,
        client_id=config.graph_client_id,
        client_secret=config.graph_client_secret,
        mailbox=config.graph_mailbox,
    )

    # Replay only messages received after service go-live time.
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT10:00:00Z")
    select = "id,subject,from,receivedDateTime,body,hasAttachments,internetMessageId,isRead"
    qs = urlencode(
        {
            "$filter": f"receivedDateTime ge {since}",
            "$orderby": "receivedDateTime asc",
            "$top": "200",
            "$select": select,
        }
    )
    path = f"/users/{quote(config.graph_mailbox)}/mailFolders/inbox/messages?{qs}"
    data = client._api_get(path)
    messages = data.get("value", [])

    storage = DailyPdfStorage(config.output_root)
    attachment_processor = AttachmentProcessor(storage)
    web_downloader = WebDownloader(
        cmr_keyword=config.cmr_keyword,
        headless=config.playwright_headless,
        dry_run=config.dry_run,
        allowlist=config.link_domain_allowlist,
    )

    replayed = 0
    attachments_saved = 0
    web_downloaded = 0

    for msg in messages:
        body_text, links = client._extract_body_and_links(msg)
        target_links = filter_target_links(links, config.link_substring)
        if not target_links:
            continue

        msg_id = msg.get("id", "")
        subject = (msg.get("subject") or "").strip()
        from_obj = (msg.get("from") or {}).get("emailAddress", {})
        sender = from_obj.get("address") or from_obj.get("name") or ""
        received_at = client._parse_datetime(msg.get("receivedDateTime"))
        internet_msg_id = msg.get("internetMessageId")

        attachments = client._fetch_attachments(msg_id) if msg.get("hasAttachments", False) else []
        email_obj = ParsedEmail(
            uid=msg_id,
            message_id=internet_msg_id,
            subject=subject,
            sender=sender,
            received_at=received_at,
            body_text=body_text,
            links=links,
            attachments=attachments,
        )

        saved = attachment_processor.process(email_obj)
        attachments_saved += len(saved)

        for url in target_links:
            day_dir = storage.get_day_dir(email_obj.received_at)
            result = web_downloader.scan_and_download(url=url, output_dir=day_dir)
            web_downloaded += len(result.downloaded_paths)
            print(
                f"replayed subject={subject!r} isRead={msg.get('isRead')} "
                f"scanned={result.scanned_candidates} cmr={result.cmr_found} "
                f"downloaded={len(result.downloaded_paths)}"
            )

        replayed += 1

    print(
        f"replay_complete scanned={len(messages)} replayed={replayed} "
        f"attachments_saved={attachments_saved} web_downloaded={web_downloaded}"
    )


if __name__ == "__main__":
    main()
