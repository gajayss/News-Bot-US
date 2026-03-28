"""HedgeFollow parser — 헤지펀드 13F 포지션 변동 분석.

Cloudflare 보호 때문에 headless 크롤링 불가.
실제 브라우저(Chrome MCP)에서 읽은 텍스트를 파싱하는 구조.

사용법:
  1. Chrome MCP로 hedgefollow.com/stocks/{SYMBOL} 접속
  2. get_page_text()로 텍스트 추출
  3. parse_hedgefollow_text()로 매도/축소 감지

크롤링 대상:
  - hedgefollow.com/stocks/{SYMBOL} → 헤지펀드 보유 현황
  - 대규모 매도(Sell/Reduce), 포지션 종료(Closed) 감지
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger("hedgefollow_scraper")


def _parse_delta(text: str) -> float:
    """'+45.77%' or '-6.65%' 파싱."""
    text = text.strip().replace("%", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_shares_text(text: str) -> str:
    """'(+455k)' or '(-785k)' or '(-32.5M)' 정리."""
    return text.strip()


def parse_hedgefollow_text(
    text: str,
    symbol: str,
    min_reduce_pct: float = -5.0,
) -> list[dict[str, Any]]:
    """Chrome MCP get_page_text() 결과를 파싱.

    hedgefollow 페이지의 텍스트에서 포지션 축소/종료를 감지.
    min_reduce_pct: 이 비율 이하로 줄인 펀드만 경고 (기본 -5%)

    Returns: classify_news()용 synthetic news dict 리스트
    """
    alerts: list[dict[str, Any]] = []

    # 텍스트에서 펀드별 행 파싱
    # 패턴: "펀드명 XX.XX% -YY.YY% (+/-NNNk) $VALUE ..."
    lines = text.split("\n")

    # 헤지펀드 데이터 행: 펀드명 뒤에 퍼센트와 변동이 따라옴
    # "Miura Global Management Francisco Alfaro 11.22%-40% (-24k)$6.7M..."
    fund_pattern = re.compile(
        r'([A-Z][A-Za-z\s&\'.,-]+?)\s+'         # 펀드명
        r'(\d+\.?\d*%)\s*'                        # % of Portfolio
        r'([-+]?\d+\.?\d*%)\s*'                   # Δ % change
        r'\(([^)]+)\)'                             # (shares change)
    )

    # 더 간단한 패턴: -XX.XX% 찾기
    reduce_pattern = re.compile(r'(-\d+\.?\d*)%')

    for line in lines:
        line = line.strip()
        if not line or len(line) < 20:
            continue

        # 축소/종료 감지
        deltas = reduce_pattern.findall(line)
        if not deltas:
            continue

        for delta_str in deltas:
            delta_pct = float(delta_str)
            if delta_pct > min_reduce_pct:
                continue

            # 펀드명 추출 (줄의 앞부분에서 영문 이름)
            name_match = re.match(r'^([A-Za-z][A-Za-z\s&\'.,-]{5,60})', line)
            fund_name = name_match.group(1).strip() if name_match else line[:40]

            # NEW는 스킵
            if "NEW" in line and "+" in line:
                continue

            action = f"REDUCED {delta_pct:.1f}%"

            alerts.append({
                "id": f"hf_{symbol}_{fund_name[:15]}_{datetime.now().strftime('%Y%m%d')}".replace(" ", "_"),
                "source": "HedgeFollow 13F",
                "headline": (
                    f"{symbol}: hedge fund {fund_name} {action} position"
                ),
                "summary": (
                    f"HedgeFollow 13F: {fund_name} reduced {symbol} position by {delta_pct:.1f}%. "
                    f"헤지펀드 포지션 축소 감지."
                ),
                "url": f"https://hedgefollow.com/stocks/{symbol}",
                "datetime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "_meta": {
                    "ticker": symbol,
                    "fund": fund_name,
                    "action": action,
                    "delta_pct": delta_pct,
                },
            })
            break  # 한 줄에서 하나만

    # 중복 제거 (같은 펀드)
    seen_funds: set[str] = set()
    unique_alerts: list[dict[str, Any]] = []
    for a in alerts:
        fund = a["_meta"]["fund"]
        if fund not in seen_funds:
            seen_funds.add(fund)
            unique_alerts.append(a)

    logger.info("HedgeFollow %s: %d significant reductions parsed", symbol, len(unique_alerts))
    return unique_alerts
