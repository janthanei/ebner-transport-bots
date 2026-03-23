from __future__ import annotations

import logging
import signal
import time
from dataclasses import asdict, dataclass

from .attachment_processor import AttachmentProcessor
from .config import AppConfig
from .email_parser import ParsedEmail, parse_email
from .link_extractor import filter_target_links
from .storage import DailyPdfStorage
from .web_downloader import WebDownloader


LOGGER = logging.getLogger(__name__)
SHUTDOWN = False


@dataclass
class ProcessSummary:
    processed: int = 0
    saved_attachments: int = 0
    downloaded_from_web: int = 0


def _handle_signal(signum, _frame) -> None:
    global SHUTDOWN
    LOGGER.info("Received signal=%s, shutting down after current cycle", signum)
    SHUTDOWN = True


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _fetch_emails_graph(config: AppConfig) -> list[ParsedEmail]:
    from .graph_client import GraphClient

    client = GraphClient(
        tenant_id=config.graph_tenant_id,
        client_id=config.graph_client_id,
        client_secret=config.graph_client_secret,
        mailbox=config.graph_mailbox,
    )
    return client.fetch_recent_messages(config.max_emails_per_cycle)


def _fetch_emails_imap(config: AppConfig) -> list[ParsedEmail]:
    from .imap_client import ImapClient

    client = ImapClient(
        host=config.imap_host,
        port=config.imap_port,
        user=config.imap_user,
        app_password=config.imap_app_password,
        mailbox=config.imap_mailbox,
    )
    emails: list[ParsedEmail] = []
    for uid, raw in client.fetch_recent_messages(config.max_emails_per_cycle):
        emails.append(parse_email(uid, raw))
    return emails


def process_cycle(config: AppConfig) -> ProcessSummary:
    summary = ProcessSummary()

    if config.mail_provider == "graph":
        emails = _fetch_emails_graph(config)
    else:
        emails = _fetch_emails_imap(config)

    storage = DailyPdfStorage(config.output_root)
    attachment_processor = AttachmentProcessor(storage)
    web_downloader = WebDownloader(
        cmr_keyword=config.cmr_keyword,
        headless=config.playwright_headless,
        dry_run=config.dry_run,
        allowlist=config.link_domain_allowlist,
    )

    for email_obj in emails:
        saved_paths = attachment_processor.process(email_obj)
        summary.saved_attachments += len(saved_paths)

        target_links = filter_target_links(email_obj.links, config.link_substring)
        for url in target_links:
            day_dir = storage.get_day_dir(email_obj.received_at)
            result = web_downloader.scan_and_download(url=url, output_dir=day_dir)
            summary.downloaded_from_web += len(result.downloaded_paths)
            LOGGER.info(
                "Link processed uid=%s url=%s scanned=%s cmr_found=%s downloaded=%s",
                email_obj.uid,
                url,
                result.scanned_candidates,
                result.cmr_found,
                len(result.downloaded_paths),
            )

        summary.processed += 1

    return summary


def run() -> None:
    config = AppConfig.from_env()
    setup_logging(config.log_level)
    LOGGER.info("Starting with provider=%s", config.mail_provider)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    backoff_seconds = 5
    while not SHUTDOWN:
        try:
            summary = process_cycle(config)
            LOGGER.info("Cycle complete stats=%s", asdict(summary))
            backoff_seconds = 5
            if SHUTDOWN:
                break
            time.sleep(config.poll_interval_seconds)
        except Exception as exc:
            LOGGER.exception("Cycle failed error=%s", exc)
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 60)

    LOGGER.info("Stopped.")


if __name__ == "__main__":
    run()
