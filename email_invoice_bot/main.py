from __future__ import annotations

import logging
import signal
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_client import GraphClient

from .attachment_processor import AttachmentProcessor
from .config import AppConfig
from .email_parser import ParsedEmail, parse_email
from .link_extractor import filter_target_links
from .print_job_store import PendingPrintJob, PrintJobStore
from .printnode_client import PrintNodeClient
from .retention import purge_old_output
from .state_store import StateStore
from .storage import DailyPdfStorage
from .web_downloader import WebDownloader


LOGGER = logging.getLogger(__name__)
SHUTDOWN = False


@dataclass
class ProcessSummary:
    processed: int = 0
    saved_attachments: int = 0
    downloaded_from_web: int = 0
    printed_jobs: int = 0


def _handle_signal(signum, _frame) -> None:
    global SHUTDOWN
    LOGGER.info("Received signal=%s, shutting down after current cycle", signum)
    SHUTDOWN = True


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


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


def _move_to_print_bucket(file_path: Path, bucket: str) -> Path:
    base_dir = file_path.parent
    if base_dir.name in {"druck_erfolg", "druck_fehler", "druck_ausstehend"}:
        base_dir = base_dir.parent
    target_dir = base_dir / bucket
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / file_path.name
    if target_path.exists():
        target_path.unlink()
    shutil.move(str(file_path), str(target_path))
    return target_path


def _parse_not_before_utc(value: str) -> datetime | None:
    raw = value.strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finalize_processed_email(
    summary: ProcessSummary,
    state_store: StateStore,
    state_key: str,
    graph_client: GraphClient | None,
    graph_mark_read: bool,
    graph_message_id: str,
) -> None:
    summary.processed += 1
    state_store.add(state_key)
    state_store.flush()
    if graph_client is None or not graph_mark_read:
        return
    try:
        graph_client.mark_message_read(graph_message_id)
        LOGGER.info("Marked message read graph_id=%s", graph_message_id)
    except Exception as exc:
        LOGGER.warning("Mark read failed graph_id=%s error=%s", graph_message_id, exc)


def _reconcile_pending_print_jobs(
    print_client: PrintNodeClient,
    job_store: PrintJobStore,
    summary: ProcessSummary,
) -> None:
    for job in job_store.items():
        job_id = job.job_id
        try:
            info = print_client.get_printjob(job_id)
        except Exception as exc:
            LOGGER.warning("Print job lookup failed job_id=%s error=%s", job_id, exc)
            continue

        state = (info.get("state") or "").lower()
        if state in {"queued", "new", "sent"}:
            continue

        file_path = Path(job.file_path)
        if not file_path.exists():
            LOGGER.warning("Pending print file missing job_id=%s file=%s", job_id, job.file_path)
            job_store.remove(job_id)
            job_store.flush()
            continue

        if state == "done":
            moved = _move_to_print_bucket(file_path, "druck_erfolg")
            summary.printed_jobs += 1
            LOGGER.info("Print done job_id=%s moved_to=%s", job_id, moved)
            job_store.remove(job_id)
            job_store.flush()
            continue

        if state == "error":
            moved = _move_to_print_bucket(file_path, "druck_fehler")
            LOGGER.warning("Print error job_id=%s moved_to=%s", job_id, moved)
            job_store.remove(job_id)
            job_store.flush()
            continue

        LOGGER.info("Print job unknown state job_id=%s state=%s", job_id, state)


