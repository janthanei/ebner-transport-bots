from datetime import datetime

from email_invoice_bot.storage import DailyPdfStorage


def test_write_pdf_bytes_suffixes_existing_original_filename(tmp_path):
    storage = DailyPdfStorage(tmp_path)
    at = datetime(2026, 3, 3, 12, 0, 0)
    a = storage.write_pdf_bytes(b"one", at, "invoice.pdf")
    b = storage.write_pdf_bytes(b"two", at, "invoice.pdf")

    assert a.exists()
    assert b.exists()
    assert a != b
    assert a.parent.name == "2026-03-03"
    assert a.name == "invoice.pdf"
    assert b.name == "invoice_2.pdf"
    assert a.read_bytes() == b"one"
    assert b.read_bytes() == b"two"
