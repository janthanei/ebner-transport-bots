from email.message import EmailMessage

from email_invoice_bot.email_parser import parse_email


def _build_sample_email() -> bytes:
    msg = EmailMessage()
    msg["Subject"] = "Invoice 123"
    msg["From"] = "sender@example.com"
    msg["To"] = "receiver@example.com"
    msg["Date"] = "Tue, 03 Mar 2026 10:00:00 +0000"
    msg["Message-ID"] = "<msg-1@example.com>"
    msg.set_content("Please see invoice. https://download.example.com/inv/1")
    msg.add_attachment(
        b"%PDF-1.7 fake",
        maintype="application",
        subtype="pdf",
        filename="invoice.pdf",
    )
    return msg.as_bytes()


def test_parse_email_extracts_links_and_attachments():
    parsed = parse_email(uid="101", raw_message=_build_sample_email())
    assert parsed.uid == "101"
    assert parsed.message_id == "<msg-1@example.com>"
    assert "download.example.com" in parsed.links[0]
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].filename == "invoice.pdf"

