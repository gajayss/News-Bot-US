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

    # ------------------------------------------------------------------
    # 선분이력(Slowly Changing Dimension) 시그널 관리
    # ------------------------------------------------------------------
    _OPEN_SENTINEL = "9999-12-31T23:59:59"
    _SIGNALS_FILE = "signals_store"  # 날짜 초기화 없는 영구 파일

    def _ensure_signals_store(self) -> None:
        """signals_store.json 초기화 (날짜 초기화 없이 영구 유지)."""
        path = self.interface_dir / f"{self._SIGNALS_FILE}.json"
        if not path.exists():
            path.write_text(
                json.dumps({"items": []}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def upsert_signal(self, item: dict[str, Any]) -> str:
        """선분이력 기반 시그널 upsert.

        동일 (symbol, side, asset_class) 의 열린 시그널이 존재하면
        strength / reason / confidence / urgency / qty 만 업데이트.
        없으면 신규 insert.

        Returns:
            "updated" | "inserted"
        """
        self._ensure_signals_store()
        path = self.interface_dir / f"{self._SIGNALS_FILE}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        items: list[dict[str, Any]] = payload.setdefault("items", [])

        symbol     = item.get("symbol", "")
        side       = item.get("side", "")
        asset_cls  = item.get("asset_class", "")

        # 열린 시그널(expired_at == sentinel) 탐색
        for existing in items:
            if (
                existing.get("symbol") == symbol
                and existing.get("side") == side
                and existing.get("asset_class") == asset_cls
                and existing.get("expired_at", self._OPEN_SENTINEL) == self._OPEN_SENTINEL
            ):
                # 강도·사유 변경분만 업데이트 (선분이력 유지)
                changed = False
                for fld in ("strength", "reason", "confidence", "urgency", "qty", "option_plan"):
                    if fld in item and item[fld] != existing.get(fld):
                        existing[fld] = item[fld]
                        changed = True
                if changed:
                    path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                return "updated"

        # 신규 insert
        items.append(item)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return "inserted"

    def expire_signal(
        self,
        symbol: str,
        side: str,
        asset_class: str,
        expired_at: str,
    ) -> bool:
        """열린 시그널을 종료 처리 (expired_at 업데이트).

        Returns:
            True if any signal was expired, False otherwise.
        """
        self._ensure_signals_store()
        path = self.interface_dir / f"{self._SIGNALS_FILE}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        items: list[dict[str, Any]] = payload.get("items", [])
        changed = False
        for existing in items:
            if (
                existing.get("symbol") == symbol
                and existing.get("side") == side
                and existing.get("asset_class") == asset_class
                and existing.get("expired_at", self._OPEN_SENTINEL) == self._OPEN_SENTINEL
            ):
                existing["expired_at"] = expired_at
                changed = True
        if changed:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return changed

    def read_signals(self) -> list[dict[str, Any]]:
        """signals_store의 모든 시그널 반환."""
        self._ensure_signals_store()
        path = self.interface_dir / f"{self._SIGNALS_FILE}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("items", []))

    def backfill_from_daily(self) -> int:
        """기동 시 daily 파일(stock/option_signals) → signals_store 마이그레이션.

        signals_store에 없는 시그널만 insert (upsert 중복 제거).
        재기동 후에도 즉시 이력 표시 가능하도록 보장.

        Returns:
            삽입된 신규 건수.
        """
        inserted = 0
        for fname in ("stock_signals", "option_signals"):
            path = self._active_path(fname)
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                for item in payload.get("items", []):
                    # signal_id 기반 정확한 중복 체크 (upsert는 symbol/side/class 기준이라
                    # 같은 symbol이라도 다른 이벤트면 신규 insert 해야 함)
                    if self._upsert_by_signal_id(item) == "inserted":
                        inserted += 1
            except Exception:
                continue
        return inserted

    def _upsert_by_signal_id(self, item: dict[str, Any]) -> str:
        """signal_id 기준 중복 없이 insert. signals_store 전용 내부 메서드."""
        self._ensure_signals_store()
        path = self.interface_dir / f"{self._SIGNALS_FILE}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        items: list[dict[str, Any]] = payload.setdefault("items", [])

        sig_id = item.get("signal_id", "")
        if sig_id:
            if any(e.get("signal_id") == sig_id for e in items):
                return "exists"  # 이미 있음 — 스킵

        items.append(item)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return "inserted"

    def set_consumer_offset(self, consumer_name: str, offset: int) -> None:
        path = self._active_path("consumer_state")
        state = self.get_consumer_state()
        state.setdefault("offsets", {})[consumer_name] = offset
        payload = {"date": self._today_key(), "items": [state]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
