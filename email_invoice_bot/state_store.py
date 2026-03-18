from __future__ import annotations

import json
from pathlib import Path


class StateStore:
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self._processed_keys: set[str] = set()
        self._dirty = False

    @staticmethod
    def build_key(uid: str, message_id: str | None) -> str:
        if message_id:
            return f"{uid}:{message_id.strip().lower()}"
        return f"{uid}:no-message-id"

    def load(self) -> None:
        if not self.state_file.exists():
            return
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        keys = data.get("processed_keys", [])
        self._processed_keys = set(keys)

    def has(self, key: str) -> bool:
        return key in self._processed_keys

    def add(self, key: str) -> None:
        if key in self._processed_keys:
            return
        self._processed_keys.add(key)
        self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        payload = {"processed_keys": sorted(self._processed_keys)}
        temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_file.replace(self.state_file)
        self._dirty = False

