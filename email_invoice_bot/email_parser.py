from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime


URL_RE = re.compile(r"https?://[^\s<>\"]+")
TAG_RE = re.compile(r"<[^>]+>")
HREF_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)


@dataclass
class ParsedAttachment:
    filename: str
    content_type: str
    payload: bytes
    inline: bool


@dataclass
class ParsedEmail:
    uid: str
    message_id: str | None
    subject: str
    sender: str
    received_at: datetime
    body_text: str
    links: list[str]
    attachments: list[ParsedAttachment]
    has_attachments: bool = False


def _to_text_from_html(html: str) -> str:
    no_tags = TAG_RE.sub(" ", html)
    normalized = re.sub(r"\s+", " ", no_tags)
    return normalized.strip()


def parse_email(uid: str, raw_message: bytes) -> ParsedEmail:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)

    subject = message.get("Subject", "").strip()
    sender = message.get("From", "").strip()
    message_id = message.get("Message-ID")
    message_id = message_id.strip() if message_id else None

    date_header = message.get("Date")
    if date_header:
        parsed = parsedate_to_datetime(date_header)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        received_at = parsed.astimezone()
    else:
        received_at = datetime.now().astimezone()

    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[ParsedAttachment] = []

    for part in message.walk():
        if part.is_multipart():
            continue

        content_disposition = part.get_content_disposition() or ""
        content_type = part.get_content_type().lower()
        filename = part.get_filename()

        payload = part.get_payload(decode=True) or b""
        inline = content_disposition == "inline"

        if filename:
            attachments.append(
                ParsedAttachment(
                    filename=filename,
                    content_type=content_type,
                    payload=payload,
                    inline=inline,
                )
            )
            continue

        if content_type == "text/plain":
            try:
                plain_parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
            except LookupError:
                plain_parts.append(payload.decode("utf-8", errors="replace"))
        elif content_type == "text/html":
            try:
                html_parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
            except LookupError:
                html_parts.append(payload.decode("utf-8", errors="replace"))

    body_text = "\n".join([p for p in plain_parts if p.strip()]).strip()
    if not body_text:
        html_text = "\n".join([p for p in html_parts if p.strip()]).strip()
        body_text = _to_text_from_html(html_text) if html_text else ""

    links: list[str] = []
    links.extend(URL_RE.findall(body_text))
    for html in html_parts:
        links.extend(HREF_RE.findall(html))
        links.extend(URL_RE.findall(html))
    # Preserve order while de-duplicating.
    links = list(dict.fromkeys(links))

    return ParsedEmail(
        uid=uid,
        message_id=message_id,
        subject=subject,
        sender=sender,
        received_at=received_at,
        body_text=body_text,
        links=links,
        attachments=attachments,
        has_attachments=bool(attachments),
    )

