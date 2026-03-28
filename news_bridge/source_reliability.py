"""News source reliability scoring — 뉴스 출처 신뢰도 필터.

원칙:
  1. Tier 1 (Reuters, Bloomberg, WSJ 등) → 신뢰도 높음 → 그대로 통과
  2. Tier 2 (CNBC, MarketWatch 등) → 중간 → confidence 약간 감소
  3. Tier 3 (소형 미디어, 블로그, 기타) → 신뢰도 낮음 → confidence 대폭 감소
  4. 알 수 없는 출처 → 최저 신뢰도 → 거래 불가 수준으로 하향

Finnhub API 가 반환하는 source 필드 기반으로 분류.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("source_reliability")


# ---------------------------------------------------------------------------
# 출처 티어 분류
# ---------------------------------------------------------------------------

# Tier 1: 최고 신뢰도 — 메이저 통신사/경제 전문지 + 영향력 있는 기관/펀드
TIER_1_SOURCES: set[str] = {
    "reuters", "bloomberg", "wsj", "wall street journal",
    "financial times", "ft", "associated press", "ap",
    "sec.gov", "sec", "federal reserve", "fed",
    "barrons", "barron's",
    # 공매도 기관 / 유명 헤지펀드 리포트 (폭락 유발 가능 → 빠른 대응 필수)
    "hindenburg", "hindenburg research",
    "citron", "citron research",
    "muddy waters", "iceberg research",
    "wolfpack", "spruce point", "grizzly research", "kerrisdale",
    # 유명 투자자/펀드 매니저 (포지션 변경 시 시장 영향 큼)
    "scion", "michael burry",
    "pershing square", "bill ackman",
    "bridgewater", "ray dalio",
    "berkshire", "warren buffett",
    "ark invest", "cathie wood",
    "elliott management",
    "third point", "dan loeb",
    "greenlight", "david einhorn",
    "citadel", "tiger global",
}

# Tier 2: 높은 신뢰도 — 주요 경제 미디어
TIER_2_SOURCES: set[str] = {
    "cnbc", "marketwatch", "yahoo finance", "yahoo",
    "seeking alpha", "seekingalpha", "benzinga",
    "investing.com", "investopedia", "fortune",
    "business insider", "insider", "the motley fool",
    "thestreet", "zacks", "morningstar",
    "tipranks", "finviz", "newsfilecorp",
    # 실시간 뉴스 피드 + 데이터 소스
    "financialjuice", "financial juice",
    "fintel", "fintel.io",
    "dataroma", "hedgefollow",
    "cathiesark", "ark invest trades",
    # SEC 공시
    "sec form 4", "13f",
}

# Tier 3: 일반 미디어 — 정확도 보통
TIER_3_SOURCES: set[str] = {
    "fox business", "cnn business", "cnn", "bbc",
    "nbc", "abc", "the guardian", "nytimes", "new york times",
    "washington post", "politico", "axios",
    "techcrunch", "the verge", "wired", "ars technica",
}

# 신뢰도 점수 (0.0 ~ 1.0)
TIER_SCORES: dict[int, float] = {
    1: 1.00,   # 그대로 통과
    2: 0.85,   # confidence 15% 감소
    3: 0.70,   # confidence 30% 감소
    4: 0.40,   # 알 수 없는 출처 → 60% 감소
}

# 최소 신뢰도 — 이 아래면 tradable=False
MIN_RELIABILITY_FOR_TRADE = 0.50


def classify_source(source: str) -> int:
    """출처 문자열 → 티어 (1~4)."""
    if not source:
        return 4

    lowered = source.lower().strip()

    # finnhub 은 source 필드에 도메인이나 매체명을 반환
    for t1 in TIER_1_SOURCES:
        if t1 in lowered:
            return 1

    for t2 in TIER_2_SOURCES:
        if t2 in lowered:
            return 2

    for t3 in TIER_3_SOURCES:
        if t3 in lowered:
            return 3

    return 4  # 알 수 없는 출처


def get_reliability_score(source: str) -> float:
    """출처 → 신뢰도 점수 (0.0 ~ 1.0)."""
    tier = classify_source(source)
    return TIER_SCORES[tier]


def adjust_confidence(confidence: float, source: str) -> float:
    """출처 신뢰도에 따라 confidence 보정."""
    reliability = get_reliability_score(source)
    adjusted = confidence * reliability
    return round(adjusted, 4)


def is_reliable_enough(source: str) -> bool:
    """거래 가능한 수준의 신뢰도인지."""
    return get_reliability_score(source) >= MIN_RELIABILITY_FOR_TRADE


def get_source_info(source: str) -> dict[str, Any]:
    """출처 정보 반환 (로깅/디버깅용)."""
    tier = classify_source(source)
    return {
        "source": source,
        "tier": tier,
        "reliability": TIER_SCORES[tier],
        "tier_label": {1: "MAJOR", 2: "REPUTABLE", 3: "GENERAL", 4: "UNKNOWN"}[tier],
        "tradable": TIER_SCORES[tier] >= MIN_RELIABILITY_FOR_TRADE,
    }
