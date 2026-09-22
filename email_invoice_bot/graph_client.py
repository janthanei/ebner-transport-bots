from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from .email_parser import ParsedAttachment, ParsedEmail

LOGGER = logging.getLogger(__name__)

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphClient:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        mailbox: str,
    ) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.mailbox = mailbox
        self._token: str | None = None

    def _acquire_token(self) -> str:
        url = TOKEN_URL.format(tenant=self.tenant_id)
        body = (
            f"client_id={quote(self.client_id)}"
            f"&client_secret={quote(self.client_secret)}"
            f"&scope={quote('https://graph.microsoft.com/.default')}"
            f"&grant_type=client_credentials"
        ).encode()
        req = Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        started = time.monotonic()
        LOGGER.info("Graph token request start mailbox=%s", self.mailbox)
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        LOGGER.info("Graph token request done mailbox=%s duration_s=%.2f", self.mailbox, time.monotonic() - started)
        return data["access_token"]

    def _get_token(self) -> str:
        if self._token is None:
            self._token = self._acquire_token()
        return self._token

    def _api_get(self, path: str) -> Any:
        url = self._graph_url(path)
        req = Request(url, headers={"Authorization": f"Bearer {self._get_token()}"})
        started = time.monotonic()
        LOGGER.info("Graph GET start path=%s", path)
        try:
            with urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            LOGGER.info("Graph GET done path=%s duration_s=%.2f", path, time.monotonic() - started)
            return payload
        except Exception as exc:
            LOGGER.warning("Graph GET failed path=%s duration_s=%.2f error=%s", path, time.monotonic() - started, exc)
            self._token = None
            raise

    @staticmethod
    def _graph_url(path: str) -> str:
        url = f"{GRAPH_BASE}{path}" if path.startswith("/") else path
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com"
                or not parsed.path.startswith("/v1.0/")):
            raise ValueError("Refusing untrusted Graph pagination URL")
        return url

    def _collection(self, path: str) -> list[dict]:
        items = []
        seen = set()
        while path:
            url = self._graph_url(path)
            if url in seen:
                raise RuntimeError("Graph returned a repeated pagination URL")
            seen.add(url)
            data = self._api_get(path)
            if not isinstance(data, dict) or not isinstance(data.get("value"), list):
                raise ValueError("Invalid Graph collection response")
            items.extend(data["value"])
            path = data.get("@odata.nextLink", "")
        return items

    def _api_patch(self, path: str, body: dict[str, Any]) -> None:
        url = f"{GRAPH_BASE}{path}"
        payload = json.dumps(body).encode("utf-8")
        req = Request(
            url,
            data=payload,
            method="PATCH",
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=30) as resp:
                resp.read()
        except Exception:
            self._token = None
            raise

    def _api_post(self, path: str, body: dict[str, Any]) -> None:
        url = self._graph_url(path)
        payload = json.dumps(body).encode("utf-8")
        req = Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=30) as resp:
                resp.read()
        except Exception:
            self._token = None
            raise

    def mark_message_read(self, message_id: str) -> None:
        user = quote(self.mailbox)
        mid = quote(message_id, safe="")
        self._api_patch(f"/users/{user}/messages/{mid}", {"isRead": True})

    def send_mail(
        self,
        subject: str,
        body: str,
        recipients: list[str],
        cc: list[str] | None = None,
        *,
        html_body: str | None = None,
    ) -> None:
        if not recipients:
            raise ValueError("Graph mail requires at least one recipient")

        def graph_recipients(addresses: list[str]) -> list[dict[str, dict[str, str]]]:
            return [
                {"emailAddress": {"address": address}}
                for address in addresses
                if address.strip()
            ]

        user = quote(self.mailbox)
        message = {
            "subject": subject,
            "body": {
                "contentType": "HTML" if html_body else "Text",
                "content": html_body or body,
            },
            "toRecipients": graph_recipients(recipients),
            "ccRecipients": graph_recipients(cc or []),
        }
        self._api_post(
            f"/users/{user}/sendMail",
            {"message": message, "saveToSentItems": True},
        )

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        cleaned = value.rstrip("Z") if value.endswith("Z") else value
        dt = datetime.fromisoformat(cleaned).replace(tzinfo=timezone.utc)
        return dt.astimezone()

    def _extract_body_and_links(self, msg: dict) -> tuple[str, list[str]]:
        import re
        body_obj = msg.get("body", {})
        content = body_obj.get("content", "")
        content_type = body_obj.get("contentType", "").lower()

        url_re = re.compile(r"https?://[^\s<>\"]+")
        href_re = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)

        if content_type == "html":
            links = href_re.findall(content) + url_re.findall(content)
            tag_re = re.compile(r"<[^>]+>")
            text = re.sub(r"\s+", " ", tag_re.sub(" ", content)).strip()
        else:
            text = content.strip()
            links = url_re.findall(text)

        return text, list(dict.fromkeys(links))

    def _fetch_attachments(self, message_id: str) -> list[ParsedAttachment]:
        user = quote(self.mailbox)
        mid = quote(message_id, safe="")
        started = time.monotonic()
        LOGGER.info("Graph attachment fetch start mailbox=%s message_id=%s", self.mailbox, message_id)
        rows = self._collection(f"/users/{user}/messages/{mid}/attachments")
        attachments: list[ParsedAttachment] = []
        for att in rows:
            if att.get("@odata.type", "") != "#microsoft.graph.fileAttachment":
                continue
            name = att.get("name", "")
            content_type = att.get("contentType", "application/octet-stream").lower()
            content_bytes = base64.b64decode(att.get("contentBytes", ""), validate=True)
            inline = att.get("isInline", False)
            if name and content_bytes:
                attachments.append(ParsedAttachment(
                    filename=name,
                    content_type=content_type,
                    payload=content_bytes,
                    inline=inline,
                ))
        LOGGER.info("Graph attachment fetch done mailbox=%s message_id=%s attachment_count=%s duration_s=%.2f", self.mailbox, message_id, len(attachments), time.monotonic() - started)
        return attachments

    def fetch_message_attachments(self, message_id: str) -> list[ParsedAttachment]:
        try:
            return self._fetch_attachments(message_id)
        except Exception as exc:
            LOGGER.exception("Failed fetching attachments msg=%s error=%s", message_id, exc)
            raise

    def fetch_recent_messages(self, max_count: int, lookback_hours: int) -> list[ParsedEmail]:
        user = quote(self.mailbox)
        since = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
        since_str = since.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        select = "id,subject,from,receivedDateTime,body,hasAttachments,isRead,internetMessageId,webLink"
        qs = urlencode({
            "$filter": f"receivedDateTime ge {since_str}",
            "$orderby": "receivedDateTime desc",
            "$top": str(max_count),
            "$select": select,
        })
        path = f"/users/{user}/mailFolders/inbox/messages?{qs}"
        LOGGER.info("Graph inbox fetch start mailbox=%s lookback_hours=%s max_count=%s", self.mailbox, lookback_hours, max_count)
        raw_messages = self._collection(path)
        LOGGER.info("Graph inbox fetch done mailbox=%s message_count=%s", self.mailbox, len(raw_messages))
        results: list[ParsedEmail] = []

        for index, msg in enumerate(raw_messages, start=1):
            msg_id = msg.get("id", "")
            subject = msg.get("subject", "").strip()
            LOGGER.info("Graph message parse start mailbox=%s index=%s/%s message_id=%s subject=%s has_attachments=%s", self.mailbox, index, len(raw_messages), msg_id, subject, msg.get("hasAttachments", False))
            from_obj = msg.get("from", {}).get("emailAddress", {})
            sender = from_obj.get("address", from_obj.get("name", ""))
            received_at = self._parse_datetime(msg.get("receivedDateTime", ""))
            internet_msg_id = msg.get("internetMessageId")

            body_text, links = self._extract_body_and_links(msg)

            has_attachments = bool(msg.get("hasAttachments", False))

            results.append(ParsedEmail(
                uid=msg_id,
                message_id=internet_msg_id,
                subject=subject,
                sender=sender,
                received_at=received_at,
                body_text=body_text,
                links=links,
                attachments=[],
                has_attachments=has_attachments,
                web_url=str(msg.get("webLink", "")),
            ))
            LOGGER.info("Graph message parse done mailbox=%s index=%s/%s message_id=%s has_attachments=%s links=%s", self.mailbox, index, len(raw_messages), msg_id, has_attachments, len(links))

        return results
