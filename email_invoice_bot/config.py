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


@dataclass(frozen=True)
class AppConfig:
    mail_provider: str

    # Graph API settings
    graph_tenant_id: str
    graph_client_id: str
    graph_client_secret: str
    graph_mailbox: str

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
    log_level: str

    @classmethod
    def from_env(cls) -> "AppConfig":
        output_root = Path(os.getenv("OUTPUT_ROOT", "output")).expanduser().resolve()
        provider = os.getenv("MAIL_PROVIDER", "imap").strip().lower()
        return cls(
            mail_provider=provider,
            graph_tenant_id=os.getenv("GRAPH_TENANT_ID", ""),
            graph_client_id=os.getenv("GRAPH_CLIENT_ID", ""),
            graph_client_secret=os.getenv("GRAPH_CLIENT_SECRET", ""),
            graph_mailbox=os.getenv("GRAPH_MAILBOX", ""),
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
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