def process_cycle(config: AppConfig) -> ProcessSummary:
    summary = ProcessSummary()
    state_store = StateStore(Path("state/processed_state.json"))
    state_store.load()
    retention = purge_old_output(
        config.output_root,
        config.retention_delete_after_days,
        pending_jobs_file=Path("state/pending_print_jobs.json"),
    )
    if retention.deleted_days or retention.skipped_pending_days:
        LOGGER.info(
            "Retention complete deleted_days=%s skipped_pending_days=%s",
            retention.deleted_days,
            retention.skipped_pending_days,
        )

    graph_client = None
    if config.mail_provider == "graph":
        from .graph_client import GraphClient

        graph_client = GraphClient(
            tenant_id=config.graph_tenant_id,
            client_id=config.graph_client_id,
            client_secret=config.graph_client_secret,
            mailbox=config.graph_mailbox,
        )
        LOGGER.info("Cycle graph fetch start mailbox=%s lookback_hours=%s max_count=%s", config.graph_mailbox, config.graph_message_lookback_hours, config.max_emails_per_cycle)
        emails = graph_client.fetch_recent_messages(
            config.max_emails_per_cycle,
            config.graph_message_lookback_hours,
        )
        LOGGER.info("Cycle graph fetch done mailbox=%s fetched_emails=%s", config.graph_mailbox, len(emails))
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
    print_client = None
    job_store = None
    if config.print_enabled:
        if not config.printnode_api_key:
            raise RuntimeError("PRINT_ENABLED=true but PRINTNODE_API_KEY is missing")
        if config.printnode_printer_id <= 0:
            raise RuntimeError("PRINT_ENABLED=true but PRINTNODE_PRINTER_ID is invalid")
        print_client = PrintNodeClient(
            api_key=config.printnode_api_key,
            printer_id=config.printnode_printer_id,
        )
        job_store = PrintJobStore(Path("state/pending_print_jobs.json"))
        job_store.load()
        _reconcile_pending_print_jobs(print_client, job_store, summary)
    print_not_before = _parse_not_before_utc(config.print_not_before_utc)

    for email_obj in emails:
        state_key = StateStore.build_key(email_obj.uid, email_obj.message_id)
        if state_store.has(state_key):
            LOGGER.debug("Skipping already processed email uid=%s", email_obj.uid)
            continue

        if graph_client is not None and email_obj.has_attachments and not email_obj.attachments:
            email_obj.attachments = graph_client.fetch_message_attachments(email_obj.uid)

        saved_paths = attachment_processor.process(email_obj)
        summary.saved_attachments += len(saved_paths)
        files_to_print = list(saved_paths)

        target_links = filter_target_links(email_obj.links, config.link_substring)
        for url in target_links:
            day_dir = storage.get_day_dir(email_obj.received_at)
            result = web_downloader.scan_and_download(url=url, output_dir=day_dir)
            summary.downloaded_from_web += len(result.downloaded_paths)
            files_to_print.extend(result.downloaded_paths)
            LOGGER.info(
                "Link processed uid=%s url=%s scanned=%s cmr_found=%s downloaded=%s",
                email_obj.uid,
                url,
                result.scanned_candidates,
                result.cmr_found,
                len(result.downloaded_paths),
            )

        if print_client is not None:
            should_print = True
            if print_not_before is not None:
                received_utc = email_obj.received_at.astimezone(timezone.utc)
                should_print = received_utc >= print_not_before
                if not should_print:
                    LOGGER.info(
                        "Skipping print before cutoff uid=%s received=%s cutoff=%s",
                        email_obj.uid,
                        received_utc.isoformat(),
                        print_not_before.isoformat(),
                    )
            if not should_print:
                _finalize_processed_email(
                    summary,
                    state_store,
                    state_key,
                    graph_client,
                    config.graph_mark_read,
                    email_obj.uid,
                )
                continue
            for file_path in dict.fromkeys(files_to_print):
                try:
                    job_id = print_client.submit_pdf(file_path)
                    moved = _move_to_print_bucket(file_path, "druck_ausstehend")
                    if job_store is not None:
                        job_store.add(
                            PendingPrintJob(
                                job_id=int(job_id),
                                file_path=str(moved),
                                base_dir=str(moved.parent.parent),
                                created_utc=datetime.now(timezone.utc).isoformat(),
                            )
                        )
                        job_store.flush()
                    LOGGER.info(
                        "Print submitted uid=%s file=%s job_id=%s moved_to=%s",
                        email_obj.uid,
                        file_path.name,
                        job_id,
                        moved,
                    )
                except Exception as exc:
                    try:
                        moved = _move_to_print_bucket(file_path, "druck_fehler")
                        LOGGER.exception(
                            "Print submission failed uid=%s file=%s moved_to=%s error=%s",
                            email_obj.uid,
                            file_path.name,
                            moved,
                            exc,
                        )
                    except Exception as move_exc:
                        LOGGER.exception(
                            "Print submission failed uid=%s file=%s and move failed error=%s move_error=%s",
                            email_obj.uid,
                            file_path.name,
                            exc,
                            move_exc,
                        )

        _finalize_processed_email(
            summary,
            state_store,
            state_key,
            graph_client,
            config.graph_mark_read,
            email_obj.uid,
        )

    if print_client is not None and job_store is not None:
        _reconcile_pending_print_jobs(print_client, job_store, summary)
        job_store.flush()

    state_store.flush()
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
