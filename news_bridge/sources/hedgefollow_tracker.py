"""HedgeFollow Tracker — 헤지펀드 13F/13G/13D 실시간 추적.

hedgefollow.com/hedge-fund-tracker 한 페이지에서
전체 헤지펀드의 최신 포지션 변동을 일자별로 파싱.

개별 종목 페이지 7개 접속 → 이 1페이지로 축소.
Cloudflare 보호 → Chrome MCP 필요.

사용법 (Chrome MCP):
    1. Chrome MCP로 hedgefollow.com/hedge-fund-tracker 접속
    2. javascript_tool로 테이블 데이터 추출
    3. parse_tracker_data()로 워치리스트 매도/축소 감지
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("hedgefollow_tracker")


def parse_tracker_data(
    rows_data: list[dict[str, str]],
    watchlist: list[str] | None = None,
    min_reduce_pct: float = -5.0,
    max_age_days: int = 30,
) -> list[dict[str, Any]]:
    """Chrome MCP javascript_tool로 추출한 tracker 테이블 데이터 파싱.

    rows_data: [{"fund": "...", "ticker": "...", "pct_change": "-12.5%",
                 "shares_change": "(-31.6k)", "filing_date": "2026-03-27", ...}, ...]

    워치리스트 종목 중 min_reduce_pct 이하로 축소한 것만 경고.

    Returns: classify_news()용 synthetic news dict 리스트
    """
    cutoff = datetime.now() - timedelta(days=max_age_days)
    alerts: list[dict[str, Any]] = []
    seen_funds: set[str] = set()

    for row in rows_data:
        ticker = row.get("ticker", "").strip().upper()
        if not ticker:
            continue

        # 워치리스트 필터
        if watchlist and ticker not in watchlist:
            continue

        fund = row.get("fund", "Unknown").strip()
        pct_str = row.get("pct_change", "0").strip()
        shares_change = row.get("shares_change", "").strip()
        filing_date = row.get("filing_date", "").strip()

        # 날짜 필터 (최근 max_age_days 이내)
        if filing_date:
            try:
                dt = datetime.strptime(filing_date[:10], "%Y-%m-%d")
                if dt < cutoff:
                    continue
            except ValueError:
                pass

        # 포지션 변동 파싱
        delta_pct = _parse_pct(pct_str)

        # NEW 포지션은 스킵
        if "NEW" in pct_str.upper() or "NEW" in shares_change.upper():
            continue

        # 축소 필터
        if delta_pct > min_reduce_pct:
            continue

        # 중복 제거 (같은 펀드+종목)
        dedup_key = f"{fund}_{ticker}"
        if dedup_key in seen_funds:
            continue
        seen_funds.add(dedup_key)

        action = f"REDUCED {delta_pct:.1f}%"

        alerts.append({
            "id": f"hft_{ticker}_{fund[:15]}_{filing_date[:10] if filing_date else datetime.now().strftime('%Y%m%d')}".replace(" ", "_"),
            "source": "HedgeFollow 13F",
            "headline": (
                f"{ticker}: hedge fund {fund} {action} position"
            ),
            "summary": (
                f"HedgeFollow tracker: {fund} reduced {ticker} position by {delta_pct:.1f}% "
                f"({shares_change}). Filing date: {filing_date}. "
                f"헤지펀드 포지션 축소 감지."
            ),
            "url": "https://hedgefollow.com/hedge-fund-tracker",
            "datetime": filing_date or datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "_meta": {
                "ticker": ticker,
                "fund": fund,
                "action": action,
                "delta_pct": delta_pct,
                "shares_change": shares_change,
                "filing_date": filing_date,
            },
        })

    logger.info("HedgeFollow tracker: %d significant reductions for watchlist", len(alerts))
    return alerts


def parse_tracker_raw_text(
    raw_rows: list[str],
    watchlist: list[str] | None = None,
    min_reduce_pct: float = -5.0,
) -> list[dict[str, Any]]:
    """Chrome MCP에서 추출한 raw text 행을 파싱.

    raw_rows: 각 행의 텍스트 (파이프 구분 또는 공백)
    예: "Japan Post Holdings Co Ltd | AFL | 100.00% | -0.54% (-281k) | ... | 2026-03-25"

    Returns: classify_news()용 synthetic news dict 리스트
    """
    alerts: list[dict[str, Any]] = []
    seen: set[str] = set()
    wl_set = set(watchlist) if watchlist else None

    for raw in raw_rows:
        raw = raw.strip()
        if not raw or len(raw) < 20:
            continue

        # 티커 추출 (2~5글자 대문자)
        ticker_candidates = re.findall(r'\b([A-Z]{2,5})\b', raw)
        ticker = None
        skip_words = {"NEW", "USD", "INC", "LLC", "ETF", "THE", "BUY", "SELL", "ADD", "LTD", "CEO", "CFO"}
        for c in ticker_candidates:
            if c not in skip_words:
                ticker = c
                break

        if not ticker:
            continue

        if wl_set and ticker not in wl_set:
            continue

        # 퍼센트 변동 추출 — "(±NNNk)" 앞의 퍼센트
        pct_with_shares = re.search(r'([-+]?\d+\.?\d*)%\s*\([^)]*[kKmM]?\)', raw)
        if not pct_with_shares:
            continue

        delta_pct = float(pct_with_shares.group(1))

        if "NEW" in raw and "+" in raw:
            continue

        if delta_pct > min_reduce_pct:
            continue

        # 펀드명 추출 (줄 앞부분)
        parts = raw.split("|")
        fund = parts[0].strip() if parts else raw[:40]
        fund = re.sub(r'^\s*\|\s*', '', fund).strip()
        if not fund or len(fund) < 3:
            fund = "Unknown Fund"

        # 날짜 추출
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', raw)
        filing_date = date_match.group(1) if date_match else ""

        dedup_key = f"{fund[:20]}_{ticker}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        alerts.append({
            "id": f"hft_{ticker}_{fund[:15]}_{filing_date or datetime.now().strftime('%Y%m%d')}".replace(" ", "_"),
            "source": "HedgeFollow 13F",
            "headline": f"{ticker}: hedge fund {fund} REDUCED {delta_pct:.1f}% position",
            "summary": (
                f"HedgeFollow tracker: {fund} reduced {ticker} position by {delta_pct:.1f}%. "
                f"Filing: {filing_date}. 헤지펀드 포지션 축소 감지."
            ),
            "url": "https://hedgefollow.com/hedge-fund-tracker",
            "datetime": filing_date or datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "_meta": {
                "ticker": ticker,
                "fund": fund,
                "action": f"REDUCED {delta_pct:.1f}%",
                "delta_pct": delta_pct,
            },
        })

    logger.info("HedgeFollow tracker raw: %d alerts parsed", len(alerts))
    return alerts


# Chrome MCP용 JavaScript 코드 (복사해서 사용)
TRACKER_JS_EXTRACT = """
new Promise(resolve => {
  setTimeout(() => {
    const rows = document.querySelectorAll('table tbody tr');
    const results = [];
    rows.forEach(row => {
      const cells = row.querySelectorAll('td');
      if (cells.length < 5) return;
      const texts = Array.from(cells).map(c => c.textContent.trim());
      results.push(texts.join(' | '));
    });
    resolve(JSON.stringify(results));
  }, 4000);
});
"""


def _parse_pct(text: str) -> float:
    """'+45.77%' or '-6.65%' 파싱."""
    match = re.search(r'([-+]?\d+\.?\d*)', text)
    if match:
        return float(match.group(1))
    return 0.0
