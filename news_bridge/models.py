from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class NewsEvent:
    event_id: str = field(default_factory=lambda: uuid4().hex)
    source: str = "unknown"
    source_news_id: str = ""
    headline: str = ""
    summary: str = ""
    url: str = ""
    published_at: str = field(default_factory=utc_now_iso)
    symbols: list[str] = field(default_factory=list)
    event_type: str = "GENERAL"
    direction: str = "NEUTRAL"
    score: float = 0.0
    confidence: float = 0.0
    urgency: float = 0.0
    horizon: str = "INTRADAY"
    tradable: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TradeSignal:
    signal_id: str = field(default_factory=lambda: uuid4().hex)
    event_id: str = ""
    asset_class: str = "STOCK"
    symbol: str = ""
    side: str = "BUY"
    strength: float = 0.0
    confidence: float = 0.0
    urgency: float = 0.0
    reason: str = ""
    event_type: str = "GENERAL"
    option_expiry_type: str = "MONTHLY"
    option_right: str = "CALL"
    reference_price: float = 0.0
    qty: int = 1
    created_at: str = field(default_factory=utc_now_iso)
    option_plan: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExecutionReport:
    report_id: str = field(default_factory=lambda: uuid4().hex)
    signal_id: str = ""
    broker: str = ""
    symbol: str = ""
    status: str = "UNKNOWN"
    detail: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
