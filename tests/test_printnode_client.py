import base64
import json
from pathlib import Path

from email_invoice_bot.printnode_client import PrintNodeClient


class StubResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b"123"


def test_submit_pdf_sets_expiry(monkeypatch, tmp_path: Path):
    request_details = {}

    def fake_urlopen(request, timeout):
        request_details["payload"] = json.loads(request.data)
        request_details["timeout"] = timeout
        return StubResponse()

    monkeypatch.setattr("email_invoice_bot.printnode_client.urlopen", fake_urlopen)
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"pdf")
    client = PrintNodeClient("api-key", 456, expire_after_seconds=900)

    assert client.submit_pdf(pdf) == 123
    assert request_details["payload"]["expireAfter"] == 900
    assert base64.b64decode(request_details["payload"]["content"]) == b"pdf"
    assert request_details["timeout"] == 30
