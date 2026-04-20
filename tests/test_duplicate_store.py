from datetime import datetime, timezone

from email_invoice_bot.duplicate_store import DuplicateStore


def test_duplicate_store_prunes_entries_older_than_lookback(tmp_path):
    state_path = tmp_path / "duplicate_history.json"
    state_path.write_text(
        '{\n'
        '  "records": [\n'
        '    {\n'
        '      "processed_at_utc": "2026-04-10T08:00:00+00:00",\n'
        '      "subject_key": "old invoice",\n'
        '      "filename_key": "old.pdf"\n'
        '    },\n'
        '    {\n'
        '      "processed_at_utc": "2026-04-19T08:00:00+00:00",\n'
        '      "subject_key": "fresh invoice",\n'
        '      "filename_key": "fresh.pdf"\n'
        "    }\n"
        "  ]\n"
        "}\n",
        encoding="utf-8",
    )

    store = DuplicateStore(state_path, lookback_days=7)
    store.load(now=datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc))

    assert not store.has_subject("old invoice", now=datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc))
    assert store.has_filename("fresh.pdf", now=datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc))


def test_duplicate_store_normalizes_subject_and_filename(tmp_path):
    store = DuplicateStore(tmp_path / "duplicate_history.json", lookback_days=7)
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    store.add("Invoice   A", "Fresh.PDF", processed_at=now)

    assert store.has_subject(" invoice a ", now=now)
    assert store.has_filename("fresh.pdf", now=now)
