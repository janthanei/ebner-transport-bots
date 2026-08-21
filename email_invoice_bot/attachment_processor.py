from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

from .content_fingerprint import fingerprint_bytes
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

    @classmethod
    def is_printable_filename(cls, filename: str) -> bool:
        return cls._ext(filename) in PDF_EXTENSIONS | IMAGE_EXTENSIONS

    def output_filename(self, filename: str) -> str:
        return self.storage.build_filename(filename)

    @classmethod
    def printable_pdf_bytes(cls, attachment: ParsedAttachment) -> bytes | None:
        ext = cls._ext(attachment.filename)
        if ext in PDF_EXTENSIONS:
            return attachment.payload
        if ext in IMAGE_EXTENSIONS:
            return cls._image_to_pdf_bytes(attachment.payload)
        return None

    @classmethod
    def content_fingerprint(cls, attachment: ParsedAttachment) -> str:
        pdf_bytes = cls.printable_pdf_bytes(attachment)
        return fingerprint_bytes(pdf_bytes) if pdf_bytes is not None else ""

    def process(
        self,
        email_obj: ParsedEmail,
        attachments: list[ParsedAttachment] | None = None,
    ) -> list[Path]:
        saved_paths: list[Path] = []
        candidates = email_obj.attachments if attachments is None else attachments
        for attachment in candidates:
            if attachment.inline:
                continue

            try:
                pdf_bytes = self.printable_pdf_bytes(attachment)
                if pdf_bytes is not None:
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
