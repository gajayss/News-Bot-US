from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class DailyJsonBus:
    def __init__(self, interface_dir: Path, log_dir: Path) -> None:
        self.interface_dir = interface_dir
        self.log_dir = log_dir
        self.interface_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _today_key() -> str:
        return datetime.now().strftime("%Y%m%d")

    def _active_path(self, name: str) -> Path:
        return self.interface_dir / f"{name}.json"

    def _log_path(self, name: str) -> Path:
        return self.log_dir / f"{name}_{self._today_key()}.log"

    def _ensure_active_file(self, name: str) -> None:
        path = self._active_path(name)
        if not path.exists():
            path.write_text(json.dumps({"date": self._today_key(), "items": []}, ensure_ascii=False, indent=2), encoding="utf-8")
            return

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {"date": self._today_key(), "items": []}

        if payload.get("date") != self._today_key():
            path.write_text(json.dumps({"date": self._today_key(), "items": []}, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_items(self, name: str) -> list[dict[str, Any]]:
        self._ensure_active_file(name)
        path = self._active_path(name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("items", []))

    def append_item(self, name: str, item: dict[str, Any]) -> None:
        self._ensure_active_file(name)
        path = self._active_path(name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("items", []).append(item)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._log_path(name).open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(item, ensure_ascii=False) + "\n")

    def get_consumer_state(self) -> dict[str, Any]:
        path = self._active_path("consumer_state")
        if not path.exists():
            data = {"date": self._today_key(), "items": [{"offsets": {}}]}
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("date") != self._today_key():
            payload = {"date": self._today_key(), "items": [{"offsets": {}}]}
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        items = payload.get("items", [])
        if not items:
            payload["items"] = [{"offsets": {}}]
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload["items"][0]

    def set_consumer_offset(self, consumer_name: str, offset: int) -> None:
        path = self._active_path("consumer_state")
        state = self.get_consumer_state()
        state.setdefault("offsets", {})[consumer_name] = offset
        payload = {"date": self._today_key(), "items": [state]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
