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
    """Return sample high-impact US economic events — 다음달 말일까지 커버."""
    import calendar as _cal
    from datetime import date as _date

    today_dt = datetime.now(timezone.utc)

    def _d(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d")

    def _add(days: int) -> datetime:
        return today_dt + timedelta(days=days)

    # 다음달 말일 계산
    today_date = today_dt.date()
    next_month = today_date.month % 12 + 1
    next_year = today_date.year + (1 if today_date.month == 12 else 0)
    last_day = _cal.monthrange(next_year, next_month)[1]
    next_month_end = datetime(next_year, next_month, last_day, 23, 59, tzinfo=timezone.utc)

    # 고정 이벤트 목록 (날짜 상대 오프셋)
    events = [
        # --- 이번주 ---
        {"event_name": "Fed Governor Waller Speaks",        "date": _d(_add(1)),  "time": "10:00", "impact": "medium", "estimate": None, "prev": None, "unit": ""},
        {"event_name": "FOMC Interest Rate Decision",        "date": _d(_add(1)),  "time": "14:00", "impact": "high",   "estimate": 4.50, "prev": 4.75, "unit": "%"},
        {"event_name": "Fed Chair Powell Press Conference",  "date": _d(_add(1)),  "time": "14:30", "impact": "high",   "estimate": None, "prev": None, "unit": ""},
        {"event_name": "Core PCE Price Index (MoM)",         "date": _d(_add(2)),  "time": "08:30", "impact": "high",   "estimate": 0.30, "prev": 0.40, "unit": "%"},
        {"event_name": "Initial Jobless Claims",             "date": _d(_add(2)),  "time": "08:30", "impact": "medium", "estimate": 220,  "prev": 215,  "unit": "K"},
        {"event_name": "Non-Farm Payrolls",                  "date": _d(_add(3)),  "time": "08:30", "impact": "high",   "estimate": 185,  "prev": 227,  "unit": "K"},
        {"event_name": "ISM Manufacturing PMI",              "date": _d(_add(3)),  "time": "10:00", "impact": "high",   "estimate": 49.5, "prev": 50.3, "unit": ""},
        {"event_name": "CPI (YoY)",                          "date": _d(_add(5)),  "time": "08:30", "impact": "high",   "estimate": 2.80, "prev": 2.90, "unit": "%"},
        # --- 다음달 주요 이벤트 ---
        {"event_name": "Initial Jobless Claims",             "date": _d(_add(9)),  "time": "08:30", "impact": "medium", "estimate": 218,  "prev": 220,  "unit": "K"},
        {"event_name": "PPI (MoM)",                          "date": _d(_add(10)), "time": "08:30", "impact": "high",   "estimate": 0.20, "prev": 0.10, "unit": "%"},
        {"event_name": "Retail Sales (MoM)",                 "date": _d(_add(12)), "time": "08:30", "impact": "high",   "estimate": 0.30, "prev": -0.90,"unit": "%"},
        {"event_name": "ISM Services PMI",                   "date": _d(_add(12)), "time": "10:00", "impact": "high",   "estimate": 53.0, "prev": 53.5, "unit": ""},
        {"event_name": "Fed Beige Book",                     "date": _d(_add(14)), "time": "14:00", "impact": "medium", "estimate": None, "prev": None, "unit": ""},
        {"event_name": "Initial Jobless Claims",             "date": _d(_add(16)), "time": "08:30", "impact": "medium", "estimate": 215,  "prev": 218,  "unit": "K"},
        {"event_name": "Philadelphia Fed Manufacturing",     "date": _d(_add(16)), "time": "08:30", "impact": "medium", "estimate": 8.5,  "prev": 12.5, "unit": ""},
        {"event_name": "Existing Home Sales",                "date": _d(_add(17)), "time": "10:00", "impact": "medium", "estimate": 3.95, "prev": 4.26, "unit": "M"},
        {"event_name": "Fed Governor Bowman Speaks",         "date": _d(_add(19)), "time": "09:00", "impact": "medium", "estimate": None, "prev": None, "unit": ""},
        {"event_name": "S&P Global Manufacturing PMI",       "date": _d(_add(21)), "time": "09:45", "impact": "medium", "estimate": 51.8, "prev": 52.5, "unit": ""},
        {"event_name": "New Home Sales",                     "date": _d(_add(22)), "time": "10:00", "impact": "medium", "estimate": 680,  "prev": 657,  "unit": "K"},
        {"event_name": "Durable Goods Orders (MoM)",         "date": _d(_add(23)), "time": "08:30", "impact": "high",   "estimate": 2.00, "prev": -1.10,"unit": "%"},
        {"event_name": "Initial Jobless Claims",             "date": _d(_add(23)), "time": "08:30", "impact": "medium", "estimate": 216,  "prev": 215,  "unit": "K"},
        {"event_name": "GDP (QoQ)",                          "date": _d(_add(24)), "time": "08:30", "impact": "high",   "estimate": 2.30, "prev": 2.40, "unit": "%"},
        {"event_name": "Core PCE Price Index (MoM)",         "date": _d(_add(25)), "time": "08:30", "impact": "high",   "estimate": 0.30, "prev": 0.30, "unit": "%"},
        {"event_name": "University of Michigan Sentiment",   "date": _d(_add(25)), "time": "10:00", "impact": "medium", "estimate": 57.0, "prev": 57.9, "unit": ""},
        {"event_name": "Fed Chair Powell Speaks",            "date": _d(_add(27)), "time": "13:00", "impact": "high",   "estimate": None, "prev": None, "unit": ""},
        {"event_name": "Non-Farm Payrolls",                  "date": _d(_add(32)), "time": "08:30", "impact": "high",   "estimate": 190,  "prev": 185,  "unit": "K"},
        {"event_name": "ISM Manufacturing PMI",              "date": _d(_add(32)), "time": "10:00", "impact": "high",   "estimate": 49.8, "prev": 49.5, "unit": ""},
        {"event_name": "FOMC Meeting Minutes",               "date": _d(_add(35)), "time": "14:00", "impact": "high",   "estimate": None, "prev": None, "unit": ""},
    ]

    # 다음달 말일 이내 이벤트만 포함
    result = []
    for ev in events:
        try:
            ev_date = datetime.strptime(ev["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if ev_date <= next_month_end:
                result.append({
                    "event_name": ev["event_name"],
                    "country": "US",
                    "date": ev["date"],
                    "time": ev["time"],
                    "impact": ev["impact"],
                    "actual": None,
                    "estimate": ev.get("estimate"),
                    "prev": ev.get("prev"),
                    "unit": ev.get("unit", ""),
                    "source": "sample",
                })
        except ValueError:
            continue
    return result
