"""V4 Market Context Scoring Engine — 실시간 시장 환경 점수 계산.

backtest_v4.py 의 7-레이어 스코어링을 실시간 뉴스봇에 통합.
yfinance 로 VIX, TLT, SPY, 업종 ETF 를 캐싱하며,
캘린더 효과(요일, 월초/월말, OPEX, 세마녀)를 반영한다.

스코어 범위: -12 ~ +12
  ≥ +3  → CALL 유리 (뉴스 BULLISH 와 합산)
  ≤ -3  → PUT 유리  (뉴스 BEARISH 와 합산)
  -2~+2 → 중립지대  (뉴스 단독으로는 옵션 진입 자제)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("market_context")

# ---------------------------------------------------------------------------
# 심볼 → 업종 ETF 매핑
# ---------------------------------------------------------------------------
SYMBOL_TO_SECTOR_ETF: dict[str, str] = {
    # 반도체
    "NVDA": "SOXX", "AMD": "SOXX", "AVGO": "SOXX", "MU": "SOXX",
    "QCOM": "SOXX", "INTC": "SOXX", "TXN": "SOXX", "AMAT": "SOXX",
    "LRCX": "SOXX", "KLAC": "SOXX", "SOXL": "SOXX",
    # 테크
    "TQQQ": "XLK", "QQQ": "XLK", "MSFT": "XLK", "AAPL": "XLK",
    "GOOGL": "XLK", "META": "XLK", "CRM": "XLK", "NOW": "XLK",
    "PLTR": "XLK", "CRWD": "XLK", "PANW": "XLK",
    # 에너지
    "XOM": "XLE", "CVX": "XLE", "OXY": "XLE", "COP": "XLE",
    "USO": "XLE", "XLE": "XLE",
    # 금융
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "MS": "XLF",
    "V": "XLF", "MA": "XLF",
    # 헬스케어
    "LLY": "XLV", "JNJ": "XLV", "UNH": "XLV", "PFE": "XLV",
    "MRK": "XLV", "AMGN": "XLV", "XBI": "XLV",
    # 산업재
    "CAT": "XLI", "DE": "XLI", "HON": "XLI", "BA": "XLI",
    "LMT": "XLI", "RTX": "XLI", "GE": "XLI", "ITA": "XLI",
}

SECTOR_ETF_TICKERS = ["SOXX", "XLK", "XLE", "XLF", "XLV", "XLI"]

# ---------------------------------------------------------------------------
# 캐시 TTL
# ---------------------------------------------------------------------------
_CACHE_TTL_SEC = 300  # 5분


# ---------------------------------------------------------------------------
# 보조 함수
# ---------------------------------------------------------------------------

def _calc_indicators(closes: pd.Series):
    """MA5/MA20/MA50, RSI14 계산."""
    ma5 = closes.rolling(5).mean()
    ma20 = closes.rolling(20).mean()
    ma50 = closes.rolling(50).mean()
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss_s = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss_s
    rsi = 100 - (100 / (1 + rs))
    return ma5, ma20, ma50, rsi


def _trend_score(price: float, ma5: float, ma20: float, ma50: float) -> int:
    """MA 정렬 기반 추세 점수 (-2 ~ +2)."""
    if ma5 > ma20 > ma50:
        return 2
    elif price > ma20:
        return 1
    elif ma5 < ma20 < ma50:
        return -2
    elif price < ma20:
        return -1
    return 0


def _safe_last(series: pd.Series) -> float:
    """시리즈의 마지막 유효값."""
    if series is None or series.empty:
        return np.nan
    v = series.dropna()
    return float(v.iloc[-1]) if not v.empty else np.nan


def _is_opex_week(dt: datetime) -> tuple[bool, datetime]:
    """월간 옵션 만기 주간 (매월 셋째 금요일 ± 2일)."""
    year, month = dt.year, dt.month
    first_day = datetime(year, month, 1)
    days_until_friday = (4 - first_day.weekday()) % 7
    first_friday = first_day + timedelta(days=days_until_friday)
    third_friday = first_friday + timedelta(weeks=2)
    diff = abs((dt - third_friday).days)
    return diff <= 2, third_friday


def _is_triple_witching(dt: datetime) -> bool:
    """세마녀의 날 (3,6,9,12월 셋째 금요일 주간)."""
    if dt.month not in [3, 6, 9, 12]:
        return False
    is_opex, _ = _is_opex_week(dt)
    return is_opex


# ---------------------------------------------------------------------------
# MarketContext 결과
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MarketScore:
    """V4 시장 환경 점수."""
    vix_score: int = 0        # -2 ~ +2
    tlt_score: int = 0        # -1 ~ +1
    index_score: int = 0      # -2 ~ +2
    sector_score: int = 0     # -2 ~ +2
    calendar_score: int = 0   # -2 ~ +2
    rotation_score: int = 0   # -1 ~ +1
    total: int = 0            # 합산 (-12 ~ +12, 종목 추세 제외)
    vix_level: float = 0.0
    spy_rsi: float = 0.0
    is_opex: bool = False
    is_triple_witching: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def direction_hint(self) -> str:
        if self.total >= 3:
            return "BULLISH"
        elif self.total <= -3:
            return "BEARISH"
        return "NEUTRAL"

    @property
    def conviction(self) -> str:
        a = abs(self.total)
        if a >= 7:
            return "HIGH"
        elif a >= 5:
            return "MEDIUM"
        elif a >= 3:
            return "LOW"
        return "NONE"


# ---------------------------------------------------------------------------
# MarketContextEngine — 핵심 엔진
# ---------------------------------------------------------------------------

class MarketContextEngine:
    """실시간 시장 환경 스코어링 엔진.

    yfinance 에서 VIX, TLT, SPY, 업종 ETF 데이터를 캐싱하고
    V4 백테스트의 7-레이어 스코어링을 실시간으로 계산한다.
    """

    def __init__(self, lookback_days: int = 90, cache_ttl: int = _CACHE_TTL_SEC) -> None:
        self._lookback = lookback_days
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}  # ticker → (timestamp, df)
        self._sector_cache: dict[str, dict[str, Any]] = {}
        self._last_full_load: float = 0.0

    def _fetch(self, ticker: str) -> pd.DataFrame:
        """캐시된 yfinance 데이터 반환."""
        now = time.time()
        if ticker in self._cache:
            ts, df = self._cache[ticker]
            if now - ts < self._cache_ttl:
                return df

        try:
            import yfinance as yf
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=self._lookback + 30)
            df = yf.download(
                ticker,
                start=start_dt.strftime("%Y-%m-%d"),
                end=end_dt.strftime("%Y-%m-%d"),
                auto_adjust=True,
                progress=False,
            )
            if df.empty:
                logger.warning("Empty data for %s", ticker)
                return pd.DataFrame()
            self._cache[ticker] = (now, df)
            return df
        except Exception as e:
            logger.error("Failed to fetch %s: %s", ticker, e)
            return self._cache.get(ticker, (0, pd.DataFrame()))[1]

    def _get_close(self, ticker: str) -> pd.Series:
        df = self._fetch(ticker)
        if df.empty:
            return pd.Series(dtype=float)
        return df["Close"].squeeze().dropna()

    def _load_sectors(self) -> None:
        """업종 ETF 데이터 일괄 로딩 (캐시 TTL 기반)."""
        now = time.time()
        if now - self._last_full_load < self._cache_ttl:
            return

        for etf in SECTOR_ETF_TICKERS:
            closes = self._get_close(etf)
            if closes.empty:
                continue
            ma5, ma20, ma50, rsi = _calc_indicators(closes)
            ret5 = closes.pct_change(5)
            self._sector_cache[etf] = {
                "close": closes, "ma5": ma5, "ma20": ma20, "ma50": ma50,
                "rsi": rsi, "ret5": ret5,
            }
        self._last_full_load = now

    # ------------------------------------------------------------------
    # 스코어링 레이어
    # ------------------------------------------------------------------

    def _score_vix(self) -> tuple[int, float]:
        """VIX 레벨 + 방향 (-2 ~ +2)."""
        vix = self._get_close("^VIX")
        if vix.empty:
            return 0, 0.0

        v = _safe_last(vix)
        vm10 = _safe_last(vix.rolling(10).mean())
        vm20 = _safe_last(vix.rolling(20).mean())

        score = 0
        if not np.isnan(v):
            if v > 30:
                score -= 1
            elif v < 18:
                score += 1

        if not np.isnan(vm10) and not np.isnan(vm20):
            if vm10 < vm20 * 0.95:
                score += 1   # VIX 하락 추세 → 강세
            elif vm10 > vm20 * 1.05:
                score -= 1   # VIX 상승 추세 → 약세

        return score, v if not np.isnan(v) else 0.0

    def _score_tlt(self) -> int:
        """금리/채권 방향 (-1 ~ +1)."""
        tlt = self._get_close("TLT")
        if tlt.empty:
            return 0

        t = _safe_last(tlt)
        tm10 = _safe_last(tlt.rolling(10).mean())

        if not np.isnan(t) and not np.isnan(tm10):
            if t > tm10 * 1.01:
                return 1    # 채권 상승 = 금리하락 = 주식유리
            elif t < tm10 * 0.99:
                return -1
        return 0

    def _score_index(self) -> tuple[int, float]:
        """SPY 지수 추세 (-2 ~ +2) + RSI."""
        spy = self._get_close("SPY")
        if spy.empty:
            return 0, 50.0

        ma5, ma20, ma50, rsi = _calc_indicators(spy)
        s = _safe_last(spy)
        s5 = _safe_last(ma5)
        s20 = _safe_last(ma20)
        s50 = _safe_last(ma50)
        spy_rsi = _safe_last(rsi)

        if any(np.isnan(x) for x in [s, s5, s20, s50]):
            return 0, spy_rsi if not np.isnan(spy_rsi) else 50.0

        return _trend_score(s, s5, s20, s50), spy_rsi if not np.isnan(spy_rsi) else 50.0

    def _score_sector(self, sector_etf: str) -> int:
        """업종 ETF 추세 (-2 ~ +2)."""
        self._load_sectors()
        sec = self._sector_cache.get(sector_etf)
        if not sec:
            return 0

        sc = _safe_last(sec["close"])
        s5 = _safe_last(sec["ma5"])
        s20 = _safe_last(sec["ma20"])
        s50 = _safe_last(sec["ma50"])

        if any(np.isnan(x) for x in [sc, s5, s20, s50]):
            return 0
        return _trend_score(sc, s5, s20, s50)

    def _score_calendar(self) -> tuple[int, bool, bool]:
        """캘린더 효과 (-2 ~ +2) + OPEX/세마녀 여부."""
        now = datetime.now()
        score = 0

        # 요일 효과
        if now.weekday() == 0:  # 월요일
            score -= 1

        # 월초 (1~3일)
        if now.day <= 3:
            score += 1

        # OPEX / 세마녀
        is_opex, _ = _is_opex_week(now)
        is_tw = _is_triple_witching(now)
        if is_tw:
            score -= 1
        return score, is_opex, is_tw

    def _score_rotation(self, sector_etf: str) -> int:
        """섹터 로테이션 (-1 ~ +1)."""
        self._load_sectors()
        sec = self._sector_cache.get(sector_etf)
        if not sec:
            return 0

        sec_ret5 = _safe_last(sec["ret5"])
        if np.isnan(sec_ret5):
            return 0

        all_ret5 = []
        for etf, sdata in self._sector_cache.items():
            r = _safe_last(sdata["ret5"])
            if not np.isnan(r):
                all_ret5.append(r)

        if len(all_ret5) < 3:
            return 0

        avg = np.mean(all_ret5)
        if sec_ret5 > avg + 0.01:
            return 1   # 업종이 시장보다 1%p+ 강세
        elif sec_ret5 < avg - 0.01:
            return -1
        return 0

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def get_score(self, symbol: str | None = None) -> MarketScore:
        """심볼에 대한 시장 환경 점수 계산.

        symbol 이 없으면 전체 시장(SPY 기준)만 평가.
        symbol 이 있으면 해당 업종 ETF 도 반영.
        """
        sector_etf = SYMBOL_TO_SECTOR_ETF.get(symbol, "XLK") if symbol else "XLK"

        vix_score, vix_level = self._score_vix()
        tlt_score = self._score_tlt()
        idx_score, spy_rsi = self._score_index()
        sec_score = self._score_sector(sector_etf)
        cal_score, is_opex, is_tw = self._score_calendar()
        rot_score = self._score_rotation(sector_etf)

        total = vix_score + tlt_score + idx_score + sec_score + cal_score + rot_score

        return MarketScore(
            vix_score=vix_score,
            tlt_score=tlt_score,
            index_score=idx_score,
            sector_score=sec_score,
            calendar_score=cal_score,
            rotation_score=rot_score,
            total=total,
            vix_level=vix_level,
            spy_rsi=spy_rsi,
            is_opex=is_opex,
            is_triple_witching=is_tw,
            detail={
                "sector_etf": sector_etf,
                "symbol": symbol,
            },
        )

    def should_trade_option(self, news_direction: str, symbol: str) -> dict[str, Any]:
        """뉴스 방향 + 시장 환경 점수를 합산해서 옵션 진입 판단.

        Returns:
            {
                "allowed": bool,        # 옵션 진입 허용 여부
                "direction": str,       # "CALL" / "PUT" / "SKIP"
                "market_score": MarketScore,
                "combined_score": int,  # 뉴스(±2) + 시장(±12) 합산
                "qty_factor": float,    # 계약 수 보정 (0.5 ~ 1.5)
                "hold_days": int,       # 권장 보유일
                "reason": str,
            }
        """
        ms = self.get_score(symbol)

        # 뉴스 방향 → ±2
        news_score = 0
        if news_direction == "BULLISH":
            news_score = 2
        elif news_direction == "BEARISH":
            news_score = -2

        combined = ms.total + news_score

        # 방향 판단
        if combined >= 3:
            direction = "CALL"
        elif combined <= -3:
            direction = "PUT"
        else:
            direction = "SKIP"

        # 뉴스와 시장이 반대 방향이면 → 진입 차단
        if news_direction == "BULLISH" and ms.total <= -3:
            direction = "SKIP"
            reason = f"뉴스 BULLISH vs 시장 약세(score={ms.total}) → 충돌, 진입 보류"
        elif news_direction == "BEARISH" and ms.total >= 3:
            direction = "SKIP"
            reason = f"뉴스 BEARISH vs 시장 강세(score={ms.total}) → 충돌, 진입 보류"
        else:
            reason = f"뉴스({news_direction}) + 시장(score={ms.total}) = 합산 {combined}"

        # 확신도 기반 계약 수 보정
        abs_combined = abs(combined)
        if abs_combined >= 7:
            qty_factor = 1.5
            hold_days = 5
        elif abs_combined >= 5:
            qty_factor = 1.0
            hold_days = 4
        elif abs_combined >= 3:
            qty_factor = 0.75
            hold_days = 3
        else:
            qty_factor = 0.5
            hold_days = 2

        # OPEX 주간 페널티
        if ms.is_opex:
            qty_factor = max(0.5, qty_factor - 0.25)
            hold_days = min(hold_days, 3)
            reason += " | OPEX주간: 계약축소+보유단축"

        # 세마녀 주간
        if ms.is_triple_witching:
            hold_days = min(hold_days, 2)
            reason += " | 세마녀: 보유 2일 제한"

        # VIX 30+ → fear regime
        if ms.vix_level > 30:
            hold_days = min(hold_days, 3)
            reason += f" | VIX={ms.vix_level:.0f} FEAR"

        allowed = direction != "SKIP"

        return {
            "allowed": allowed,
            "direction": direction,
            "market_score": ms,
            "combined_score": combined,
            "qty_factor": round(qty_factor, 2),
            "hold_days": hold_days,
            "reason": reason,
        }
