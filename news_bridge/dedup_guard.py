"""Duplicate news guard — 동일/유사 뉴스에 의한 중복 베팅 방지.

동작 원리:
  1. source_news_id 기반 정확 중복 제거  (같은 뉴스 ID → 무조건 차단)
  2. headline 유사도 기반 퍼지 중복 제거 (유사 헤드라인 → 시간 내 차단)
  3. symbol + direction 쿨다운            (같은 종목 같은 방향 → N분 내 차단)

뉴스 API 특성상 같은 이벤트가 여러 소스에서 반복 보도됨.
5분~30분 사이에 같은 종목, 같은 방향으로 여러 번 진입하면 위험.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger("dedup_guard")

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
_DEFAULT_EXACT_TTL = 3600       # 1시간: 같은 news_id 재진입 차단
_DEFAULT_FUZZY_TTL = 3600       # 1시간:  유사 헤드라인 차단 (30분→1시간, FJ RSS 반복 대응)
_DEFAULT_SYMBOL_COOLDOWN = 1800 # 30분:  같은 종목+방향 쿨다운 (15분→30분)


def _normalize_headline(headline: str) -> str:
    """헤드라인 정규화 — 소문자 + 특수문자 제거 + 공백 통일."""
    import re
    text = headline.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _headline_fingerprint(headline: str) -> str:
    """헤드라인 → 핑거프린트 (정규화 후 hash).

    단어 정렬하여 어순 차이에도 동일한 뉴스를 잡아냄.
    """
    normalized = _normalize_headline(headline)
    # 단어 정렬 → 어순 무관하게 같은 내용이면 같은 해시
    words = sorted(normalized.split())
    text = " ".join(words)
    return hashlib.md5(text.encode()).hexdigest()[:16]


def _headline_similarity(a: str, b: str) -> float:
    """단어 자카드 유사도 (0.0 ~ 1.0)."""
    wa = set(_normalize_headline(a).split())
    wb = set(_normalize_headline(b).split())
    if not wa or not wb:
        return 0.0
    intersection = wa & wb
    union = wa | wb
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# DedupGuard
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _DedupEntry:
    timestamp: float
    headline: str
    fingerprint: str
    symbols: list[str]
    direction: str


class DedupGuard:
    """뉴스 중복/반복 감지 및 차단."""

    def __init__(
        self,
        exact_ttl: int = _DEFAULT_EXACT_TTL,
        fuzzy_ttl: int = _DEFAULT_FUZZY_TTL,
        symbol_cooldown: int = _DEFAULT_SYMBOL_COOLDOWN,
        similarity_threshold: float = 0.50,  # 0.65→0.50: 같은 사건 반복 뉴스 더 공격적 필터
    ) -> None:
        self._exact_ttl = exact_ttl
        self._fuzzy_ttl = fuzzy_ttl
        self._symbol_cooldown = symbol_cooldown
        self._similarity_threshold = similarity_threshold

        # news_id → timestamp
        self._seen_ids: dict[str, float] = {}
        # fingerprint → DedupEntry
        self._seen_fps: dict[str, _DedupEntry] = {}
        # (symbol, direction) → timestamp
        self._symbol_dir_last: dict[tuple[str, str], float] = {}
        # headline list for fuzzy matching (recent only)
        self._recent_headlines: list[_DedupEntry] = []

    def _cleanup(self) -> None:
        """만료된 항목 정리."""
        now = time.time()

        # 정확 ID
        expired_ids = [k for k, ts in self._seen_ids.items() if now - ts > self._exact_ttl]
        for k in expired_ids:
            del self._seen_ids[k]

        # 핑거프린트
        expired_fps = [k for k, e in self._seen_fps.items() if now - e.timestamp > self._fuzzy_ttl]
        for k in expired_fps:
            del self._seen_fps[k]

        # 심볼 쿨다운
        expired_sym = [k for k, ts in self._symbol_dir_last.items() if now - ts > self._symbol_cooldown]
        for k in expired_sym:
            del self._symbol_dir_last[k]

        # 최근 헤드라인
        self._recent_headlines = [
            e for e in self._recent_headlines if now - e.timestamp < self._fuzzy_ttl
        ]

    def is_duplicate(
        self,
        news_id: str,
        headline: str,
        symbols: list[str],
        direction: str,
    ) -> tuple[bool, str]:
        """중복 여부 판단.

        Returns:
            (is_dup, reason)
        """
        self._cleanup()
        now = time.time()

        # 1) 정확 ID 매칭
        if news_id and news_id in self._seen_ids:
            age = now - self._seen_ids[news_id]
            return True, f"동일 뉴스 ID '{news_id}' ({age:.0f}초 전 처리됨)"

        # 2) 핑거프린트 매칭 (단어 정렬 해시)
        fp = _headline_fingerprint(headline)
        if fp in self._seen_fps:
            prev = self._seen_fps[fp]
            age = now - prev.timestamp
            return True, f"동일 헤드라인 해시 ({age:.0f}초 전: '{prev.headline[:40]}...')"

        # 3) 퍼지 유사도 매칭
        for entry in self._recent_headlines:
            sim = _headline_similarity(headline, entry.headline)
            if sim >= self._similarity_threshold:
                age = now - entry.timestamp
                return True, (
                    f"유사 헤드라인 (유사도 {sim:.0%}, {age:.0f}초 전: "
                    f"'{entry.headline[:40]}...')"
                )

        # 4) 심볼+방향 쿨다운
        for sym in symbols:
            key = (sym, direction)
            if key in self._symbol_dir_last:
                age = now - self._symbol_dir_last[key]
                return True, f"{sym} {direction} 쿨다운 중 ({age:.0f}초/{self._symbol_cooldown}초)"

        return False, ""

    def register(
        self,
        news_id: str,
        headline: str,
        symbols: list[str],
        direction: str,
    ) -> None:
        """처리 완료된 뉴스 등록 — 이후 같은 뉴스/방향 차단."""
        now = time.time()

        if news_id:
            self._seen_ids[news_id] = now

        fp = _headline_fingerprint(headline)
        entry = _DedupEntry(
            timestamp=now,
            headline=headline,
            fingerprint=fp,
            symbols=symbols,
            direction=direction,
        )
        self._seen_fps[fp] = entry
        self._recent_headlines.append(entry)

        for sym in symbols:
            self._symbol_dir_last[(sym, direction)] = now

        logger.debug("Registered: [%s] %s %s '%s'", news_id, symbols, direction, headline[:50])

    def get_stats(self) -> dict[str, int]:
        """현재 등록 현황."""
        return {
            "tracked_ids": len(self._seen_ids),
            "tracked_fingerprints": len(self._seen_fps),
            "tracked_symbol_dirs": len(self._symbol_dir_last),
            "recent_headlines": len(self._recent_headlines),
        }
