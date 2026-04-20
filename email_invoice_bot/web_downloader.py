from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from .link_extractor import is_domain_allowed


LOGGER = logging.getLogger(__name__)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@dataclass
class PdfCandidate:
    index: int
    href: str
    label: str
    filename_hint: str


@dataclass
class DownloadResult:
    scanned_candidates: int
    cmr_found: bool
    downloaded_paths: list[Path]


def contains_cmr(candidates: list[PdfCandidate], keyword: str) -> bool:
    key = keyword.lower()
    for c in candidates:
        haystack = f"{c.filename_hint} {c.label}".lower()
        if key in haystack:
            return True
    return False


class WebDownloader:
    def __init__(self, cmr_keyword: str, headless: bool, dry_run: bool, allowlist: list[str]) -> None:
        self.cmr_keyword = cmr_keyword
        self.headless = headless
        self.dry_run = dry_run
        self.allowlist = allowlist

    @staticmethod
    def _safe_name(name: str, index: int) -> str:
        base = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name.strip())
        base = base.strip("._") or f"download_{index}"
        return base

    @staticmethod
    def _filename_from_onclick(onclick: str) -> str:
        # Discordia-style links keep the true filename as the last JS argument.
        matches = re.findall(r"'([^']+\.[A-Za-z0-9]{2,8})'", onclick or "")
        if matches:
            return matches[-1]
        return ""

    @staticmethod
    def _convert_image_file_to_pdf(path: Path) -> Path:
        try:
            from PIL import Image
        except ImportError:
            return path
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            pdf_path = path.with_suffix(".pdf")
            rgb.save(pdf_path, format="PDF")
        path.unlink(missing_ok=True)
        return pdf_path

    @staticmethod
    def _write_http_body(target_path: Path, body: bytes, content_type: str) -> Path:
        suffix = target_path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS or content_type.startswith("image/"):
            try:
                from PIL import Image
            except ImportError:
                target_path.write_bytes(body)
                return target_path
            with Image.open(io.BytesIO(body)) as image:
                rgb = image.convert("RGB")
                pdf_path = target_path.with_suffix(".pdf")
                rgb.save(pdf_path, format="PDF")
            return pdf_path
        target_path.write_bytes(body)
        return target_path

    @staticmethod
    def _extract_candidates(page, base_url: str) -> list[PdfCandidate]:
        rows = page.eval_on_selector_all(
            "a",
            """anchors => anchors.map((a, idx) => ({
                index: idx,
                href: a.getAttribute('href') || '',
                text: (a.textContent || '').trim(),
                download: a.getAttribute('download') || '',
                onclick: a.getAttribute('onclick') || ''
            }))""",
        )
        candidates: list[PdfCandidate] = []
        for row in rows:
            href = row.get("href", "")
            text = row.get("text", "")
            download = row.get("download", "")
            onclick = row.get("onclick", "")
            onclick_name = WebDownloader._filename_from_onclick(onclick)
            combined = f"{href} {text} {download} {onclick}".lower()
            # Include direct links and JS-triggered downloads (e.g. downloadFile(...)).
            if ".pdf" not in combined and "downloadfile(" not in combined:
                continue
            abs_href = urljoin(base_url, href)
            hint = download or onclick_name or Path(abs_href).name or text or f"candidate_{row['index']}"
            candidates.append(
                PdfCandidate(
                    index=int(row["index"]),
                    href=abs_href,
                    label=text,
                    filename_hint=hint,
                )
            )
        return candidates

    def scan_and_download(
        self,
        url: str,
        output_dir: Path,
        should_download: Callable[[PdfCandidate], bool] | None = None,
    ) -> DownloadResult:
        if not is_domain_allowed(url, self.allowlist):
            LOGGER.warning("Skipping URL outside allowlist url=%s", url)
            return DownloadResult(scanned_candidates=0, cmr_found=False, downloaded_paths=[])

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed. Run: playwright install chromium") from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)

            candidates = self._extract_candidates(page, url)
            cmr_found = contains_cmr(candidates, self.cmr_keyword)
            if self.dry_run or not cmr_found:
                browser.close()
                return DownloadResult(
                    scanned_candidates=len(candidates),
                    cmr_found=cmr_found,
                    downloaded_paths=[],
                )

            for candidate in candidates:
                if should_download is not None and not should_download(candidate):
                    LOGGER.info("Skipping duplicate download candidate hint=%s href=%s", candidate.filename_hint, candidate.href)
                    continue
                file_name = self._safe_name(candidate.filename_hint, candidate.index)
                target_path = output_dir / file_name
                if target_path.exists():
                    target_path.unlink()

                locator = page.locator("a").nth(candidate.index)
                try:
                    locator.scroll_into_view_if_needed(timeout=5000)
                    with page.expect_download(timeout=10000) as event:
                        locator.click(timeout=5000)
                    download = event.value
                    download.save_as(str(target_path))
                    normalized = target_path
                    if target_path.suffix.lower() in IMAGE_EXTENSIONS:
                        normalized = self._convert_image_file_to_pdf(target_path)
                    downloaded.append(normalized)
                    continue
                except PlaywrightTimeoutError:
                    LOGGER.info("Download click timed out, fallback to HTTP href=%s", candidate.href)

                response = context.request.get(candidate.href, timeout=15000)
                if response.ok:
                    normalized = self._write_http_body(
                        target_path=target_path,
                        body=response.body(),
                        content_type=(response.headers.get("content-type", "").lower()),
                    )
                    downloaded.append(normalized)
                else:
                    LOGGER.warning("Skipping failed fallback href=%s status=%s", candidate.href, response.status)

            browser.close()

            return DownloadResult(
                scanned_candidates=len(candidates),
                cmr_found=cmr_found,
                downloaded_paths=downloaded,
            )
