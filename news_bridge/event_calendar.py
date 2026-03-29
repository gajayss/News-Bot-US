"""Event calendar engine — 경제 이벤트 기반 포지션 사전 준비/축소/차단.

핵심 로직:
  1. 이벤트 발표 전 → 신규 진입 차단 또는 수량 축소
  2. 이벤트 발표 후 → 결과 방향에 따라 신호 부스트 또는 반전
  3. 이벤트 간 최소 간격 → 연속 이벤트 시 포지션 최소화
  4. 변곡 시점이 핵심 — 발표 시각 전후 2시간이 승부

별3개 (investing.com 기준) = Finnhub impact=high
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("event_calendar")

# -----------------------------------------------------------------------
# Event classification
# -----------------------------------------------------------------------

# 이벤트명 → 카테고리 매핑 (키워드 기반)
EVENT_CATEGORIES: dict[str, list[str]] = {
    "FOMC":       ["fomc", "interest rate decision", "fed funds", "fomc minutes"],
    "FED_SPEAK":  ["powell", "fed chair", "fed governor", "fed president",
                   "waller", "bowman", "barkin", "daly", "bostic", "kashkari",
                   "goolsbee", "harker", "mester", "williams", "logan",
                   "speaks", "speech", "testimony"],
    "CPI":        ["cpi", "consumer price"],
    "PCE":        ["pce", "personal consumption"],
    "NFP":        ["non-farm", "nonfarm", "payroll"],
    "GDP":        ["gdp", "gross domestic"],
    "EMPLOYMENT": ["jobless claims", "unemployment", "employment"],
    "ISM":        ["ism", "pmi", "manufacturing pmi", "services pmi"],
    "RETAIL":     ["retail sales"],
    "TRUMP":      ["trump", "president speaks", "executive order"],
    "EARNINGS":   ["earnings", "quarterly results", "q1", "q2", "q3", "q4",
                   "revenue", "eps", "guidance", "earnings call",
                   "nvda earnings", "tsla earnings", "aapl earnings",
                   "msft earnings", "amzn earnings", "meta earnings"],
}

# 카테고리별 시장 충격 등급
CATEGORY_IMPACT: dict[str, int] = {
    "FOMC":       5,   # 최고 — 금리 결정, 변곡점
    "NFP":        5,   # 최고 — 고용 서프라이즈
    "CPI":        4,   # 매우 높음
    "PCE":        4,   # 매우 높음 (연준 선호 지표)
    "TRUMP":      4,   # 예측 불가, 순간 변동
    "EARNINGS":   4,   # 매우 높음 — 어닝 서프라이즈/쇼크, 세력 장난질 주의
    "FED_SPEAK":  3,   # 높음 (매파/비둘기 시그널)
    "GDP":        3,
    "ISM":        3,
    "EMPLOYMENT": 2,   # 중간
    "RETAIL":     2,
}

# 충격 등급별 사전 조치
# impact_level → (hours_before_block, qty_reduction_pct, hold_reduction_pct)
PRE_EVENT_RULES: dict[int, tuple[float, float, float]] = {
    5: (4.0, 1.00, 0.50),   # 4시간 전부터 신규 진입 차단, 보유기간 50% 축소
    4: (3.0, 0.50, 0.30),   # 3시간 전, 수량 50% 축소, 보유기간 30% 축소
    3: (2.0, 0.30, 0.20),   # 2시간 전, 수량 30% 축소, 보유기간 20% 축소
    2: (1.0, 0.00, 0.00),   # 1시간 전, 경고만
}


def classify_event(event_name: str) -> str:
    """Classify event name into category."""
    lowered = event_name.lower()
    for category, keywords in EVENT_CATEGORIES.items():
        if any(k in lowered for k in keywords):
            return category
    return "OTHER"


def get_impact_level(event: dict[str, Any]) -> int:
    """Get numeric impact level (1-5) for an event."""
    category = classify_event(str(event.get("event_name", "")))
    base_impact = CATEGORY_IMPACT.get(category, 1)

    # Finnhub impact field as fallback/boost
    raw_impact = str(event.get("impact", "")).lower()
    if raw_impact == "high" and base_impact < 3:
        base_impact = 3
    elif raw_impact == "low" and base_impact > 2:
        base_impact = max(2, base_impact - 1)

    return min(5, base_impact)


# -----------------------------------------------------------------------
# Calendar state — upcoming events awareness
# -----------------------------------------------------------------------

class EventCalendarState:
    """Maintains awareness of upcoming economic events and their effect on trading."""

    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        pre_event_block_hours: float = 4.0,
        post_event_boost_hours: float = 2.0,
    ) -> None:
        self.events: list[dict[str, Any]] = []
        self.pre_event_block_hours = pre_event_block_hours
        self.post_event_boost_hours = post_event_boost_hours
        if events:
            self.load_events(events)

    def load_events(self, events: list[dict[str, Any]]) -> None:
        """Load and enrich events with parsed datetime and impact."""
        self.events = []
        for ev in events:
            enriched = dict(ev)
            enriched["category"] = classify_event(str(ev.get("event_name", "")))
            enriched["impact_level"] = get_impact_level(ev)
            enriched["event_dt"] = self._parse_event_dt(ev)
            self.events.append(enriched)

        # Sort by datetime
        self.events.sort(key=lambda e: e.get("event_dt") or datetime.max.replace(tzinfo=timezone.utc))
        logger.info("Loaded %d calendar events", len(self.events))

    def get_upcoming(self, hours_ahead: float = 24.0) -> list[dict[str, Any]]:
        """Get events happening within the next N hours."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)
        return [
            ev for ev in self.events
            if ev.get("event_dt") and now <= ev["event_dt"] <= cutoff
        ]

    def get_active_constraints(self) -> dict[str, Any]:
        """Check if any upcoming event should constrain current trading.

        Returns a dict describing the most restrictive active constraint:
        {
            "constrained": bool,
            "action": "BLOCK" | "REDUCE" | "WARN" | "NONE",
            "event_name": str,
            "category": str,
            "impact_level": int,
            "hours_until": float,
            "qty_reduction_pct": float,   # 0.0~1.0 (1.0 = full block)
            "hold_reduction_pct": float,
            "reason": str,
        }
        """
        now = datetime.now(timezone.utc)
        most_restrictive = self._no_constraint()

        for ev in self.events:
            event_dt = ev.get("event_dt")
            if not event_dt:
                continue

            hours_until = (event_dt - now).total_seconds() / 3600.0

            # Skip past events (but check post-event window)
            if hours_until < -self.post_event_boost_hours:
                continue

            # Post-event window: no constraint, but mark as recently occurred
            if hours_until < 0:
                continue

            impact = ev.get("impact_level", 1)
            rule = PRE_EVENT_RULES.get(impact)
            if not rule:
                continue

            block_hours, qty_red, hold_red = rule

            if hours_until <= block_hours:
                # This event is within the pre-event window
                if qty_red >= 1.0:
                    action = "BLOCK"
                elif qty_red > 0:
                    action = "REDUCE"
                else:
                    action = "WARN"

                # Keep the most restrictive constraint
                if qty_red > most_restrictive.get("qty_reduction_pct", 0):
                    most_restrictive = {
                        "constrained": True,
                        "action": action,
                        "event_name": ev.get("event_name", ""),
                        "category": ev.get("category", ""),
                        "impact_level": impact,
                        "hours_until": round(hours_until, 1),
                        "event_time": event_dt.strftime("%Y-%m-%d %H:%M UTC"),
                        "qty_reduction_pct": qty_red,
                        "hold_reduction_pct": hold_red,
                        "reason": self._build_reason(ev, action, hours_until),
                    }

        return most_restrictive

    def get_event_summary(self) -> list[dict[str, Any]]:
        """Return simplified event list for logging/display."""
        now = datetime.now(timezone.utc)
        result = []
        for ev in self.events:
            event_dt = ev.get("event_dt")
            if not event_dt:
                continue
            hours_until = (event_dt - now).total_seconds() / 3600.0
            if hours_until < -24:
                continue  # skip old
            result.append({
                "event_name": ev.get("event_name", ""),
                "category": ev.get("category", ""),
                "impact_level": ev.get("impact_level", 0),
                "date": ev.get("date", ""),
                "time": ev.get("time", ""),
                "hours_until": round(hours_until, 1),
                "status": "PAST" if hours_until < 0 else "UPCOMING",
                "actual":   ev.get("actual"),    # 실제값 (ACT)
                "estimate": ev.get("estimate"),  # 예측값 (FORECAST)
                "prev":     ev.get("prev"),       # 직전값 (PREV)
                "unit":     ev.get("unit", ""),  # 단위 (%, K 등)
            })
        return result

    @staticmethod
    def _no_constraint() -> dict[str, Any]:
        return {
            "constrained": False,
            "action": "NONE",
            "event_name": "",
            "category": "",
            "impact_level": 0,
            "hours_until": 999,
            "event_time": "",
            "qty_reduction_pct": 0.0,
            "hold_reduction_pct": 0.0,
            "reason": "",
        }

    @staticmethod
    def _parse_event_dt(ev: dict[str, Any]) -> datetime | None:
        date_str = str(ev.get("date", "")).strip()
        time_str = str(ev.get("time", "")).strip()
        if not date_str:
            return None
        try:
            if time_str and ":" in time_str:
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            else:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _build_reason(ev: dict[str, Any], action: str, hours_until: float) -> str:
        name = ev.get("event_name", "")
        cat = ev.get("category", "")
        impact = ev.get("impact_level", 0)
        time_str = ev.get("time", "")

        if action == "BLOCK":
            return (
                f"[BLOCK] {name} ({cat}, 충격{impact}) 발표 {hours_until:.1f}시간 전 "
                f"→ 신규 옵션 진입 차단. 발표시각: {time_str} UTC"
            )
        elif action == "REDUCE":
            return (
                f"[REDUCE] {name} ({cat}, 충격{impact}) 발표 {hours_until:.1f}시간 전 "
                f"→ 수량 축소 + 보유기간 단축. 발표시각: {time_str} UTC"
            )
        else:
            return (
                f"[WARN] {name} ({cat}, 충격{impact}) 발표 {hours_until:.1f}시간 전 "
                f"→ 주의. 발표시각: {time_str} UTC"
            )
