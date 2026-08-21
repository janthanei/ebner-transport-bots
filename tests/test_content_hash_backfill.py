import os
from datetime import datetime, timedelta, timezone

from email_invoice_bot.backfill_content_hashes import backfill_successful_prints
from email_invoice_bot.content_fingerprint import fingerprint_file
from email_invoice_bot.duplicate_store import DuplicateStore


def test_backfill_adds_successful_print_hashes_once(tmp_path):
    success_dir = tmp_path / "output" / "Rechnungen" / "2026-08-21" / "druck_erfolg"
    success_dir.mkdir(parents=True)
    printed_file = success_dir / "invoice.pdf"
    printed_file.write_bytes(b"%PDF-1.7 printed")
    expired_file = success_dir / "expired.pdf"
    expired_file.write_bytes(b"%PDF-1.7 expired")
    expired_at = datetime.now(timezone.utc) - timedelta(days=8)
    os.utime(expired_file, (expired_at.timestamp(), expired_at.timestamp()))

    store = DuplicateStore(tmp_path / "duplicate_history.json")
    store.load()

    assert backfill_successful_prints(tmp_path / "output", store) == (1, 0)
    assert store.has_content_hash(fingerprint_file(printed_file))
    assert not store.has_content_hash(fingerprint_file(expired_file))
    assert backfill_successful_prints(tmp_path / "output", store) == (0, 1)
