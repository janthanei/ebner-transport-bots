from __future__ import annotations

import hashlib
from pathlib import Path


def fingerprint_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fingerprint_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
