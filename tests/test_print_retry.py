from pathlib import Path

import pymupdf

from email_invoice_bot.print_retry import classify_retry, normalize_pdf


def test_classify_retry_normalizes_known_renderer_error():
    assert classify_retry("engine6_error; Too few operands in path") == "normalize"


def test_classify_retry_resubmits_transient_error():
    assert classify_retry("Printer temporarily unavailable") == "resubmit"


def test_classify_retry_rejects_ambiguous_error():
    assert classify_retry("Unknown spooler failure") is None


def test_normalize_pdf_uses_raster_fallback_without_ghostscript(tmp_path: Path, monkeypatch):
    pdf_path = tmp_path / "invoice.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Invoice")
    document.save(pdf_path)
    document.close()
    original_size = pdf_path.stat().st_size
    monkeypatch.setattr("email_invoice_bot.print_retry.shutil.which", lambda _name: None)

    normalize_pdf(pdf_path)

    normalized = pymupdf.open(pdf_path)
    assert normalized.page_count == 1
    assert pdf_path.stat().st_size > original_size
    normalized.close()
