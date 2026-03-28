"""Fintel.io 공매도 볼륨 스크래퍼 — Short Volume Ratio 급증 감지.

fintel.io/ssv/us/{SYMBOL} 페이지에서 일별 공매도 데이터를 크롤링.
공매도 비율이 급증하면 약세 신호로 분류.

⚠️ fintel.io는 requests 403 차단 → Chrome MCP 필요.
   requests 버전은 fallback으로 유지하되, 실패 시 로그만 남김.

크롤링 대상:
  - fintel.io/ssv/us/{SYMBOL} → 일별 Short Volume, Short Volume Ratio
  - Short Volume Ratio > 50% = 공매도 압력 높음
  - 최근 5일 평균 대비 20%p 이상 급증 = 경고

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

logger = logging.getLogger("fintel_scraper")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_number(text: str) -> float:
    """'1,234,567' or '38.75%' 파싱."""
    text = text.strip().replace(",", "").replace("%", "")
    if not text or text == "-" or text == "N/A":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def scrape_fintel_short_volume(
    symbol: str,
    lookback_days: int = 10,
) -> dict[str, Any] | None:
    """Fintel Short Volume 페이지 크롤링.

    Returns: {symbol, latest_ratio, avg_ratio, spike, days_data} or None
    """
    url = f"https://fintel.io/ssv/us/{symbol.lower()}"

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Fintel scrape failed for %s: %s", symbol, e)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # 테이블에서 일별 데이터 추출
    tables = soup.find_all("table")
    target_table = None
    for table in tables:
        header = table.find("tr")
        if header and ("Short Volume" in header.get_text() or "Volume" in header.get_text()):
            target_table = table
            break

    if not target_table:
        # fallback: 가장 큰 테이블
        if tables:
            target_table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)

    if not target_table:
        logger.warning("Fintel %s: short volume table not found", symbol)
        return None

    rows = target_table.find_all("tr")[1:]  # skip header
    days_data: list[dict[str, Any]] = []

    for row in rows[:lookback_days]:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        texts = [c.get_text(strip=True) for c in cols]

        # 날짜 찾기
        date_str = ""
        for t in texts:
            if re.match(r"\d{4}-\d{2}-\d{2}", t):
                date_str = t[:10]
                break

        # 숫자 값들 추출
        numbers = []
        for t in texts:
            val = _parse_number(t)
            if val > 0:
                numbers.append(val)

        # Short Volume Ratio 찾기 (보통 0~100 사이의 퍼센트)
        ratio = 0.0
        for t in texts:
            if "%" in t:
                ratio = _parse_number(t)
                break

        # % 없으면 0~100 범위 숫자 찾기
        if ratio == 0 and numbers:
            for n in numbers:
                if 10 < n < 100:
                    ratio = n
                    break

        if ratio > 0:
            days_data.append({
                "date": date_str,
                "ratio": ratio,
                "raw": " | ".join(texts),
            })

    if not days_data:
        logger.warning("Fintel %s: no short volume data parsed", symbol)
        return None

    latest_ratio = days_data[0]["ratio"]
    avg_ratio = sum(d["ratio"] for d in days_data) / len(days_data) if days_data else 0

    return {
        "symbol": symbol,
        "latest_ratio": latest_ratio,
        "avg_ratio": round(avg_ratio, 2),
        "spike": round(latest_ratio - avg_ratio, 2),
        "days_count": len(days_data),
        "days_data": days_data[:5],  # 최근 5일만
    }


def scan_short_volume(
    watchlist: list[str],
    spike_threshold: float = 10.0,
    high_ratio_threshold: float = 50.0,
) -> list[dict[str, Any]]:
    """워치리스트 전체 공매도 볼륨 스캔.

    spike_threshold: 평균 대비 이만큼 %p 이상 급증하면 경고
    high_ratio_threshold: 이 비율 이상이면 공매도 압력 높음 경고

    Returns: classify_news()용 synthetic news dict 리스트
    """
    alerts: list[dict[str, Any]] = []

    for symbol in watchlist:
        result = scrape_fintel_short_volume(symbol)
        if not result:
            time.sleep(1)
            continue

        latest = result["latest_ratio"]
        avg = result["avg_ratio"]
        spike = result["spike"]

        # 공매도 비율 급증 감지
        if spike >= spike_threshold:
            alerts.append({
                "id": f"fintel_sv_{symbol}_{datetime.now().strftime('%Y%m%d')}",
                "source": "Fintel Short Volume",
                "headline": (
                    f"{symbol} short volume ratio spiked to {latest:.1f}% "
                    f"(avg {avg:.1f}%, +{spike:.1f}%p)"
                ),
                "summary": (
                    f"Fintel: {symbol} 공매도 비율 {latest:.1f}%로 급증 "
                    f"(최근 평균 {avg:.1f}%, {spike:+.1f}%p 변동). "
                    f"공매도 압력 증가 감지."
                ),
                "url": f"https://fintel.io/ssv/us/{symbol.lower()}",
                "datetime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "_meta": {
                    "ticker": symbol,
                    "short_ratio": latest,
                    "avg_ratio": avg,
                    "spike": spike,
                    "signal_type": "SHORT_VOLUME_SPIKE",
                },
            })

        # 절대적 고비율 경고
        elif latest >= high_ratio_threshold:
            alerts.append({
                "id": f"fintel_hi_{symbol}_{datetime.now().strftime('%Y%m%d')}",
                "source": "Fintel Short Volume",
                "headline": (
                    f"{symbol} short volume ratio elevated at {latest:.1f}% "
                    f"(avg {avg:.1f}%)"
                ),
                "summary": (
                    f"Fintel: {symbol} 공매도 비율 {latest:.1f}% 고수준 유지 "
                    f"(평균 {avg:.1f}%). 공매도 세력 주시 필요."
                ),
                "url": f"https://fintel.io/ssv/us/{symbol.lower()}",
                "datetime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "_meta": {
                    "ticker": symbol,
                    "short_ratio": latest,
                    "avg_ratio": avg,
                    "spike": spike,
                    "signal_type": "HIGH_SHORT_RATIO",
                },
            })

        time.sleep(1)  # rate limiting

    logger.info("Fintel short volume: %d alerts from %d symbols", len(alerts), len(watchlist))
    return alerts
