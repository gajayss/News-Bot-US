"""Insider transaction monitor — SEC Form 4 공시 기반 내부자 매도 감시.

Finnhub insider-transactions API를 주기적으로 폴링하여
뉴스에 안 떠도 내부자 대량 매도를 감지한다.

핵심 원칙:
  - CEO/창업자 매도 = 최강 약세 신호 (고점 징후)
  - 복수 임원 동시 매도 = 위험도 배가
  - 최근 30일 매도 총량이 평소 대비 급증 = 경고
  - SEC Form 4 공시는 거래 후 2영업일 이내 제출
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger("insider_source")

# ---------------------------------------------------------------------------
# 내부자 직급 분류
# ---------------------------------------------------------------------------
_CEO_TITLES = {
    "ceo", "chief executive", "president", "founder", "co-founder",
    "chairman", "chairwoman", "chair of the board",
}
_CFO_TITLES = {"cfo", "chief financial"}
_C_SUITE_TITLES = {
    "coo", "cto", "cmo", "cio", "chief operating", "chief technology",
    "chief marketing", "chief information", "evp", "svp",
    "executive vice president", "senior vice president",
}
_BOARD_FAMILY = {
    "director", "board member", "trustee",
    "brother", "sister", "spouse", "family",
}

# 매도 트랜잭션 코드 (SEC Form 4)
_SELL_CODES = {"S", "S-Sale", "S - Sale"}  # Finnhub 반환 포맷


def _classify_insider_role(name: str, title: str) -> str:
    """내부자 직급 분류 → CEO / CFO / C_SUITE / BOARD / OFFICER."""
    combined = f"{name} {title}".lower()
    if any(t in combined for t in _CEO_TITLES):
        return "CEO"
    if any(t in combined for t in _CFO_TITLES):
        return "CFO"
    if any(t in combined for t in _C_SUITE_TITLES):
        return "C_SUITE"
    if any(t in combined for t in _BOARD_FAMILY):
        return "BOARD"
    return "OFFICER"


def _is_significant_sale(
    role: str,
    shares: int,
    value_usd: float,
) -> tuple[bool, str]:
    """유의미한 매도인지 판단.

    Returns: (is_significant, reason)
    """
    # CEO/창업자: 어떤 매도든 중요
    if role == "CEO":
        if value_usd >= 10_000_000:
            return True, f"CEO 대규모 매도 ${value_usd:,.0f}"
        elif value_usd >= 1_000_000:
            return True, f"CEO 매도 ${value_usd:,.0f}"
        elif shares >= 50_000:
            return True, f"CEO {shares:,}주 매도"
        return False, ""

    # CFO: 재무 책임자가 팔면 심각
    if role == "CFO":
        if value_usd >= 5_000_000:
            return True, f"CFO 대규모 매도 ${value_usd:,.0f}"
        elif value_usd >= 500_000:
            return True, f"CFO 매도 ${value_usd:,.0f}"
        return False, ""

    # C-Suite: 대규모만
    if role == "C_SUITE":
        if value_usd >= 10_000_000:
            return True, f"임원 대규모 매도 ${value_usd:,.0f}"
        return False, ""

    # 이사회/가족: 대규모만
    if role == "BOARD":
        if value_usd >= 5_000_000:
            return True, f"이사회/가족 매도 ${value_usd:,.0f}"
        return False, ""

    # 일반 임원: 초대규모만
    if value_usd >= 20_000_000:
        return True, f"임원 초대규모 매도 ${value_usd:,.0f}"
    return False, ""


# ---------------------------------------------------------------------------
# Finnhub API
# ---------------------------------------------------------------------------

def fetch_insider_transactions(
    api_key: str,
    symbol: str,
) -> list[dict[str, Any]]:
    """Finnhub insider transactions API 호출."""
    url = "https://finnhub.io/api/v1/stock/insider-transactions"
    try:
        resp = requests.get(
            url,
            params={"symbol": symbol, "token": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
    except Exception as e:
        logger.warning("Insider fetch failed for %s: %s", symbol, e)
        return []


# ---------------------------------------------------------------------------
# 내부자 매도 분석
# ---------------------------------------------------------------------------

def analyze_insider_selling(
    transactions: list[dict[str, Any]],
    symbol: str,
    lookback_days: int = 30,
) -> list[dict[str, Any]]:
    """최근 N일 내부자 매도를 분석하여 유의미한 매도 이벤트를 반환.

    Returns list of significant insider selling events,
    each formatted as a "synthetic news" dict for classify_news().
    """
    cutoff = datetime.now() - timedelta(days=lookback_days)
    alerts: list[dict[str, Any]] = []
    total_sell_value = 0.0
    total_sell_shares = 0
    sellers: list[str] = []

    for tx in transactions:
        # 매도 필터
        tx_code = str(tx.get("transactionCode", ""))
        if tx_code not in _SELL_CODES and "sale" not in tx_code.lower():
            if tx.get("change", 0) >= 0:  # 매수 or 무변동
                continue

        # 날짜 필터
        filing_date = str(tx.get("filingDate", ""))
        tx_date = str(tx.get("transactionDate", filing_date))
        try:
            dt = datetime.strptime(tx_date[:10], "%Y-%m-%d")
        except (ValueError, IndexError):
            continue
        if dt < cutoff:
            continue

        name = str(tx.get("name", "Unknown"))
        shares = abs(int(tx.get("share", 0) or tx.get("change", 0)))
        price = float(tx.get("transactionPrice", 0) or 0)
        value = shares * price if price > 0 else 0

        role = _classify_insider_role(name, "")
        is_sig, reason = _is_significant_sale(role, shares, value)

        total_sell_value += value
        total_sell_shares += shares
        if name not in sellers:
            sellers.append(name)

        if is_sig:
            alerts.append({
                "id": f"insider_{symbol}_{tx_date}_{name[:10]}",
                "source": "SEC Form 4",
                "headline": f"{symbol} {role} {name} sold {shares:,} shares (${value:,.0f}) — {reason}",
                "summary": f"SEC Form 4 filing: {name} ({role}) sold {shares:,} shares of {symbol} at ${price:.2f} on {tx_date}. {reason}",
                "url": "",
                "datetime": tx_date,
            })

    # 복수 임원 동시 매도 집계 경고
    if len(sellers) >= 3 and total_sell_value >= 5_000_000:
        alerts.insert(0, {
            "id": f"insider_cluster_{symbol}_{datetime.now().strftime('%Y%m%d')}",
            "source": "SEC Form 4",
            "headline": f"{symbol} insider selling cluster: {len(sellers)} insiders sold ${total_sell_value:,.0f} in {lookback_days}d",
            "summary": (
                f"Multiple {symbol} insiders selling: {', '.join(sellers[:5])}. "
                f"Total {total_sell_shares:,} shares worth ${total_sell_value:,.0f} in {lookback_days} days. "
                f"고점 경고 신호."
            ),
            "url": "",
            "datetime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

    return alerts


def scan_watchlist_insiders(
    api_key: str,
    watchlist: list[str],
    lookback_days: int = 30,
    rate_limit_sec: float = 0.5,
) -> list[dict[str, Any]]:
    """워치리스트 전체 내부자 거래 스캔.

    Returns list of synthetic news events for classify_news().
    """
    all_alerts: list[dict[str, Any]] = []

    for symbol in watchlist:
        transactions = fetch_insider_transactions(api_key, symbol)
        if not transactions:
            continue

        alerts = analyze_insider_selling(transactions, symbol, lookback_days)
        all_alerts.extend(alerts)

        if rate_limit_sec > 0:
            time.sleep(rate_limit_sec)

    if all_alerts:
        logger.info("Insider scan: %d alerts from %d symbols", len(all_alerts), len(watchlist))

    return all_alerts
