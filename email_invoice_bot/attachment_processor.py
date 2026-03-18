from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

from .email_parser import ParsedAttachment, ParsedEmail
from .storage import DailyPdfStorage


LOGGER = logging.getLogger(__name__)
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


class AttachmentProcessor:
    def __init__(self, storage: DailyPdfStorage) -> None:
        self.storage = storage

    @staticmethod
    def _ext(filename: str) -> str:
        return Path(filename).suffix.lower()

    @staticmethod
    def _image_to_pdf_bytes(image_bytes: bytes) -> bytes:
        with Image.open(io.BytesIO(image_bytes)) as image:
            rgb = image.convert("RGB")
            out = io.BytesIO()
            rgb.save(out, format="PDF")
            return out.getvalue()

    def process(self, email_obj: ParsedEmail) -> list[Path]:
        saved_paths: list[Path] = []
        for attachment in email_obj.attachments:
            if attachment.inline:
                continue

            ext = self._ext(attachment.filename)
            try:
                if ext in PDF_EXTENSIONS:
                    saved_paths.append(
                        self.storage.write_pdf_bytes(
                            attachment.payload,
                            email_obj.received_at,
                            attachment.filename,
                        )
                    )
                elif ext in IMAGE_EXTENSIONS:
                    pdf_bytes = self._image_to_pdf_bytes(attachment.payload)
                    saved_paths.append(
                        self.storage.write_pdf_bytes(
                            pdf_bytes,
                            email_obj.received_at,
                            attachment.filename,
                        )
                    )
            except Exception as exc:
                LOGGER.exception(
                    "Attachment processing failed filename=%s error=%s",
                    attachment.filename,
                    exc,
                )
        return saved_paths

