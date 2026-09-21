from __future__ import annotations

import logging
import signal
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .graph_client import GraphClient

from .attachment_processor import AttachmentProcessor
from .config import AppConfig
from .content_fingerprint import fingerprint_file
from .duplicate_store import DuplicateStore
from .email_parser import ParsedEmail, parse_email
from .link_extractor import filter_target_links
from .notifications import PrintNotificationService
from .print_job_store import PendingPrintJob, PrintJobStore
from .print_ledger import PrintLedger
from .print_retry import classify_retry, normalize_pdf
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
    if bucket == "druck_fehler":
        try:
            datetime.strptime(base_dir.name, "%Y-%m-%d")
        except ValueError:
            pass
        else:
            target_dir = base_dir.parent / bucket / base_dir.name
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = DailyPdfStorage.unique_path(target_dir / file_path.name)
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
    mark_read: bool,
    graph_message_id: str,
) -> None:
    summary.processed += 1
    state_store.add(state_key)
    state_store.flush()
    if graph_client is None or not mark_read:
        return
    try:
        graph_client.mark_message_read(graph_message_id)
        LOGGER.info("Marked message read graph_id=%s", graph_message_id)
    except Exception as exc:
        LOGGER.warning("Mark read failed graph_id=%s error=%s", graph_message_id, exc)


def _record_duplicate_history(
    duplicate_store: DuplicateStore,
    subject: str,
    file_paths: list[Path],
    source_urls: list[str] | None = None,
) -> None:
    for file_path in dict.fromkeys(file_paths):
        duplicate_store.add(
            subject,
            file_path.name,
            content_hash=fingerprint_file(file_path),
        )
    for url in dict.fromkeys(source_urls or []):
        duplicate_store.add_url(subject, url)
    duplicate_store.flush()


def _attachment_content_hash(
    attachment_processor: AttachmentProcessor,
    attachment,
) -> str:
    try:
        return attachment_processor.content_fingerprint(attachment)
    except Exception as exc:
        LOGGER.warning(
            "Content fingerprint failed filename=%s error=%s",
            attachment.filename,
            exc,
        )
        return ""


def _file_content_hash(file_path: Path) -> str:
    try:
        return fingerprint_file(file_path)
    except Exception as exc:
        LOGGER.warning("Content fingerprint failed file=%s error=%s", file_path, exc)
        return ""


def _log_shadow_subject_skip(
    duplicate_store: DuplicateStore,
    attachment_processor: AttachmentProcessor,
    email_obj: ParsedEmail,
) -> None:
    printable = [
        attachment
        for attachment in email_obj.attachments
        if not attachment.inline and attachment_processor.is_printable_filename(attachment.filename)
    ]
    fingerprints = [
        (attachment.filename, _attachment_content_hash(attachment_processor, attachment))
        for attachment in printable
    ]
    matched = sum(
        1
        for _filename, content_hash in fingerprints
        if content_hash and duplicate_store.has_content_hash(content_hash)
    )
    if not printable:
        decision = "no_printable_items"
    elif matched == len(printable) and all(content_hash for _filename, content_hash in fingerprints):
        decision = "confirmed_duplicate"
    else:
        decision = "potential_false_positive"
    LOGGER.info(
        "Content hash shadow subject_skip uid=%s subject=%s decision=%s printable=%s matched=%s filenames=%s",
        email_obj.uid,
        email_obj.subject,
        decision,
        len(printable),
        matched,
        ",".join(filename for filename, _content_hash in fingerprints),
    )


