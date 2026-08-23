import time

import pymupdf

from email_invoice_bot.attachment_processor import AttachmentProcessor
from email_invoice_bot.content_fingerprint import fingerprint_pdf_bytes


def _make_pdf(text: str, created: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.set_metadata({"creationDate": created, "modDate": created})
    return document.tobytes()


def test_pdf_fingerprint_ignores_metadata_changes():
    first = _make_pdf("Invoice total: 100.00 EUR", "D:20260818145516")
    second = _make_pdf("Invoice total: 100.00 EUR", "D:20260818162029")

    assert first != second
    assert fingerprint_pdf_bytes(first) == fingerprint_pdf_bytes(second)


def test_pdf_fingerprint_detects_visible_changes():
    first = _make_pdf("Invoice total: 100.00 EUR", "D:20260818145516")
    second = _make_pdf("Invoice total: 110.00 EUR", "D:20260818145516")

    assert fingerprint_pdf_bytes(first) != fingerprint_pdf_bytes(second)


def test_image_conversion_fingerprint_is_stable():
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (20, 20), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    image_bytes = output.getvalue()

    first = AttachmentProcessor._image_to_pdf_bytes(image_bytes)
    time.sleep(1.1)
    second = AttachmentProcessor._image_to_pdf_bytes(image_bytes)

    assert first != second
    assert fingerprint_pdf_bytes(first) == fingerprint_pdf_bytes(second)
