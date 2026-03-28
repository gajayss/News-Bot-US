"""Insider transaction scraper — finviz + dataroma 크롤링.

SEC Form 4 기반 내부자 매도 + 슈퍼인베스터 포지션 변동을 자동 감지.
Finnhub API보다 빠르게 데이터를 얻을 수 있다.

크롤링 대상:
  1. finviz.com/insidertrading.ashx — 실시간 내부자 거래 (매도 집중 감시)
  2. dataroma.com — 슈퍼인베스터 82명 포트폴리오 변동

출력: classify_news()에 넣을 수 있는 synthetic news dict 리스트
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("insider_scraper")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# ---------------------------------------------------------------------------
# CEO/고위임원 직급 판별
# ---------------------------------------------------------------------------
_CEO_ROLES = {"ceo", "chief executive", "president", "founder", "co-founder", "chairman"}
_HIGH_ROLES = {"cfo", "chief financial", "coo", "cto", "evp", "svp"}


def _is_ceo_level(role: str) -> bool:
    lowered = role.lower()
    return any(r in lowered for r in _CEO_ROLES)


def _is_high_level(role: str) -> bool:
    lowered = role.lower()
    return _is_ceo_level(role) or any(r in lowered for r in _HIGH_ROLES)


def _parse_value(text: str) -> float:
    """'1,234,567' or '1.23M' 형태의 금액 파싱."""
    text = text.strip().replace("$", "").replace(",", "")
    if not text or text == "-":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_shares(text: str) -> int:
    text = text.strip().replace(",", "")
    if not text or text == "-":
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# 1. Finviz Insider Trading Scraper
# ---------------------------------------------------------------------------

def scrape_finviz_insider(
    watchlist: list[str] | None = None,
    min_value: float = 1_000_000,
) -> list[dict[str, Any]]:
    """Finviz 내부자 거래 페이지 크롤링.

    Sale 거래 중 금액이 min_value 이상인 것만 필터.
    watchlist가 주어지면 해당 종목만 반환.

    Returns: classify_news()용 synthetic news dict 리스트
    """
    url = "https://finviz.com/insidertrading.ashx"
    alerts: list[dict[str, Any]] = []

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Finviz scrape failed: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # 내부자 거래 테이블 찾기
    tables = soup.find_all("table")
    target_table = None
    for table in tables:
        header_row = table.find("tr")
        if header_row and "Ticker" in header_row.get_text():
            target_table = table
            break

    if not target_table:
        logger.warning("Finviz: insider trading table not found")
        return []

    rows = target_table.find_all("tr")[1:]  # skip header

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 10:
            continue

        try:
            ticker = cols[0].get_text(strip=True).upper()
            owner = cols[1].get_text(strip=True)
            relationship = cols[2].get_text(strip=True)
            date_str = cols[3].get_text(strip=True)
            transaction = cols[4].get_text(strip=True)
            cost = _parse_value(cols[5].get_text(strip=True))
            shares = _parse_shares(cols[6].get_text(strip=True))
            value = _parse_value(cols[7].get_text(strip=True))
        except (IndexError, AttributeError):
            continue

        # 매도만 (Sale, Proposed Sale)
        if "sale" not in transaction.lower():
            continue

        # 워치리스트 필터
        if watchlist and ticker not in watchlist:
            continue

        # 최소 금액 필터
        if value < min_value:
            continue

        is_ceo = _is_ceo_level(relationship)
        role_tag = "CEO" if is_ceo else ("CFO/C-Suite" if _is_high_level(relationship) else "Officer")

        alerts.append({
            "id": f"finviz_insider_{ticker}_{date_str}_{owner[:10]}".replace(" ", "_"),
            "source": "SEC Form 4",
            "headline": (
                f"{ticker} {role_tag} {owner} sold {shares:,} shares "
                f"(${value:,.0f}) on {date_str}"
            ),
            "summary": (
                f"Finviz insider trading: {owner} ({relationship}) "
                f"sold {shares:,} shares of {ticker} at ${cost:.2f}, "
                f"total value ${value:,.0f}. "
                f"{'CEO/창업자 매도 — 고점 경고 신호' if is_ceo else '내부자 매도 감지'}"
            ),
            "url": f"https://finviz.com/insidertrading.ashx",
            "datetime": date_str,
            "_meta": {
                "ticker": ticker,
                "owner": owner,
                "role": relationship,
                "role_tag": role_tag,
                "transaction": transaction,
                "shares": shares,
                "value": value,
                "cost": cost,
            },
        })

    logger.info("Finviz insider: %d significant sales found", len(alerts))
    return alerts


# ---------------------------------------------------------------------------
# 2. Dataroma Super Investor Activity Scraper
# ---------------------------------------------------------------------------

def scrape_dataroma_activity(
    watchlist: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Dataroma 슈퍼인베스터 최근 활동 크롤링.

    Sell/Reduce 활동 중 워치리스트 종목만 필터.

    Returns: classify_news()용 synthetic news dict 리스트
    """
    url = "https://www.dataroma.com/m/allact.php?typ=a"
    alerts: list[dict[str, Any]] = []

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Dataroma scrape failed: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # 활동 테이블 찾기
    table = soup.find("table", {"id": "grid"})
    if not table:
        # fallback: 가장 큰 테이블
        tables = soup.find_all("table")
        table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)

    if not table:
        logger.warning("Dataroma: activity table not found")
        return []

    rows = table.find_all("tr")[1:]  # skip header

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 4:
            continue

        try:
            # Dataroma 구조: 투자자 | 종목 | 활동(Buy/Sell/Add/Reduce) | 변동% | 포트폴리오 영향
            raw_texts = [c.get_text(strip=True) for c in cols]

            # 텍스트에서 종목 티커 추출 (대문자 2~5글자)
            ticker_match = None
            for text in raw_texts:
                m = re.search(r'\b([A-Z]{2,5})\b', text)
                if m:
                    candidate = m.group(1)
                    if candidate not in {"BUY", "SELL", "ADD", "NEW", "USD", "INC", "LLC", "ETF", "THE"}:
                        ticker_match = candidate
                        break

            if not ticker_match:
                continue

            # 활동 유형 감지
            full_text = " ".join(raw_texts).lower()
            is_sell = "sell" in full_text or "reduce" in full_text

            if not is_sell:
                continue

            # 워치리스트 필터
            if watchlist and ticker_match not in watchlist:
                continue

            # 투자자 이름 (첫 번째 컬럼)
            investor = raw_texts[0] if raw_texts else "Unknown"

            # 변동 퍼센트 추출
            pct_match = re.search(r'[-+]?\d+\.?\d*%', full_text)
            change_pct = pct_match.group(0) if pct_match else ""

            alerts.append({
                "id": f"dataroma_{ticker_match}_{investor[:15]}_{datetime.now().strftime('%Y%m%d')}".replace(" ", "_"),
                "source": "Dataroma 13F",
                "headline": (
                    f"Super investor {investor} sells/reduces {ticker_match} "
                    f"position {change_pct}"
                ),
                "summary": (
                    f"Dataroma 13F filing: {investor} reduced or sold {ticker_match} "
                    f"position ({change_pct}). 슈퍼인베스터 포지션 축소 감지."
                ),
                "url": "https://www.dataroma.com/m/allact.php?typ=a",
                "datetime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "_meta": {
                    "ticker": ticker_match,
                    "investor": investor,
                    "action": "SELL/REDUCE",
                    "change_pct": change_pct,
                },
            })
        except Exception:
            continue

    logger.info("Dataroma: %d sell/reduce activities found", len(alerts))
    return alerts


# ---------------------------------------------------------------------------
# 통합 스캔
# ---------------------------------------------------------------------------

def scan_insider_web(
    watchlist: list[str] | None = None,
    min_value: float = 1_000_000,
) -> list[dict[str, Any]]:
    """finviz + dataroma 통합 스캔.

    Returns: classify_news()용 synthetic news dict 리스트
    """
    alerts: list[dict[str, Any]] = []

    # 1) Finviz 내부자 거래
    try:
        finviz_alerts = scrape_finviz_insider(watchlist, min_value)
        alerts.extend(finviz_alerts)
    except Exception as e:
        logger.warning("Finviz scan error: %s", e)

    time.sleep(1)  # rate limiting

    # 2) Dataroma 슈퍼인베스터
    try:
        dataroma_alerts = scrape_dataroma_activity(watchlist)
        alerts.extend(dataroma_alerts)
    except Exception as e:
        logger.warning("Dataroma scan error: %s", e)

    return alerts
