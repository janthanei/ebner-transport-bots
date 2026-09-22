from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _get_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    return int(value)


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_domains(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [d.strip().lower() for d in value.split(",") if d.strip()]


def _get_list(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class AppConfig:
    mail_provider: str

    # Graph API settings
    graph_tenant_id: str
    graph_client_id: str
    graph_client_secret: str
    graph_mailbox: str
    graph_message_lookback_hours: int
    graph_mark_read: bool

    # IMAP settings (fallback/prototype)
    imap_host: str
    imap_port: int
    imap_user: str
    imap_app_password: str
    imap_mailbox: str

    poll_interval_seconds: int
    max_emails_per_cycle: int
    output_root: Path
    link_substring: str
    link_domain_allowlist: list[str]
    cmr_keyword: str
    playwright_headless: bool
    dry_run: bool
    print_enabled: bool
    printnode_api_key: str
    printnode_printer_id: int
    printnode_expire_after_seconds: int
    print_not_before_utc: str
    print_retry_enabled: bool
    print_retry_delay_seconds: int
    print_email_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_starttls: bool
    smtp_from_email: str
    smtp_from_name: str
    print_alert_to: list[str]
    print_alert_cc: list[str]
    print_error_share_path: str
    print_weekly_report_enabled: bool
    print_weekly_report_to: list[str]
    print_weekly_report_cc: list[str]
    print_weekly_report_weekday: int
    print_weekly_report_hour: int
    print_report_timezone: str
    duplicate_content_hash_shadow: bool
    duplicate_content_hash_active: bool
    retention_delete_after_days: int
    log_level: str

    @classmethod
    def from_env(cls) -> "AppConfig":
        output_root = Path(os.getenv("OUTPUT_ROOT", "output")).expanduser().resolve()
        provider = os.getenv("MAIL_PROVIDER", "imap").strip().lower()
        print_alert_to = _get_list("PRINT_ALERT_TO")
        print_alert_cc = _get_list("PRINT_ALERT_CC")
        print_weekly_report_to = (
            _get_list("PRINT_WEEKLY_REPORT_TO")
            if "PRINT_WEEKLY_REPORT_TO" in os.environ
            else print_alert_to
        )
        print_weekly_report_cc = (
            _get_list("PRINT_WEEKLY_REPORT_CC")
            if "PRINT_WEEKLY_REPORT_CC" in os.environ
            else print_alert_cc
        )
        return cls(
            mail_provider=provider,
            graph_tenant_id=os.getenv("GRAPH_TENANT_ID", ""),
            graph_client_id=os.getenv("GRAPH_CLIENT_ID", ""),
            graph_client_secret=os.getenv("GRAPH_CLIENT_SECRET", ""),
            graph_mailbox=os.getenv("GRAPH_MAILBOX", ""),
            graph_message_lookback_hours=_get_int("GRAPH_MESSAGE_LOOKBACK_HOURS", 72),
            graph_mark_read=_get_bool("GRAPH_MARK_READ", default=True),
            imap_host=os.getenv("IMAP_HOST", ""),
            imap_port=_get_int("IMAP_PORT", 993),
            imap_user=os.getenv("IMAP_USER", ""),
            imap_app_password=os.getenv("IMAP_APP_PASSWORD", ""),
            imap_mailbox=os.getenv("IMAP_MAILBOX", "INBOX"),
            poll_interval_seconds=_get_int("POLL_INTERVAL_SECONDS", 30),
            max_emails_per_cycle=_get_int("MAX_EMAILS_PER_CYCLE", 25),
            output_root=output_root,
            link_substring=_get_required("LINK_SUBSTRING"),
            link_domain_allowlist=_get_domains("LINK_DOMAIN_ALLOWLIST"),
            cmr_keyword=os.getenv("CMR_KEYWORD", "CMR"),
            playwright_headless=not _get_bool("PLAYWRIGHT_HEADFUL", default=False),
            dry_run=_get_bool("DRY_RUN", default=False),
            print_enabled=_get_bool("PRINT_ENABLED", default=False),
            printnode_api_key=os.getenv("PRINTNODE_API_KEY", "").strip(),
            printnode_printer_id=_get_int("PRINTNODE_PRINTER_ID", 0),
            printnode_expire_after_seconds=_get_int(
                "PRINTNODE_EXPIRE_AFTER_SECONDS",
                900,
            ),
            print_not_before_utc=os.getenv("PRINT_NOT_BEFORE_UTC", "").strip(),
            print_retry_enabled=_get_bool("PRINT_RETRY_ENABLED", default=True),
            print_retry_delay_seconds=_get_int("PRINT_RETRY_DELAY_SECONDS", 60),
            print_email_enabled=_get_bool("PRINT_EMAIL_ENABLED", default=False),
            smtp_host=os.getenv("SMTP_HOST", "").strip(),
            smtp_port=_get_int("SMTP_PORT", 587),
            smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_starttls=_get_bool("SMTP_STARTTLS", default=True),
            smtp_from_email=os.getenv("SMTP_FROM_EMAIL", "").strip(),
            smtp_from_name=os.getenv("SMTP_FROM_NAME", "Ebner Druckservice").strip(),
            print_alert_to=print_alert_to,
            print_alert_cc=print_alert_cc,
            print_error_share_path=os.getenv(
                "PRINT_ERROR_SHARE_PATH",
                r"\\45.154.207.113\EbnerTransport\Rechnungen\druck_fehler",
            ).strip(),
            print_weekly_report_enabled=_get_bool(
                "PRINT_WEEKLY_REPORT_ENABLED",
                default=True,
            ),
            print_weekly_report_to=print_weekly_report_to,
            print_weekly_report_cc=print_weekly_report_cc,
            print_weekly_report_weekday=_get_int("PRINT_WEEKLY_REPORT_WEEKDAY", 0),
            print_weekly_report_hour=_get_int("PRINT_WEEKLY_REPORT_HOUR", 8),
            print_report_timezone=os.getenv(
                "PRINT_REPORT_TIMEZONE",
                "Europe/Berlin",
            ).strip(),
            duplicate_content_hash_shadow=_get_bool("DUPLICATE_CONTENT_HASH_SHADOW", default=False),
            duplicate_content_hash_active=_get_bool("DUPLICATE_CONTENT_HASH_ACTIVE", default=False),
            retention_delete_after_days=_get_int("RETENTION_DELETE_AFTER_DAYS", 0),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
