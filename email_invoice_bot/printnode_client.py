from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class PrintNodeClient:
    def __init__(self, api_key: str, printer_id: int, source: str = "ebner-invoice-bot") -> None:
        self.api_key = api_key
        self.printer_id = printer_id
        self.source = source

    def submit_pdf(self, pdf_path: Path, title: str | None = None) -> int:
        pdf_bytes = pdf_path.read_bytes()
        payload = {
            "printerId": self.printer_id,
            "title": title or pdf_path.name,
            "contentType": "pdf_base64",
            "content": base64.b64encode(pdf_bytes).decode("ascii"),
            "source": self.source,
        }

        auth = base64.b64encode(f"{self.api_key}:".encode("utf-8")).decode("ascii")
        req = Request(
            "https://api.printnode.com/printjobs",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace").strip()
                return int(body)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"PrintNode submission failed status={exc.code} body={detail}") from exc
