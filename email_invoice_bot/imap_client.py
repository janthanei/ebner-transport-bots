from __future__ import annotations

import imaplib
import logging
from contextlib import contextmanager
from typing import Iterator


LOGGER = logging.getLogger(__name__)


class ImapClient:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        app_password: str,
        mailbox: str = "INBOX",
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.app_password = app_password
        self.mailbox = mailbox

    @contextmanager
    def connect(self) -> Iterator[imaplib.IMAP4_SSL]:
        client = imaplib.IMAP4_SSL(self.host, self.port)
        try:
            client.login(self.user, self.app_password)
            status, _ = client.select(self.mailbox)
            if status != "OK":
                raise RuntimeError(f"Failed to select mailbox: {self.mailbox}")
            yield client
        finally:
            try:
                client.close()
            except Exception:
                pass
            client.logout()

    @staticmethod
    def search_candidate_uids(client: imaplib.IMAP4_SSL, max_count: int) -> list[str]:
        # Process only unread mail to avoid back-processing old inbox items.
        status, data = client.uid("search", None, "UNSEEN")
        if status != "OK" or not data:
            return []

        raw_uids = data[0].decode("utf-8").strip()
        if not raw_uids:
            return []

        uids = raw_uids.split()
        return uids[-max_count:]

    @staticmethod
    def fetch_message(client: imaplib.IMAP4_SSL, uid: str) -> bytes:
        status, message_data = client.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not message_data:
            raise RuntimeError(f"Failed to fetch message UID {uid}")

        for item in message_data:
            if not isinstance(item, tuple):
                continue
            payload = item[1]
            if isinstance(payload, bytes):
                return payload
        raise RuntimeError(f"No RFC822 payload for UID {uid}")

    def fetch_recent_messages(self, max_count: int) -> list[tuple[str, bytes]]:
        messages: list[tuple[str, bytes]] = []
        with self.connect() as client:
            uids = self.search_candidate_uids(client, max_count=max_count)
            for uid in uids:
                try:
                    raw = self.fetch_message(client, uid)
                    messages.append((uid, raw))
                except Exception as exc:
                    LOGGER.exception("Failed fetching uid=%s error=%s", uid, exc)
        return messages

