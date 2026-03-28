"""Economic calendar source — Finnhub economic calendar API + investing.com 별3개급 이벤트.

핵심 이벤트:
  - FOMC 금리결정 / FOMC Minutes
  - CPI, Core CPI
  - PCE, Core PCE
  - Non-Farm Payrolls (고용지표)
  - Fed 위원 연설 (매파/비둘기)
  - 트럼프 소셜/인터뷰 (수동 또는 뉴스 기반)
  - GDP, ISM, Retail Sales, Jobless Claims

Finnhub free tier: /calendar/economic 엔드포인트 사용.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger("calendar_source")

# -----------------------------------------------------------------------
# Finnhub economic calendar
# -----------------------------------------------------------------------

def fetch_finnhub_calendar(api_key: str, days_ahead: int = 7) -> list[dict[str, Any]]:
    """Fetch upcoming US economic events from Finnhub."""
    today = datetime.now(timezone.utc).date()
    from_date = today.isoformat()
    to_date = (today + timedelta(days=days_ahead)).isoformat()

    url = "https://finnhub.io/api/v1/calendar/economic"
    try:
        resp = requests.get(
            url,
            params={"from": from_date, "to": to_date, "token": api_key},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Finnhub calendar fetch failed: %s", exc)
        return []

    raw_events = data.get("economicCalendar", [])
    if not isinstance(raw_events, list):
        return []

    # Filter US events only, normalize
    result: list[dict[str, Any]] = []
    for ev in raw_events:
        country = str(ev.get("country", "")).upper()
        if country not in {"US", "USA", "UNITED STATES"}:
            continue
        result.append({
            "event_name": str(ev.get("event", "")),
            "country": "US",
            "date": str(ev.get("date", "")),
            "time": str(ev.get("time", "")),
            "impact": str(ev.get("impact", "low")),        # low/medium/high
            "actual": ev.get("actual"),
            "estimate": ev.get("estimate"),
            "prev": ev.get("prev"),
            "unit": str(ev.get("unit", "")),
            "source": "finnhub",
        })
    return result


# -----------------------------------------------------------------------
# Sample calendar (테스트용)
# -----------------------------------------------------------------------

def fetch_sample_calendar() -> list[dict[str, Any]]:
    """Return sample high-impact US economic events for testing."""
    today = datetime.now(timezone.utc)
    tomorrow = today + timedelta(days=1)
    day2 = today + timedelta(days=2)
    day3 = today + timedelta(days=3)
    day5 = today + timedelta(days=5)

    def _d(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d")

    return [
        {
            "event_name": "FOMC Interest Rate Decision",
            "country": "US",
            "date": _d(tomorrow),
            "time": "14:00",
            "impact": "high",
            "actual": None,
            "estimate": 4.50,
            "prev": 4.75,
            "unit": "%",
            "source": "sample",
        },
        {
            "event_name": "Fed Chair Powell Press Conference",
            "country": "US",
            "date": _d(tomorrow),
            "time": "14:30",
            "impact": "high",
            "actual": None,
            "estimate": None,
            "prev": None,
            "unit": "",
            "source": "sample",
        },
        {
            "event_name": "Core PCE Price Index (MoM)",
            "country": "US",
            "date": _d(day2),
            "time": "08:30",
            "impact": "high",
            "actual": None,
            "estimate": 0.3,
            "prev": 0.4,
            "unit": "%",
            "source": "sample",
        },
        {
            "event_name": "Non-Farm Payrolls",
            "country": "US",
            "date": _d(day3),
            "time": "08:30",
            "impact": "high",
            "actual": None,
            "estimate": 185,
            "prev": 227,
            "unit": "K",
            "source": "sample",
        },
        {
            "event_name": "CPI (YoY)",
            "country": "US",
            "date": _d(day5),
            "time": "08:30",
            "impact": "high",
            "actual": None,
            "estimate": 2.8,
            "prev": 2.9,
            "unit": "%",
            "source": "sample",
        },
        {
            "event_name": "Fed Governor Waller Speaks",
            "country": "US",
            "date": _d(tomorrow),
            "time": "10:00",
            "impact": "medium",
            "actual": None,
            "estimate": None,
            "prev": None,
            "unit": "",
            "source": "sample",
        },
        {
            "event_name": "Initial Jobless Claims",
            "country": "US",
            "date": _d(day2),
            "time": "08:30",
            "impact": "medium",
            "actual": None,
            "estimate": 220,
            "prev": 215,
            "unit": "K",
            "source": "sample",
        },
        {
            "event_name": "ISM Manufacturing PMI",
            "country": "US",
            "date": _d(day3),
            "time": "10:00",
            "impact": "high",
            "actual": None,
            "estimate": 49.5,
            "prev": 50.3,
            "unit": "",
            "source": "sample",
        },
    ]
