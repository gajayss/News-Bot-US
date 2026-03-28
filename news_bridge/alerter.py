from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger("alerter")


class TelegramAlerter:
    """Optional Telegram notification. Silently no-ops if not configured."""

    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        self._session = requests.Session() if self.enabled else None

    def send(self, message: str) -> None:
        if not self.enabled or self._session is None:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            self._session.post(
                url,
                json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)

    def notify_order(self, result: dict[str, Any]) -> None:
        status = result.get("status", "UNKNOWN")
        symbol = result.get("symbol") or result.get("underlying", "?")
        side = result.get("side", "?")
        broker = result.get("broker", "?")
        emoji = "\u2705" if status in {"SENT", "SIMULATED"} else "\u274c"
        msg = f"{emoji} <b>[{broker}]</b> {side} {symbol} → {status}"
        reason = result.get("reason", "")
        if reason:
            msg += f"\n{reason}"
        self.send(msg)

    def notify_rate_limited(self, symbol: str, reason: str) -> None:
        self.send(f"\u26a0\ufe0f <b>Rate Limited</b> {symbol}: {reason}")
