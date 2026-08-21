from datetime import datetime
from io import BytesIO

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image

from email_invoice_bot.attachment_processor import AttachmentProcessor
from email_invoice_bot.content_fingerprint import fingerprint_file
from email_invoice_bot.email_parser import ParsedAttachment, ParsedEmail
from email_invoice_bot.storage import DailyPdfStorage


def _make_png_bytes() -> bytes:
    im = Image.new("RGB", (10, 10), "white")
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def test_image_attachment_converted_to_pdf(tmp_path):
    parsed = ParsedEmail(
        uid="1",
        message_id="<a@b>",
        subject="Invoice",
        sender="sender@example.com",
        received_at=datetime(2026, 3, 3, 8, 0, 0),
        body_text="hello",
        links=[],
        attachments=[
            ParsedAttachment(
                filename="invoice.png",
                content_type="image/png",
                payload=_make_png_bytes(),
                inline=False,
            )
        ],
    )
    proc = AttachmentProcessor(DailyPdfStorage(tmp_path))
    paths = proc.process(parsed)
    assert len(paths) == 1
    assert paths[0].suffix.lower() == ".pdf"
    assert paths[0].exists()
    assert paths[0].name == "invoice.pdf"
    assert proc.content_fingerprint(parsed.attachments[0]) == fingerprint_file(paths[0])
