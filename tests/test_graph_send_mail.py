import json

from email_invoice_bot.graph_client import GraphClient


class StubResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b""


def test_send_mail_uses_configured_mailbox(monkeypatch):
    request_details = {}

    def fake_urlopen(request, timeout):
        request_details["url"] = request.full_url
        request_details["method"] = request.method
        request_details["timeout"] = timeout
        request_details["payload"] = json.loads(request.data)
        return StubResponse()

    monkeypatch.setattr("email_invoice_bot.graph_client.urlopen", fake_urlopen)
    client = GraphClient("tenant", "client", "secret", "info@ebnertransport.com")
    client._token = "token"

    client.send_mail(
        "Druck nicht möglich",
        "Plain body",
        ["Christian.Ebner@ebnertransport.com"],
        ["thanei.jan@gmail.com"],
        html_body="<p>HTML body</p>",
    )

    assert request_details["url"].endswith(
        "/users/info%40ebnertransport.com/sendMail"
    )
    assert request_details["method"] == "POST"
    assert request_details["timeout"] == 30
    message = request_details["payload"]["message"]
    assert message["body"] == {"contentType": "HTML", "content": "<p>HTML body</p>"}
    assert message["toRecipients"] == [
        {"emailAddress": {"address": "Christian.Ebner@ebnertransport.com"}}
    ]
    assert message["ccRecipients"] == [
        {"emailAddress": {"address": "thanei.jan@gmail.com"}}
    ]
    assert request_details["payload"]["saveToSentItems"] is True
