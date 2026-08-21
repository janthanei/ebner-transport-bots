from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from .config import AppConfig
from .content_fingerprint import fingerprint_file
from .duplicate_store import DuplicateStore


def backfill_successful_prints(output_root: Path, duplicate_store: DuplicateStore) -> tuple[int, int]:
    added = 0
    existing = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=duplicate_store.lookback_days)
    pattern = "Rechnungen/*/druck_erfolg/*.pdf"
    for file_path in sorted(output_root.glob(pattern)):
        processed_at = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
        if processed_at < cutoff:
            continue
        content_hash = fingerprint_file(file_path)
        if duplicate_store.has_content_hash(content_hash):
            existing += 1
            continue
        duplicate_store.add(
            "",
            file_path.name,
            processed_at=processed_at,
            content_hash=content_hash,
        )
        added += 1
    duplicate_store.flush()
    return added, existing


def run() -> None:
    load_dotenv()
    config = AppConfig.from_env()
    duplicate_store = DuplicateStore(Path("state/duplicate_history.json"))
    duplicate_store.load()
    added, existing = backfill_successful_prints(config.output_root, duplicate_store)
    print(f"Content hash backfill complete added={added} existing={existing}")


if __name__ == "__main__":
    run()