def _reconcile_pending_print_jobs(
    print_client: PrintNodeClient,
    job_store: PrintJobStore,
    summary: ProcessSummary,
    ledger: PrintLedger | None = None,
    retry_enabled: bool = False,
    retry_delay_seconds: int = 60,
    now_utc: datetime | None = None,
) -> None:
    current_time = now_utc or datetime.now(timezone.utc)
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
            if ledger is not None:
                ledger.update_status(job_id, "done", file_path=moved)
                if job.retry_count and job.original_job_id is not None:
                    ledger.mark_recovered(job.original_job_id)
            summary.printed_jobs += 1
            LOGGER.info("Print done job_id=%s moved_to=%s", job_id, moved)
            job_store.remove(job_id)
            job_store.flush()
            continue

        if state == "error":
            error_message = job.last_error_message
            if not error_message:
                try:
                    states = print_client.get_printjob_states(job_id)
                    error_message = next(
                        (
                            str(item.get("message", ""))
                            for item in reversed(states)
                            if str(item.get("state", "")).lower() == "error"
                        ),
                        "",
                    )
                except Exception as exc:
                    LOGGER.warning("Print job state history failed job_id=%s error=%s", job_id, exc)

            retry_policy = classify_retry(error_message) if retry_enabled else None
            if retry_policy is not None and job.retry_count == 0:
                if not job.retry_after_utc:
                    retry_after = current_time + timedelta(seconds=max(retry_delay_seconds, 0))
                    job.retry_after_utc = retry_after.isoformat()
                    job.last_error_message = error_message
                    job_store.replace(job_id, job)
                    job_store.flush()
                    if ledger is not None:
                        ledger.update_status(job_id, "retry_waiting", error_message=error_message)
                    LOGGER.warning(
                        "Print retry scheduled job_id=%s policy=%s retry_after=%s error=%s",
                        job_id,
                        retry_policy,
                        job.retry_after_utc,
                        error_message,
                    )
                    continue

                retry_after = datetime.fromisoformat(job.retry_after_utc.replace("Z", "+00:00"))
                if current_time < retry_after:
                    continue

                try:
                    if retry_policy == "normalize":
                        normalize_pdf(file_path)
                    retry_job_id = print_client.submit_pdf(
                        file_path,
                        idempotency_key=f"ebner-retry-{job_id}",
                    )
                    original_job_id = job.original_job_id or job_id
                    replacement = PendingPrintJob(
                        job_id=int(retry_job_id),
                        file_path=job.file_path,
                        base_dir=job.base_dir,
                        created_utc=current_time.isoformat(),
                        email_uid=job.email_uid,
                        email_subject=job.email_subject,
                        retry_count=1,
                        original_job_id=original_job_id,
                    )
                    job_store.replace(job_id, replacement)
                    job_store.flush()
                    if ledger is not None:
                        ledger.update_status(job_id, "retried", error_message=error_message)
                        ledger.record_submission(
                            job_id=int(retry_job_id),
                            file_path=file_path,
                            printer_id=print_client.printer_id,
                            email_uid=job.email_uid,
                            email_subject=job.email_subject,
                            retry_count=1,
                            original_job_id=original_job_id,
                            retry_of_job_id=job_id,
                            submitted_utc=current_time.isoformat(),
                        )
                    LOGGER.warning(
                        "Print retry submitted original_job_id=%s retry_job_id=%s policy=%s",
                        job_id,
                        retry_job_id,
                        retry_policy,
                    )
                    continue
                except Exception as exc:
                    error_message = f"{error_message}; retry failed: {exc}".strip("; ")
                    LOGGER.exception("Print retry failed job_id=%s error=%s", job_id, exc)

            moved = _move_to_print_bucket(file_path, "druck_fehler")
            if ledger is not None:
                ledger.update_status(
                    job_id,
                    "error",
                    file_path=moved,
                    error_message=error_message,
                )
                if job.retry_count and job.original_job_id is not None:
                    ledger.mark_retry_failed(job.original_job_id)
            LOGGER.warning(
                "Print error job_id=%s moved_to=%s retry_count=%s error=%s",
                job_id,
                moved,
                job.retry_count,
                error_message,
            )
            job_store.remove(job_id)
            job_store.flush()
            continue

        LOGGER.info("Print job unknown state job_id=%s state=%s", job_id, state)


def process_cycle(config: AppConfig) -> ProcessSummary:
    summary = ProcessSummary()
    state_store = StateStore(Path("state/processed_state.json"))
    state_store.load()
    duplicate_store = DuplicateStore(Path("state/duplicate_history.json"))
    duplicate_store.load()
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
    content_fingerprint_enabled = (
        config.duplicate_content_hash_shadow or config.duplicate_content_hash_active
    )
    web_downloader = WebDownloader(
        cmr_keyword=config.cmr_keyword,
        headless=config.playwright_headless,
        dry_run=config.dry_run,
        allowlist=config.link_domain_allowlist,
    )
    print_client = None
    job_store = None
    print_ledger = None
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
        print_ledger = PrintLedger(Path("state/print_history.sqlite3"))
        for pending_job in job_store.items():
            print_ledger.record_submission(
                job_id=pending_job.job_id,
                file_path=Path(pending_job.file_path),
                printer_id=config.printnode_printer_id,
                email_uid=pending_job.email_uid,
                email_subject=pending_job.email_subject,
                retry_count=pending_job.retry_count,
                original_job_id=pending_job.original_job_id,
                submitted_utc=pending_job.created_utc,
            )
        _reconcile_pending_print_jobs(
            print_client,
            job_store,
            summary,
            print_ledger,
            retry_enabled=config.print_retry_enabled,
            retry_delay_seconds=config.print_retry_delay_seconds,
        )
    print_not_before = _parse_not_before_utc(config.print_not_before_utc)

    for email_obj in emails:
        state_key = StateStore.build_key(email_obj.uid, email_obj.message_id)
        if state_store.has(state_key):
            LOGGER.debug("Skipping already processed email uid=%s", email_obj.uid)
            continue

        target_links = filter_target_links(email_obj.links, config.link_substring)
        has_target_links = bool(target_links)
        subject_duplicate = duplicate_store.has_subject(email_obj.subject)
        subject_is_generic = duplicate_store.is_generic_subject(email_obj.subject)
        if subject_duplicate and subject_is_generic:
            LOGGER.info(
                "Ignoring weak duplicate subject uid=%s subject=%s links=%s",
                email_obj.uid,
                email_obj.subject,
                len(target_links),
            )
        if subject_duplicate and has_target_links:
            LOGGER.info(
                "Ignoring duplicate subject for link-bearing email uid=%s subject=%s links=%s",
                email_obj.uid,
                email_obj.subject,
                len(target_links),
            )
        should_skip_subject = subject_duplicate and not subject_is_generic and not has_target_links
        if (
            content_fingerprint_enabled
            and should_skip_subject
            and graph_client is not None
            and email_obj.has_attachments
            and not email_obj.attachments
        ):
            email_obj.attachments = graph_client.fetch_message_attachments(email_obj.uid)
        if config.duplicate_content_hash_shadow and should_skip_subject:
            _log_shadow_subject_skip(duplicate_store, attachment_processor, email_obj)
        printable_attachments = [
            attachment
            for attachment in email_obj.attachments
            if not attachment.inline and attachment_processor.is_printable_filename(attachment.filename)
        ]
        if config.duplicate_content_hash_active and should_skip_subject and printable_attachments:
            LOGGER.info(
                "Deferring duplicate subject to content fingerprints uid=%s subject=%s printable=%s",
                email_obj.uid,
                email_obj.subject,
                len(printable_attachments),
            )
            should_skip_subject = False
        if should_skip_subject:
            LOGGER.info("Skipping duplicate email subject uid=%s subject=%s", email_obj.uid, email_obj.subject)
            _finalize_processed_email(
                summary,
                state_store,
                state_key,
                graph_client,
                False,
                email_obj.uid,
            )
            continue

        if graph_client is not None and email_obj.has_attachments and not email_obj.attachments:
            email_obj.attachments = graph_client.fetch_message_attachments(email_obj.uid)

        accepted_output_names: set[str] = set()
        accepted_content_hash_values: set[str] = set()
        filtered_attachments = []
        for attachment in email_obj.attachments:
            if attachment.inline:
                continue
            if not attachment_processor.is_printable_filename(attachment.filename):
                continue
            output_name = attachment_processor.output_filename(attachment.filename)
            content_hash = (
                _attachment_content_hash(attachment_processor, attachment)
                if content_fingerprint_enabled
                else ""
            )
            duplicate_in_email = output_name in accepted_output_names
            duplicate_in_history = duplicate_store.has_filename(output_name)
            duplicate_content = bool(content_hash) and (
                content_hash in accepted_content_hash_values
                or duplicate_store.has_content_hash(content_hash)
            )
            weak_duplicate = duplicate_in_email or duplicate_in_history or (
                subject_duplicate and not subject_is_generic and not has_target_links
            )
            if config.duplicate_content_hash_active and duplicate_content:
                LOGGER.info(
                    "Skipping duplicate attachment content uid=%s filename=%s output_name=%s",
                    email_obj.uid,
                    attachment.filename,
                    output_name,
                )
                continue
            if config.duplicate_content_hash_active and content_hash:
                if weak_duplicate:
                    LOGGER.info(
                        "Accepting changed attachment content uid=%s filename=%s output_name=%s",
                        email_obj.uid,
                        attachment.filename,
                        output_name,
                    )
            elif weak_duplicate:
                LOGGER.info(
                    "Skipping duplicate attachment uid=%s filename=%s output_name=%s",
                    email_obj.uid,
                    attachment.filename,
                    output_name,
                )
                if config.duplicate_content_hash_shadow:
                    LOGGER.info(
                        "Content hash shadow filename_skip uid=%s filename=%s decision=%s",
                        email_obj.uid,
                        attachment.filename,
                        "confirmed_duplicate" if duplicate_content else "potential_false_positive",
                    )
                continue
            if (
                config.duplicate_content_hash_shadow
                and content_hash
                and (
                    content_hash in accepted_content_hash_values
                    or duplicate_store.has_content_hash(content_hash)
                )
            ):
                LOGGER.info(
                    "Content hash shadow attachment_accept uid=%s filename=%s decision=would_skip_content_duplicate",
                    email_obj.uid,
                    attachment.filename,
                )
            accepted_output_names.add(output_name)
            if content_hash:
                accepted_content_hash_values.add(content_hash)
            filtered_attachments.append(attachment)

        saved_paths = attachment_processor.process(email_obj, attachments=filtered_attachments)
        summary.saved_attachments += len(saved_paths)
        files_to_print = list(saved_paths)

        reserved_download_names = set(accepted_output_names)
        reserved_url_keys: set[str] = set()
        urls_with_downloads: list[str] = []

        def _should_download(candidate) -> bool:
            output_name = storage.build_filename(candidate.filename_hint)
            if config.duplicate_content_hash_active:
                reserved_download_names.add(output_name)
                return True
            if output_name in reserved_download_names or duplicate_store.has_filename(output_name):
                LOGGER.info(
                    "Skipping duplicate download uid=%s hint=%s output_name=%s",
                    email_obj.uid,
                    candidate.filename_hint,
                    output_name,
                )
                return False
            reserved_download_names.add(output_name)
            return True

        for url in target_links:
            url_key = DuplicateStore.normalize_url(url)
            if url_key in reserved_url_keys or duplicate_store.has_url(url):
                LOGGER.info("Skipping duplicate link uid=%s url=%s", email_obj.uid, url)
                continue
            reserved_url_keys.add(url_key)
            day_dir = storage.get_day_dir(email_obj.received_at)
            result = web_downloader.scan_and_download(
                url=url,
                output_dir=day_dir,
                should_download=_should_download,
            )
            accepted_downloads: list[Path] = []
            for downloaded_path in result.downloaded_paths:
                content_hash = (
                    _file_content_hash(downloaded_path)
                    if content_fingerprint_enabled
                    else ""
                )
                duplicate_content = bool(content_hash) and (
                    content_hash in accepted_content_hash_values
                    or duplicate_store.has_content_hash(content_hash)
                )
                if config.duplicate_content_hash_active and duplicate_content:
                    try:
                        downloaded_path.unlink()
                    except OSError as exc:
                        LOGGER.warning(
                            "Failed removing duplicate download file=%s error=%s",
                            downloaded_path,
                            exc,
                        )
                    LOGGER.info(
                        "Skipping duplicate download content uid=%s file=%s",
                        email_obj.uid,
                        downloaded_path.name,
                    )
                    continue
                if config.duplicate_content_hash_shadow and duplicate_content:
                    LOGGER.info(
                        "Content hash shadow download_accept uid=%s file=%s decision=would_skip_content_duplicate",
                        email_obj.uid,
                        downloaded_path.name,
                    )
                if content_hash:
                    accepted_content_hash_values.add(content_hash)
                accepted_downloads.append(downloaded_path)
            summary.downloaded_from_web += len(accepted_downloads)
            files_to_print.extend(accepted_downloads)
            if accepted_downloads:
                urls_with_downloads.append(url)
            LOGGER.info(
                "Link processed uid=%s url=%s scanned=%s cmr_found=%s downloaded=%s",
                email_obj.uid,
                url,
                result.scanned_candidates,
                result.cmr_found,
                len(accepted_downloads),
            )

        extracted_files = list(dict.fromkeys(files_to_print))
        if extracted_files:
            _record_duplicate_history(duplicate_store, email_obj.subject, extracted_files, urls_with_downloads)

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
                    config.graph_mark_read and bool(extracted_files),
                    email_obj.uid,
                )
                continue
            for file_path in extracted_files:
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
                                email_uid=email_obj.uid,
                                email_subject=email_obj.subject,
                                original_job_id=int(job_id),
                            )
                        )
                        job_store.flush()
                    if print_ledger is not None:
                        print_ledger.record_submission(
                            job_id=int(job_id),
                            file_path=moved,
                            printer_id=config.printnode_printer_id,
                            email_uid=email_obj.uid,
                            email_subject=email_obj.subject,
                            original_job_id=int(job_id),
                        )
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
                        if print_ledger is not None:
                            print_ledger.record_submission_failure(
                                file_path=moved,
                                printer_id=config.printnode_printer_id,
                                error_message=str(exc),
                                email_uid=email_obj.uid,
                                email_subject=email_obj.subject,
                            )
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
            config.graph_mark_read and bool(extracted_files),
            email_obj.uid,
        )

    if print_client is not None and job_store is not None:
        _reconcile_pending_print_jobs(
            print_client,
            job_store,
            summary,
            print_ledger,
            retry_enabled=config.print_retry_enabled,
            retry_delay_seconds=config.print_retry_delay_seconds,
        )
        job_store.flush()

    if config.print_email_enabled and print_ledger is not None:
        notifier = PrintNotificationService(
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
            smtp_username=config.smtp_username,
            smtp_password=config.smtp_password,
            smtp_starttls=config.smtp_starttls,
            from_email=config.smtp_from_email,
            from_name=config.smtp_from_name,
            recipients=config.print_alert_to,
            cc=config.print_alert_cc,
            error_share_path=config.print_error_share_path,
            report_timezone=config.print_report_timezone,
            weekly_weekday=config.print_weekly_report_weekday,
            weekly_hour=config.print_weekly_report_hour,
        )
        notifier.send_unnotified_errors(print_ledger)
        if config.print_weekly_report_enabled:
            try:
                notifier.maybe_send_weekly_report(print_ledger)
            except Exception as exc:
                LOGGER.exception("Weekly print report failed error=%s", exc)

    duplicate_store.flush()
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
