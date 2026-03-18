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
    imap_host: str
    imap_port: int
    imap_user: str
    imap_app_password: str
    imap_mailbox: str
    poll_interval_seconds: int
    max_emails_per_cycle: int
    output_root: Path
    state_file: Path
    link_substring: str
    link_domain_allowlist: list[str]
    cmr_keyword: str
    playwright_headless: bool
    dry_run: bool
    log_level: str

    @classmethod
    def from_env(cls) -> "AppConfig":
        output_root = Path(os.getenv("OUTPUT_ROOT", "output")).expanduser().resolve()
        state_file = Path(os.getenv("STATE_FILE", "state/processed_state.json")).expanduser().resolve()
        return cls(
            imap_host=_get_required("IMAP_HOST"),
            imap_port=_get_int("IMAP_PORT", 993),
            imap_user=_get_required("IMAP_USER"),
            imap_app_password=_get_required("IMAP_APP_PASSWORD"),
            imap_mailbox=os.getenv("IMAP_MAILBOX", "INBOX"),
            poll_interval_seconds=_get_int("POLL_INTERVAL_SECONDS", 30),
            max_emails_per_cycle=_get_int("MAX_EMAILS_PER_CYCLE", 25),
            output_root=output_root,
            state_file=state_file,
            link_substring=_get_required("LINK_SUBSTRING"),
            link_domain_allowlist=_get_domains("LINK_DOMAIN_ALLOWLIST"),
            cmr_keyword=os.getenv("CMR_KEYWORD", "CMR"),
            playwright_headless=not _get_bool("PLAYWRIGHT_HEADFUL", default=False),
            dry_run=_get_bool("DRY_RUN", default=False),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )

