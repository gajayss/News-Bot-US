from __future__ import annotations

import time
from collections import deque
from typing import Any


class OrderRateLimiter:
    """Prevents order floods: max N orders per window, cooldown per symbol."""

    def __init__(
        self,
        max_orders_per_minute: int = 5,
        symbol_cooldown_sec: float = 60.0,
    ) -> None:
        self.max_orders_per_minute = max_orders_per_minute
        self.symbol_cooldown_sec = symbol_cooldown_sec
        self._order_timestamps: deque[float] = deque()
        self._symbol_last_order: dict[str, float] = {}

    def _prune_old(self) -> None:
        cutoff = time.time() - 60.0
        while self._order_timestamps and self._order_timestamps[0] < cutoff:
            self._order_timestamps.popleft()

    def check(self, symbol: str) -> dict[str, Any] | None:
        """Return None if allowed, or a dict with rejection reason."""
        now = time.time()
        self._prune_old()

        if len(self._order_timestamps) >= self.max_orders_per_minute:
            return {
                "rejected": True,
                "reason": f"rate_limit: {self.max_orders_per_minute} orders/min exceeded",
                "wait_sec": round(60.0 - (now - self._order_timestamps[0]), 1),
            }

        last = self._symbol_last_order.get(symbol)
        if last is not None:
            elapsed = now - last
            if elapsed < self.symbol_cooldown_sec:
                return {
                    "rejected": True,
                    "reason": f"symbol_cooldown: {symbol} ordered {elapsed:.0f}s ago, cooldown={self.symbol_cooldown_sec}s",
                    "wait_sec": round(self.symbol_cooldown_sec - elapsed, 1),
                }

        return None

    def record(self, symbol: str) -> None:
        """Record that an order was placed for this symbol."""
        now = time.time()
        self._order_timestamps.append(now)
        self._symbol_last_order[symbol] = now
