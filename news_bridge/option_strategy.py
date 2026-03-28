"""Option strategy engine — entry, exit, sizing, holding period.

Core philosophy (실전 경험 기반):
  1. 먼 만기 옵션 = 프리미엄 덩어리. 올라도 남는 게 없다
  2. 폭락 맞은 종목 반등 = 느리다. 옵션으로 하면 시간가치에 먹힌다 → 주식이 답
  3. 뉴스 무빙은 빠르다. DTE를 이벤트 속도에 맞춰라
  4. -30% 물타기 금지. 손절 후 더 싸게 재진입이 정답
  5. 수량은 1~2계약. 소액으로 확률 싸움
  6. 익절 50% 잡고 빨리 나와라. 욕심 부리면 시간가치가 먹는다
  7. 횡보/하락 중이면 빠른 손절 → 추가 하락 후 재진입
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class OptionPlan:
    """Concrete option entry + exit plan."""

    # --- 진입 판단 ---
    asset_recommendation: str       # "OPTION" or "STOCK_PREFERRED"
    recommendation_reason: str

    # --- 행사가 ---
    strike_preference: str          # "ATM", "OTM_1", "ITM_1"
    strike_delta_target: float      # target delta

    # --- 만기 ---
    target_dte_min: int
    target_dte_max: int
    target_dte_ideal: int
    expiry_type: str                # "WEEKLY" or "MONTHLY"
    expiry_guidance: str

    # --- 프리미엄 ---
    premium_max_pct: float          # max premium as % of underlying
    premium_note: str

    # --- 수량 ---
    qty: int
    qty_reason: str

    # --- 손절 / 익절 / 보유기간 ---
    stop_loss_pct: float            # e.g. -0.30 = -30%
    take_profit_pct: float          # e.g. 0.50 = +50%
    max_hold_days: int              # 최대 보유일
    exit_strategy: str              # 청산 전략 설명
    reentry_rule: str               # 재진입 규칙

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_recommendation": self.asset_recommendation,
            "recommendation_reason": self.recommendation_reason,
            "strike_preference": self.strike_preference,
            "strike_delta_target": self.strike_delta_target,
            "target_dte_min": self.target_dte_min,
            "target_dte_max": self.target_dte_max,
            "target_dte_ideal": self.target_dte_ideal,
            "expiry_type": self.expiry_type,
            "expiry_guidance": self.expiry_guidance,
            "premium_max_pct": self.premium_max_pct,
            "premium_note": self.premium_note,
            "qty": self.qty,
            "qty_reason": self.qty_reason,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "max_hold_days": self.max_hold_days,
            "exit_strategy": self.exit_strategy,
            "reentry_rule": self.reentry_rule,
        }


# ---------------------------------------------------------------------------
# Event speed profile
# ---------------------------------------------------------------------------
EVENT_SPEED = {
    "GEOPOLITICAL":   "FAST",     # hours ~ 1-2 days
    "FED":            "FAST",     # same day, FOMC reaction
    "TRUMP":          "FAST",     # 예측 불가, 순간 변동
    "FOMC":           "FAST",     # FOMC 후 즉시 반응
    "FED_SPEAK":      "FAST",     # 매파/비둘기 즉시 반영
    "REGULATION":     "MEDIUM",   # 1-3 days
    "EARNINGS":       "MEDIUM",   # overnight gap + 1-3 days drift
    "SHORT_SELLING":  "FAST",     # 공매도 리포트 → 엄청 폭락, 당일~2일 승부
    "HEDGEFUND":      "MEDIUM",   # 헤지펀드 포지션 변경 → 1-3일 반영
    "ANALYST":        "SLOW",     # days ~ weeks
    "INSIDER":        "SLOW",     # 내부자 매도 → 서서히 하락 (1-2달), 옵션은 DTE 길게
    "GENERAL":        "SLOW",
}

# Fear regime: GEOPOLITICAL/FED events or very strong negative score
# → tighter SL, shorter hold, faster exit
FEAR_EVENT_TYPES = {"GEOPOLITICAL", "FED", "SHORT_SELLING", "TRUMP"}


def build_option_plan(
    event_type: str,
    direction: str,         # "BULLISH" or "BEARISH"
    score: float,           # -1.0 ~ +1.0
    confidence: float,      # 0.0 ~ 1.0
    urgency: float,         # 0.0 ~ 1.0
    is_recovery: bool,
    base_qty: int = 1,
    max_qty: int = 2,
    max_premium_pct: float = 0.03,
    stop_loss_pct: float = -0.25,
    take_profit_pct: float = 0.40,
    max_hold_days: int = 10,
    fear_regime: bool = False,
) -> OptionPlan:
    """Determine optimal option entry + exit plan."""

    speed = EVENT_SPEED.get(event_type, "SLOW")
    abs_score = abs(score)

    # Fear regime: 지정학/FED 이벤트 또는 수동 설정 시만 적용
    # EARNINGS/ANALYST 등은 score 높아도 FEAR 아님 (시장 공포와 다름)
    is_fear = fear_regime or event_type in FEAR_EVENT_TYPES

    # ===================================================================
    # 1. STOCK vs OPTION decision
    # ===================================================================
    if is_recovery:
        return _stock_preferred(
            reason="반등 회복 구간: 프리미엄 소진 > 회복 속도. 물타기 금지, 주식 직접매매 권장",
            score=abs_score, confidence=confidence,
            base_qty=base_qty, max_qty=max_qty,
            stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
            max_hold_days=max_hold_days,
        )

    # 헤지펀드/공매도/내부자 매도 → 옵션 진입 기준 완화 (방향성 확실)
    _high_impact_types = {"HEDGEFUND", "SHORT_SELLING", "INSIDER"}
    is_high_impact = event_type in _high_impact_types

    if not is_high_impact and (abs_score < 0.60 or confidence < 0.65):
        return _stock_preferred(
            reason=f"약한 신호(score={score:+.2f}, conf={confidence:.2f}): 옵션 프리미엄 리스크 > 기대수익",
            score=abs_score, confidence=confidence,
            base_qty=base_qty, max_qty=max_qty,
            stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
            max_hold_days=max_hold_days,
        )

    if speed == "SLOW" and abs_score < 0.85 and not is_high_impact:
        return _stock_preferred(
            reason=f"느린 촉매({event_type})+중간 강도: 시간가치 잠식이 방향성 이익 초과",
            score=abs_score, confidence=confidence,
            base_qty=base_qty, max_qty=max_qty,
            stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
            max_hold_days=max_hold_days,
        )

    # ===================================================================
    # 2. EXPIRY & DTE
    # ===================================================================
    if speed == "FAST":
        expiry_type = "WEEKLY"
        dte_min, dte_ideal, dte_max = 2, 5, 10
        expiry_guidance = "이번 주 또는 다음 주 금요일 만기 (DTE 2~10일)"
    elif speed == "MEDIUM":
        if abs_score >= 0.85:
            expiry_type = "WEEKLY"
            dte_min, dte_ideal, dte_max = 5, 8, 14
            expiry_guidance = "다음 주 금요일 만기 (DTE 5~14일)"
        else:
            expiry_type = "MONTHLY"
            dte_min, dte_ideal, dte_max = 14, 21, 30
            expiry_guidance = "당월물 (DTE 14~30일), 30일 초과 금지"
    else:
        expiry_type = "MONTHLY"
        dte_min, dte_ideal, dte_max = 14, 21, 30
        expiry_guidance = "당월물 (DTE 14~30일), 프리미엄 과다 구간 회피"

    # ===================================================================
    # 3. STRIKE selection
    # ===================================================================
    if confidence >= 0.90 and abs_score >= 0.90:
        strike_pref = "ITM_1"
        delta_target = 0.60
        strike_note = "ITM 1단계: 높은 확신, 델타 0.60, 시간가치 최소화"
    elif confidence >= 0.80 and abs_score >= 0.80:
        strike_pref = "ATM"
        delta_target = 0.50
        strike_note = "ATM: 델타 0.50, 방향성 + 비용 균형"
    elif confidence >= 0.70:
        strike_pref = "OTM_1"
        delta_target = 0.35
        strike_note = "OTM 1단계: 프리미엄 절약, 델타 0.35"
    else:
        strike_pref = "OTM_2"
        delta_target = 0.25
        strike_note = "OTM 2단계: 최소 프리미엄"

    # Fast events → ATM 이상 강제 (OTM은 단기 급등락에서 반응 부족)
    if speed == "FAST" and strike_pref.startswith("OTM"):
        strike_pref = "ATM"
        delta_target = 0.50
        strike_note = "빠른 이벤트: ATM 강제 (OTM은 단기 변동에 반응 부족)"

    # ===================================================================
    # 4. PREMIUM guard
    # ===================================================================
    if dte_ideal <= 7:
        premium_cap = max_premium_pct
        premium_note = f"단기(DTE≤7): 시간가치 최소, 기초자산 대비 {premium_cap*100:.1f}% 이내"
    elif dte_ideal <= 14:
        premium_cap = max_premium_pct * 0.8
        premium_note = f"중기(DTE 8~14): 기초자산 대비 {premium_cap*100:.1f}% 이내"
    else:
        premium_cap = max_premium_pct * 0.6
        premium_note = f"월물(DTE 15~30): 기초자산 대비 {premium_cap*100:.1f}% 이내 엄수"

    # ===================================================================
    # 5. QUANTITY — 1~2계약, 소액 확률 싸움
    # ===================================================================
    sizing_factor = confidence * abs_score
    if sizing_factor >= 0.85:
        qty = max_qty      # max (2)
        qty_reason = f"강한 확신(conf×score={sizing_factor:.2f}): {max_qty}계약"
    else:
        qty = base_qty     # min (1)
        qty_reason = f"기본(conf×score={sizing_factor:.2f}): {base_qty}계약"

    # ===================================================================
    # 6. EXIT STRATEGY — 손절/익절/보유기간/재진입
    # ===================================================================
    sl, tp, hold = _calc_exit(
        speed=speed,
        abs_score=abs_score,
        confidence=confidence,
        is_fear=is_fear,
        base_sl=stop_loss_pct,
        base_tp=take_profit_pct,
        base_hold=max_hold_days,
        dte_ideal=dte_ideal,
    )

    exit_strategy, reentry_rule = _build_exit_text(sl, tp, hold, is_fear, speed)

    return OptionPlan(
        asset_recommendation="OPTION",
        recommendation_reason=f"{event_type} {direction} score={score:+.2f} conf={confidence:.2f} → {strike_note}",
        strike_preference=strike_pref,
        strike_delta_target=delta_target,
        target_dte_min=dte_min,
        target_dte_max=dte_max,
        target_dte_ideal=dte_ideal,
        expiry_type=expiry_type,
        expiry_guidance=expiry_guidance,
        premium_max_pct=round(premium_cap, 4),
        premium_note=premium_note,
        qty=qty,
        qty_reason=qty_reason,
        stop_loss_pct=round(sl, 2),
        take_profit_pct=round(tp, 2),
        max_hold_days=hold,
        exit_strategy=exit_strategy,
        reentry_rule=reentry_rule,
    )


# -----------------------------------------------------------------------
# Exit calculation
# -----------------------------------------------------------------------

def _calc_exit(
    speed: str,
    abs_score: float,
    confidence: float,
    is_fear: bool,
    base_sl: float,
    base_tp: float,
    base_hold: int,
    dte_ideal: int,
) -> tuple[float, float, int]:
    """Return (stop_loss_pct, take_profit_pct, max_hold_days)."""

    sl = base_sl     # default -0.25
    tp = base_tp     # default  0.40
    hold = base_hold # default  10 days

    # --- Fear regime: 모든 것을 타이트하게 ---
    if is_fear:
        sl = max(sl, -0.20)          # -20% (더 빨리 끊어)
        tp = min(tp, 0.35)           # +35% (욕심 금지)
        hold = max(2, hold // 2)     # 보유기간 절반

    # --- Speed 기반 조정 (FEAR 아닐 때) ---
    if not is_fear:
        if speed == "FAST":
            sl = max(sl, -0.25)       # NORMAL+FAST: -25%
            tp = min(tp, 0.40)
            hold = min(hold, 7)
        elif speed == "MEDIUM":
            sl = max(sl, -0.30)       # NORMAL+MEDIUM: -30% (max 허용)
            tp = min(tp, 0.45)
            hold = min(hold, 10)
        # SLOW: base values 유지
    else:
        if speed == "FAST":
            hold = min(hold, 5)       # FEAR+FAST: 5일 이내
        elif speed == "MEDIUM":
            hold = min(hold, 7)

    # --- 보유기간은 DTE를 넘을 수 없음 ---
    hold = min(hold, dte_ideal)

    # --- 절대 한도 ---
    sl = max(sl, -0.30)    # 손절 max -30% (절대 상한)
    tp = min(tp, 0.50)     # 익절 max +50%
    hold = min(hold, 10)   # 최대 10일 (10주일 아님, 거래일 기준)

    return sl, tp, hold


def _build_exit_text(
    sl: float,
    tp: float,
    hold: int,
    is_fear: bool,
    speed: str,
) -> tuple[str, str]:
    """Build human-readable exit strategy and reentry rule."""

    regime = "FEAR" if is_fear else "NORMAL"

    exit_lines = [
        f"[{regime}] 손절: {sl*100:+.0f}% | 익절: +{tp*100:.0f}% | 최대보유: {hold}거래일",
    ]

    # 분할 익절 규칙
    tp_half = tp * 0.5  # 1차 익절 = 목표의 절반
    exit_lines.append(f"분할익절: +{tp_half*100:.0f}%에서 절반 청산 → 나머지 +{tp*100:.0f}%까지 트레일링")
    exit_lines.append(f"트레일링 스탑: 고점 대비 -{abs(sl)*100/2:.0f}% 하락 시 잔여 전량 청산")

    if speed == "FAST":
        exit_lines.append("빠른 이벤트: 당일~2일 내 방향 안 나오면 손절 전환")
    elif speed == "MEDIUM":
        exit_lines.append("3일 내 방향 미확인 시 타임스탑(보유만으로 -10% 이상이면 청산)")
    else:
        exit_lines.append("5일 내 +10% 미달 시 청산 검토")

    exit_lines.append("횡보/하락 손실 중: 즉시 손절, 물타기 절대 금지")

    reentry_lines = [
        f"손절(-{abs(sl)*100:.0f}%) 후 기초자산 추가 하락 시 재진입 허용",
        "재진입 조건: 기초자산이 손절 시점 대비 추가 -3% 이상 하락 후",
        "재진입 수량: 동일(1~2계약), 절대 물타기 아님 (새 포지션)",
        "같은 종목 같은 방향 재진입은 1회까지만",
    ]

    return " | ".join(exit_lines), " | ".join(reentry_lines)


# -----------------------------------------------------------------------
# Stock preferred
# -----------------------------------------------------------------------

def _stock_preferred(
    reason: str,
    score: float,
    confidence: float,
    base_qty: int,
    max_qty: int,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_hold_days: int,
) -> OptionPlan:
    """Return a plan that recommends stock over options."""
    sizing_factor = confidence * score
    if sizing_factor >= 0.80:
        qty = min(max_qty * 3, base_qty * 5)
    elif sizing_factor >= 0.60:
        qty = min(max_qty * 2, base_qty * 3)
    else:
        qty = base_qty

    # 주식은 옵션보다 느슨하게
    sl = max(stop_loss_pct * 0.5, -0.15)   # 주식 손절 절반 수준
    tp = min(take_profit_pct * 1.5, 0.50)   # 주식 익절은 좀 더 여유

    return OptionPlan(
        asset_recommendation="STOCK_PREFERRED",
        recommendation_reason=reason,
        strike_preference="N/A",
        strike_delta_target=0.0,
        target_dte_min=0,
        target_dte_max=0,
        target_dte_ideal=0,
        expiry_type="N/A",
        expiry_guidance="옵션 비권장 → 주식 직접매매",
        premium_max_pct=0.0,
        premium_note="해당없음 (주식 권장)",
        qty=qty,
        qty_reason=f"주식 수량: conf×score={sizing_factor:.2f}",
        stop_loss_pct=round(sl, 2),
        take_profit_pct=round(tp, 2),
        max_hold_days=max_hold_days,
        exit_strategy=f"주식 손절: {sl*100:+.0f}% | 익절: +{tp*100:.0f}% | 물타기 금지, 손절 후 재진입",
        reentry_rule="손절 후 추가 -5% 하락 시 재진입 허용 | 1회 제한",
    )
