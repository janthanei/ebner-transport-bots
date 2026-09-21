from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

from .config import AppConfig
from .print_ledger import PrintLedger
from .printnode_client import PrintNodeClient


def run() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = AppConfig.from_env()
    if not config.printnode_api_key or config.printnode_printer_id <= 0:
        raise RuntimeError("PrintNode configuration is required for history backfill")

    client = PrintNodeClient(config.printnode_api_key, config.printnode_printer_id)
    jobs = [
        job
        for job in client.list_printjobs()
        if (job.get("printer") or {}).get("id") == config.printnode_printer_id
        and job.get("source") == "ebner-invoice-bot"
    ]
    ledger = PrintLedger(Path("state/print_history.sqlite3"))
    imported = ledger.import_printnode_history(jobs)
    logging.info("Print history backfill complete fetched=%s imported=%s", len(jobs), imported)


if __name__ == "__main__":
    run()
