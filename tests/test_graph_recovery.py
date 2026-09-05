import base64

import pytest

from email_invoice_bot.graph_client import GraphClient


def client():
    return GraphClient("tenant", "client", "secret", "test@example.com")


def test_attachment_failure_is_not_an_empty_success(monkeypatch):
    graph = client()
    def fail(path):
        raise TimeoutError("temporary outage")
    monkeypatch.setattr(graph, "_api_get", fail)
    with pytest.raises(TimeoutError):
        graph.fetch_message_attachments("message")


def test_all_attachment_pages_are_fetched(monkeypatch):
    graph = client()
    def attachment(name):
        return {"@odata.type": "#microsoft.graph.fileAttachment", "name": name,
                "contentBytes": base64.b64encode(b"pdf").decode()}
    pages = iter([
        {"value": [attachment("a.pdf")], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next"},
        {"value": [attachment("b.pdf")]},
    ])
    monkeypatch.setattr(graph, "_api_get", lambda path: next(pages))
    assert [a.filename for a in graph.fetch_message_attachments("message")] == ["a.pdf", "b.pdf"]


def test_mail_backlog_larger_than_page_size(monkeypatch):
    graph = client()
    def message(n):
        return {"id": str(n), "receivedDateTime": "2026-09-05T10:00:00Z"}
    pages = iter([
        {"value": [message(n) for n in range(10)], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next"},
        {"value": [message(n) for n in range(10, 30)]},
    ])
    monkeypatch.setattr(graph, "_api_get", lambda path: next(pages))
    assert len(graph.fetch_recent_messages(10, 72)) == 30


def test_untrusted_next_link_never_receives_token(monkeypatch):
    graph = client()
    monkeypatch.setattr(graph, "_api_get", lambda path: {
        "value": [], "@odata.nextLink": "https://attacker.example/v1.0/next"})
    with pytest.raises(ValueError, match="untrusted"):
        graph._collection("/users/test/messages")


def test_repeated_next_link_fails_instead_of_looping(monkeypatch):
    graph = client()
    monkeypatch.setattr(graph, "_api_get", lambda path: {
        "value": [], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next"})
    with pytest.raises(RuntimeError, match="repeated"):
        graph._collection("/users/test/messages")
