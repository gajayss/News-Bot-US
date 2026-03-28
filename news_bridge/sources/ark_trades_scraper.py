"""ARK Invest 매매 추적 — 캐시우드 일일 매매 감지.

cathiesark.com에서 ARK ETF의 일일 Buy/Sell 거래를 크롤링.
캐시우드가 워치리스트 종목을 매도하면 약세 신호.

핵심 규칙 (사용자 경험 기반):
  - 볼륨(금액)이 작으면 무시 — 큰 거래만 의미 있음
  - 2일 이상 연속 같은 방향(매수/매도)이면 강한 신호
  - TSLA, PLTR에서 저점매수/고점매도 거의 정확했음

cathiesark.com 테이블 구조:
  Date | Fund | Ticker | Direction | Market Value | % of Position | % of ETF

출력: classify_news()에 넣을 수 있는 synthetic news dict 리스트
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("ark_trades_scraper")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

_COMBINED_TRADES_URL = "https://cathiesark.com/ark-funds-combined/trades"

# ARK ETF 이름은 티커가 아님
_ARK_FUNDS = {"ARKK", "ARKQ", "ARKW", "ARKG", "ARKF", "ARKX", "PRNT", "IZRL"}
_SKIP_TICKERS = _ARK_FUNDS | {"BUY", "SELL", "NEW", "ETF", "USD", "THE", "INC", "LLC", "LTD"}


def _parse_market_value(text: str) -> float:
    """'$3.0M' or '$545.0K' or '$1.2B' 파싱 → 달러."""
    text = text.strip().replace("$", "").replace(",", "")
    if not text or text == "-":
        return 0.0
    multiplier = 1.0
    if text.upper().endswith("B"):
        multiplier = 1_000_000_000
        text = text[:-1]
    elif text.upper().endswith("M"):
        multiplier = 1_000_000
        text = text[:-1]
    elif text.upper().endswith("K"):
        multiplier = 1_000
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return 0.0


def _parse_pct(text: str) -> float:
    """'2.44%' or '-1.23%' 파싱."""
    text = text.strip().replace("%", "").replace(",", "")
    if not text or text == "-":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_date(text: str) -> str:
    """'Mar 20, 2026' → '2026-03-20'."""
    text = text.strip()
    # YYYY-MM-DD 이미 있으면 그대로
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    # 'Mar 20, 2026' 형식
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def scrape_ark_trades(
    watchlist: list[str] | None = None,
) -> list[dict[str, Any]]:
    """cathiesark.com에서 ARK 매매 데이터 크롤링.

    테이블 구조: Date | Fund | Ticker | Direction | Market Value | % of Position | % of ETF

    Returns: classify_news()용 synthetic news dict 리스트
    """
    alerts: list[dict[str, Any]] = []

    try:
        resp = requests.get(_COMBINED_TRADES_URL, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("ARK trades scrape failed: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # React 렌더링된 JSON 데이터 찾기 (Next.js __NEXT_DATA__ 등)
    script_data = _extract_json_data(soup)
    if script_data:
        alerts = _parse_json_trades(script_data, watchlist)
        if alerts:
            return alerts

    # HTML 테이블 파싱 (fallback)
    tables = soup.find_all("table")
    target_table = None
    for table in tables:
        header = table.find("tr")
        if header:
            header_text = header.get_text().lower()
            if any(kw in header_text for kw in ("ticker", "direction", "fund", "trade")):
                target_table = table
                break

    if not target_table and tables:
        target_table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)

    if not target_table:
        logger.warning("ARK trades: no table found")
        return []

    rows = target_table.find_all("tr")[1:]  # skip header

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 4:
            continue

        texts = [c.get_text(strip=True) for c in cols]

        # cathiesark 구조: Date | Fund | Ticker | Direction | Market Value | % Position | % ETF
        # 인덱스 기반 파싱 시도
        parsed = _parse_row_by_content(texts)
        if not parsed:
            continue

        ticker = parsed["ticker"]
        direction = parsed["direction"]
        fund = parsed["fund"]
        date_str = parsed["date"]
        market_value = parsed["market_value"]
        pct_position = parsed["pct_position"]
        pct_etf = parsed["pct_etf"]

        # ARK ETF 자체는 스킵
        if ticker in _ARK_FUNDS:
            continue

        # 워치리스트 필터
        if watchlist and ticker not in watchlist:
            continue

        is_new = "new" in " ".join(texts).lower()
        dir_tag = "BuyNew" if is_new and direction == "Buy" else direction

        alert_id = f"ark_{direction.lower()}_{ticker}_{fund}_{date_str}".replace(" ", "_")

        headline = (
            f"Cathie Wood's {fund} {direction.lower()} {ticker} "
            f"(${market_value / 1_000_000:.1f}M)"
            + (f" — NEW position" if is_new else "")
        )

        dir_kr = "매도" if direction == "Sell" else "매수"
        summary = (
            f"ARK Invest: {fund} {dir_kr} {ticker}. "
            f"거래 금액 ${market_value:,.0f}, 포지션 비중 {pct_position:.2f}%. "
            f"{'캐시우드 매도 — 주의 신호.' if direction == 'Sell' else '캐시우드 매수 — 긍정 신호.'}"
        )

        alerts.append({
            "id": alert_id,
            "source": "ARK Invest",
            "headline": headline,
            "summary": summary,
            "url": _COMBINED_TRADES_URL,
            "datetime": date_str or datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "_meta": {
                "ticker": ticker,
                "direction": direction.upper(),
                "fund": fund,
                "market_value": market_value,
                "pct_position": pct_position,
                "pct_etf": pct_etf,
                "is_new_position": is_new,
            },
        })

    logger.info("ARK trades: %d watchlist trades found (%d sells)",
                len(alerts), sum(1 for a in alerts if a["_meta"]["direction"] == "SELL"))
    return alerts


def _extract_json_data(soup: BeautifulSoup) -> list[dict] | None:
    """React/Next.js 앱의 내장 JSON 데이터 추출."""
    # __NEXT_DATA__ 스크립트 태그
    for script in soup.find_all("script"):
        text = script.string or ""
        if "__NEXT_DATA__" in text or "trades" in text.lower():
            try:
                # JSON 추출
                json_match = re.search(r'\{.*"trades".*\}', text, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(0))
                    if "trades" in data:
                        return data["trades"]
                    # nested 구조 탐색
                    for key in ("props", "pageProps", "data"):
                        if key in data and isinstance(data[key], dict):
                            if "trades" in data[key]:
                                return data[key]["trades"]
            except (json.JSONDecodeError, KeyError):
                continue

    # application/json 타입 스크립트
    for script in soup.find_all("script", type="application/json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list) and data and "ticker" in str(data[0]).lower():
                return data
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def _parse_json_trades(
    trades: list[dict],
    watchlist: list[str] | None,
) -> list[dict[str, Any]]:
    """JSON 형태의 매매 데이터 파싱."""
    alerts: list[dict[str, Any]] = []

    for trade in trades:
        ticker = str(trade.get("ticker", trade.get("symbol", ""))).upper()
        if not ticker or ticker in _SKIP_TICKERS:
            continue
        if watchlist and ticker not in watchlist:
            continue

        direction = str(trade.get("direction", trade.get("action", ""))).capitalize()
        if direction not in ("Buy", "Sell"):
            continue

        fund = str(trade.get("fund", trade.get("etf", ""))).upper()
        date_str = _parse_date(str(trade.get("date", "")))
        market_value = _parse_market_value(str(trade.get("market_value", trade.get("value", "0"))))
        pct_position = float(trade.get("pct_of_position", trade.get("weight", 0)) or 0)
        pct_etf = float(trade.get("pct_of_etf", 0) or 0)

        alert_id = f"ark_{direction.lower()}_{ticker}_{fund}_{date_str}".replace(" ", "_")

        dir_kr = "매도" if direction == "Sell" else "매수"
        alerts.append({
            "id": alert_id,
            "source": "ARK Invest",
            "headline": f"Cathie Wood's {fund} {direction.lower()} {ticker} (${market_value / 1_000_000:.1f}M)",
            "summary": f"ARK Invest: {fund} {dir_kr} {ticker}. 거래금액 ${market_value:,.0f}.",
            "url": _COMBINED_TRADES_URL,
            "datetime": date_str,
            "_meta": {
                "ticker": ticker,
                "direction": direction.upper(),
                "fund": fund,
                "market_value": market_value,
                "pct_position": pct_position,
                "pct_etf": pct_etf,
            },
        })

    return alerts


def _parse_row_by_content(texts: list[str]) -> dict[str, Any] | None:
    """행의 텍스트 내용을 분석해서 각 필드 추출.

    순서가 불확실할 수 있으므로 내용 기반으로 판별.
    """
    ticker = ""
    direction = ""
    fund = ""
    date_str = ""
    market_value = 0.0
    pct_position = 0.0
    pct_etf = 0.0
    pcts: list[float] = []

    for t in texts:
        t_stripped = t.strip()

        # 날짜 감지
        if not date_str:
            d = _parse_date(t_stripped)
            if d:
                date_str = d
                continue

        # Fund 감지 (ARK로 시작)
        if not fund and t_stripped.upper() in _ARK_FUNDS:
            fund = t_stripped.upper()
            continue

        # Direction 감지
        t_lower = t_stripped.lower()
        if not direction and t_lower in ("buy", "sell", "buynew"):
            direction = "Buy" if "buy" in t_lower else "Sell"
            continue

        # Market Value 감지 ($로 시작)
        if "$" in t_stripped and market_value == 0:
            market_value = _parse_market_value(t_stripped)
            continue

        # 퍼센트 감지
        if "%" in t_stripped:
            pcts.append(_parse_pct(t_stripped))
            continue

        # 티커 감지 (1~5글자 대문자, ARK 아닌 것)
        if not ticker and re.match(r'^[A-Z]{1,5}$', t_stripped) and t_stripped not in _SKIP_TICKERS:
            ticker = t_stripped
            continue

    if not ticker or not direction:
        return None

    if len(pcts) >= 2:
        pct_position = pcts[0]
        pct_etf = pcts[1]
    elif len(pcts) == 1:
        pct_position = pcts[0]

    return {
        "ticker": ticker,
        "direction": direction,
        "fund": fund,
        "date": date_str,
        "market_value": market_value,
        "pct_position": pct_position,
        "pct_etf": pct_etf,
    }


# ---------------------------------------------------------------------------
# 연속 매매 감지 + 볼륨 필터
# ---------------------------------------------------------------------------

def _detect_consecutive_trades(
    alerts: list[dict[str, Any]],
    min_consecutive_days: int = 2,
    min_single_value: float = 1_000_000,
) -> list[dict[str, Any]]:
    """연속 매매 감지 — 같은 종목 2일+ 같은 방향이면 강한 신호.

    단발 거래는 market_value >= $1M일 때만 포함.
    """
    ticker_trades: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        meta = a.get("_meta", {})
        key = f"{meta.get('ticker', '')}_{meta.get('direction', '')}"
        ticker_trades[key].append(a)

    enhanced: list[dict[str, Any]] = []

    for key, trades in ticker_trades.items():
        if not trades:
            continue

        trades.sort(key=lambda x: x.get("datetime", ""))

        # 고유 날짜 수 계산
        unique_dates = set()
        for t in trades:
            dt = t.get("datetime", "")[:10]
            if dt:
                unique_dates.add(dt)

        if len(unique_dates) >= min_consecutive_days:
            # 연속 매매 — 강한 신호
            ticker = trades[0]["_meta"]["ticker"]
            direction = trades[0]["_meta"]["direction"]
            total_value = sum(t["_meta"].get("market_value", 0) for t in trades)
            days = len(unique_dates)

            lead = trades[0].copy()
            dir_kr = "매도" if direction == "SELL" else "매수"
            lead["headline"] = (
                f"Cathie Wood ARK {days}일 연속 {ticker} {direction} "
                f"(total ${total_value / 1_000_000:.1f}M)"
            )
            lead["summary"] = (
                f"ARK Invest {days}일 연속 {ticker} {dir_kr}. "
                f"총 거래금액 ${total_value:,.0f}. "
                f"{'캐시우드 연속 매도 — 강한 약세 신호' if direction == 'SELL' else '캐시우드 연속 매수 — 강한 매수 신호'}."
            )
            lead["id"] = f"ark_streak_{direction.lower()}_{ticker}_{days}d"
            enhanced.append(lead)
        else:
            # 단발 거래 — 금액이 클 때만
            for t in trades:
                value = t["_meta"].get("market_value", 0)
                if value >= min_single_value:
                    enhanced.append(t)
                else:
                    logger.debug("ARK skip low-value: %s %s $%.0f",
                                 t["_meta"].get("ticker"), t["_meta"].get("direction"), value)

    return enhanced


def scan_ark_trades(
    watchlist: list[str] | None = None,
    min_consecutive_days: int = 2,
) -> list[dict[str, Any]]:
    """ARK 매매 통합 스캔.

    연속 매매 감지 + 금액 필터 적용.
    Returns: classify_news()용 synthetic news dict 리스트
    """
    raw_alerts = scrape_ark_trades(watchlist)
    return _detect_consecutive_trades(raw_alerts, min_consecutive_days)
