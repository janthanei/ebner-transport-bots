from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class PrintNodeClient:
    def __init__(self, api_key: str, printer_id: int, source: str = "ebner-invoice-bot") -> None:
        self.api_key = api_key
        self.printer_id = printer_id
        self.source = source

    def submit_pdf(
        self,
        pdf_path: Path,
        title: str | None = None,
        idempotency_key: str | None = None,
    ) -> int:
        pdf_bytes = pdf_path.read_bytes()
        payload = {
            "printerId": self.printer_id,
            "title": title or pdf_path.name,
            "contentType": "pdf_base64",
            "content": base64.b64encode(pdf_bytes).decode("ascii"),
            "source": self.source,
        }

        auth = base64.b64encode(f"{self.api_key}:".encode("utf-8")).decode("ascii")
        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        req = Request(
            "https://api.printnode.com/printjobs",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace").strip()
                return int(body)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"PrintNode submission failed status={exc.code} body={detail}") from exc

    def get_printjob(self, job_id: int) -> dict[str, Any]:
        auth = base64.b64encode(f"{self.api_key}:".encode("utf-8")).decode("ascii")
        req = Request(
            f"https://api.printnode.com/printjobs/{job_id}",
            headers={
                "Authorization": f"Basic {auth}",
            },
        )
        try:
            with urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace").strip()
                data = json.loads(body) if body else []
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"PrintNode get_printjob failed status={exc.code} body={detail}") from exc

        if not isinstance(data, list) or not data:
            raise RuntimeError(f"PrintNode get_printjob returned empty job_id={job_id}")
        if not isinstance(data[0], dict):
            raise RuntimeError(f"PrintNode get_printjob returned invalid payload job_id={job_id}")
        return data[0]

    def get_printjob_states(self, job_id: int) -> list[dict[str, Any]]:
        auth = base64.b64encode(f"{self.api_key}:".encode("utf-8")).decode("ascii")
        req = Request(
            f"https://api.printnode.com/printjobs/{job_id}/states",
            headers={"Authorization": f"Basic {auth}"},
        )
        try:
            with urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace").strip()
                data = json.loads(body) if body else []
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"PrintNode get_printjob_states failed status={exc.code} body={detail}"
            ) from exc

        if len(data) == 1 and isinstance(data[0], list):
            data = data[0]
        if not isinstance(data, list):
            raise RuntimeError(f"PrintNode returned invalid states job_id={job_id}")
        return [item for item in data if isinstance(item, dict)]

    def list_printjobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        after: int | None = None
        while True:
            query = {"dir": "asc", "limit": 100}
            if after is not None:
                query["after"] = after
            auth = base64.b64encode(f"{self.api_key}:".encode("utf-8")).decode("ascii")
            req = Request(
                f"https://api.printnode.com/printjobs?{urlencode(query)}",
                headers={"Authorization": f"Basic {auth}"},
            )
            try:
                with urlopen(req, timeout=30) as response:
                    body = response.read().decode("utf-8", errors="replace").strip()
                    page = json.loads(body) if body else []
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"PrintNode list_printjobs failed status={exc.code} body={detail}"
                ) from exc

            if not isinstance(page, list):
                raise RuntimeError("PrintNode list_printjobs returned invalid payload")
            valid_page = [item for item in page if isinstance(item, dict)]
            jobs.extend(valid_page)
            if len(page) < 100:
                break
            if not valid_page or not isinstance(valid_page[-1].get("id"), int):
                raise RuntimeError("PrintNode list_printjobs cannot continue pagination")
            after = int(valid_page[-1]["id"])
        return jobs
