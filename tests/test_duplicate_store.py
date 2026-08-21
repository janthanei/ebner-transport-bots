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
    state_path = tmp_path / "duplicate_history.json"
    store = DuplicateStore(state_path, lookback_days=7)
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    store.add("Invoice   A", "Fresh.PDF", processed_at=now, content_hash="ABC123")
    store.flush()

    assert store.has_subject(" invoice a ", now=now)
    assert store.has_filename("fresh.pdf", now=now)
    assert store.has_content_hash("abc123", now=now)

    reloaded = DuplicateStore(state_path, lookback_days=7)
    reloaded.load(now=now)
    assert reloaded.has_content_hash("ABC123", now=now)


def test_duplicate_store_does_not_block_generic_filenames(tmp_path):
    store = DuplicateStore(tmp_path / "duplicate_history.json", lookback_days=7)
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    store.add("Older invoice", "POD.PDF", processed_at=now)
    store.add("Older invoice", "faktura.pdf", processed_at=now)
    store.add("Older invoice", "inv.pdf", processed_at=now)
    store.add("Older invoice", "za_czniki.pdf", processed_at=now)
    store.add("Older invoice", "Rechnung.pdf", processed_at=now)

    assert not store.has_filename("pod.pdf", now=now)
    assert not store.has_filename("Faktura.pdf", now=now)
    assert not store.has_filename("INV.pdf", now=now)
    assert not store.has_filename("za_czniki.pdf", now=now)
    assert not store.has_filename("rechnung.pdf", now=now)
    assert store.has_subject("Older invoice", now=now)


def test_duplicate_store_detects_generic_subjects():
    assert DuplicateStore.is_generic_subject("d")
    assert DuplicateStore.is_generic_subject("WG: d")
    assert DuplicateStore.is_generic_subject("Rechnung")
    assert DuplicateStore.is_generic_subject("REMINDER")
    assert not DuplicateStore.is_generic_subject("Invoice 1454020QBP")


def test_duplicate_store_normalizes_url_keys(tmp_path):
    store = DuplicateStore(tmp_path / "duplicate_history.json", lookback_days=7)
    now = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    store.add_url(
        "Documents",
        "https://documents.discordia.eu/files/9AE74BE8-5BA7-4F63-AD58-9A697E7A09C3?token=abc#view",
        processed_at=now,
    )

    assert store.has_url(
        "https://documents.discordia.eu/files/9ae74be8-5ba7-4f63-ad58-9a697e7a09c3?token=abc",
        now=now,
    )
