from __future__ import annotations

import logging
import math
from typing import Any

from .axes import AXES, DEFAULT_AXIS, apply_axis_modifiers, classify_axis
from .dedup_guard import DedupGuard
from .event_calendar import EventCalendarState
from .market_context import MarketContextEngine, MarketScore
from .models import NewsEvent, TradeSignal
from .option_strategy import build_option_plan
from .sector_map import get_sector_info
from .source_reliability import (
    adjust_confidence,
    get_source_info,
    is_reliable_enough,
)

logger = logging.getLogger("orchestrator")


class SignalOrchestrator:
    def __init__(
        self,
        confidence_threshold: float,
        neg_stock_threshold: float,
        pos_stock_threshold: float,
        neg_option_threshold: float,
        pos_option_threshold: float,
        max_signals_per_event: int = 3,
        base_qty: int = 1,
        max_qty: int = 2,
        max_premium_pct: float = 0.03,
        stop_loss_pct: float = -0.30,
        take_profit_pct: float = 0.40,
        max_hold_days: int = 10,
        fear_regime: bool = False,
        calendar: EventCalendarState | None = None,
        # --- V4 통합 ---
        market_engine: MarketContextEngine | None = None,
        dedup_guard: DedupGuard | None = None,
        use_market_context: bool = True,
        market_score_threshold: int = 3,   # |합산| >= 3 이어야 옵션 진입
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.neg_stock_threshold = neg_stock_threshold
        self.pos_stock_threshold = pos_stock_threshold
        self.neg_option_threshold = neg_option_threshold
        self.pos_option_threshold = pos_option_threshold
        self.max_signals_per_event = max_signals_per_event
        self.base_qty = base_qty
        self.max_qty = max_qty
        self.max_premium_pct = max_premium_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_hold_days = max_hold_days
        self.fear_regime = fear_regime
        self.calendar = calendar

        # V4 시장 환경 엔진
        self._market_engine = market_engine or MarketContextEngine()
        self._dedup = dedup_guard or DedupGuard()
        self._use_market_ctx = use_market_context
        self._market_threshold = market_score_threshold

    def build_signals(self, event: NewsEvent) -> list[TradeSignal]:
        if not event.tradable or event.confidence < self.confidence_threshold:
            return []

        # ===================================================================
        # 0. 출처 신뢰도 필터
        # ===================================================================
        source_info = get_source_info(event.source)
        if not is_reliable_enough(event.source):
            logger.info(
                "Low reliability source '%s' (tier=%d) → skip: %s",
                event.source, source_info["tier"], event.headline[:60],
            )
            return []

        # confidence 를 출처 신뢰도로 보정
        adj_confidence = adjust_confidence(event.confidence, event.source)
        if adj_confidence < self.confidence_threshold:
            logger.info(
                "Confidence degraded by source reliability: %.2f → %.2f (threshold=%.2f)",
                event.confidence, adj_confidence, self.confidence_threshold,
            )
            return []

        # ===================================================================
        # 1. 뉴스 중복 검사
        # ===================================================================
        is_dup, dup_reason = self._dedup.is_duplicate(
            news_id=event.source_news_id,
            headline=event.headline,
            symbols=event.symbols,
            direction=event.direction,
        )
        if is_dup:
            logger.info("Duplicate detected → skip: %s | %s", dup_reason, event.headline[:60])
            return []

        # ===================================================================
        # 2. 5축 프로파일 로드
        # ===================================================================
        axis = AXES.get(event.axis_id, DEFAULT_AXIS)
        adj_sl, adj_tp, adj_hold, adj_qty = apply_axis_modifiers(
            axis, self.stop_loss_pct, self.take_profit_pct,
            self.max_hold_days, self.max_qty,
        )

        # ===================================================================
        # 3. 캘린더 제약 조건 확인
        # ===================================================================
        cal_constraint = self._get_calendar_constraint()

        # ===================================================================
        # 4. V4 시장 환경 스코어링 (핵심 통합 지점)
        # ===================================================================
        # 첫 번째 심볼 기준으로 시장 점수 계산 (같은 이벤트는 같은 시장 환경)
        primary_symbol = event.symbols[0] if event.symbols else None
        market_ctx = None
        if self._use_market_ctx and primary_symbol:
            market_ctx = self._market_engine.should_trade_option(
                news_direction=event.direction,
                symbol=primary_symbol,
            )
            logger.info(
                "V4 Market Context: %s | combined=%d | direction=%s | %s",
                primary_symbol,
                market_ctx["combined_score"],
                market_ctx["direction"],
                market_ctx["reason"],
            )

        signals: list[TradeSignal] = []
        selected_symbols = event.symbols[: self.max_signals_per_event]

        is_recovery = (
            0.20 <= event.score <= 0.50
            and event.event_type in {"GEOPOLITICAL", "REGULATION"}
        )

        for symbol in selected_symbols:
            # 섹터/산업군 조회 (종목별)
            _sector, _industry = get_sector_info(symbol)

            # --- STOCK signals (V4 영향 없이 뉴스 기반) ---
            stock_qty = self.base_qty
            if event.score <= self.neg_stock_threshold:
                signals.append(
                    TradeSignal(
                        event_id=event.event_id,
                        asset_class="STOCK",
                        symbol=symbol,
                        side="SELL",
                        strength=abs(event.score),
                        confidence=adj_confidence,
                        urgency=event.urgency,
                        reason=f"[{axis.axis_id}|{source_info['tier_label']}] {event.event_type} bearish",
                        event_type=event.event_type,
                        axis_id=event.axis_id,
                        qty=stock_qty,
                        sector=_sector,
                        industry=_industry,
                    )
                )
            elif event.score >= self.pos_stock_threshold:
                signals.append(
                    TradeSignal(
                        event_id=event.event_id,
                        asset_class="STOCK",
                        symbol=symbol,
                        side="BUY",
                        strength=abs(event.score),
                        confidence=adj_confidence,
                        urgency=event.urgency,
                        reason=f"[{axis.axis_id}|{source_info['tier_label']}] {event.event_type} bullish",
                        event_type=event.event_type,
                        axis_id=event.axis_id,
                        qty=stock_qty,
                        sector=_sector,
                        industry=_industry,
                    )
                )

            # --- OPTION signals (V4 시장 환경 + 5축 + 뉴스 감성 합산) ---
            option_side = None
            option_right = None
            direction = None

            if market_ctx and self._use_market_ctx:
                # ★ V4 합산 방향 사용: 뉴스 + 시장 환경 결합
                if not market_ctx["allowed"]:
                    logger.info(
                        "V4 blocks option for %s: %s", symbol, market_ctx["reason"],
                    )
                    # 시장과 뉴스 충돌 → 옵션 차단, 주식으로 전환 검토
                    continue

                v4_direction = market_ctx["direction"]
                if v4_direction == "CALL":
                    option_side = "BUY_CALL"
                    option_right = "CALL"
                    direction = "BULLISH"
                elif v4_direction == "PUT":
                    option_side = "BUY_PUT"
                    option_right = "PUT"
                    direction = "BEARISH"
                # SKIP → 옵션 진입 안 함
            else:
                # fallback: 기존 뉴스 스코어 기반
                if event.score <= self.neg_option_threshold:
                    option_side = "BUY_PUT"
                    option_right = "PUT"
                    direction = "BEARISH"
                elif event.score >= self.pos_option_threshold:
                    option_side = "BUY_CALL"
                    option_right = "CALL"
                    direction = "BULLISH"

            if not option_side:
                continue

            # 축별 조정값 기반 + 캘린더 제약 적용
            effective_max_qty = adj_qty
            effective_hold = adj_hold
            effective_sl = adj_sl
            effective_tp = adj_tp
            cal_note = ""

            # V4 확신도에 따른 계약 수/보유일 보정
            if market_ctx:
                qty_factor = market_ctx["qty_factor"]
                effective_max_qty = max(1, math.floor(effective_max_qty * qty_factor))
                effective_hold = min(effective_hold, market_ctx["hold_days"])

                ms: MarketScore = market_ctx["market_score"]
                # VIX 30+ → fear regime 강제 활성화
                if ms.vix_level > 30:
                    self.fear_regime = True

            if cal_constraint["constrained"]:
                action = cal_constraint["action"]
                if action == "BLOCK":
                    # 신규 옵션 진입 차단 → 주식으로 전환
                    already_has_stock = any(
                        s.symbol == symbol and s.asset_class == "STOCK"
                        for s in signals
                    )
                    if not already_has_stock:
                        stock_side = "SELL" if direction == "BEARISH" else "BUY"
                        signals.append(
                            TradeSignal(
                                event_id=event.event_id,
                                asset_class="STOCK",
                                symbol=symbol,
                                side=stock_side,
                                strength=abs(event.score),
                                confidence=adj_confidence,
                                urgency=event.urgency,
                                reason=f"[{axis.axis_id}|이벤트차단→주식] {cal_constraint['reason']}",
                                event_type=event.event_type,
                                axis_id=event.axis_id,
                                qty=self.base_qty,
                                sector=_sector,
                                industry=_industry,
                            )
                        )
                    continue  # skip option signal for this symbol

                elif action == "REDUCE":
                    qty_red = cal_constraint["qty_reduction_pct"]
                    hold_red = cal_constraint["hold_reduction_pct"]
                    effective_max_qty = max(1, math.floor(effective_max_qty * (1 - qty_red)))
                    effective_hold = max(2, math.floor(effective_hold * (1 - hold_red)))
                    cal_note = f" | [이벤트축소] {cal_constraint['reason']}"

            plan = build_option_plan(
                event_type=event.event_type,
                direction=direction,
                score=event.score,
                confidence=adj_confidence,
                urgency=event.urgency,
                is_recovery=is_recovery,
                base_qty=self.base_qty,
                max_qty=effective_max_qty,
                max_premium_pct=self.max_premium_pct,
                stop_loss_pct=effective_sl,
                take_profit_pct=effective_tp,
                max_hold_days=effective_hold,
                fear_regime=self.fear_regime,
            )

            if plan.asset_recommendation == "STOCK_PREFERRED":
                already_has_stock = any(
                    s.symbol == symbol and s.asset_class == "STOCK"
                    for s in signals
                )
                if not already_has_stock:
                    stock_side = "SELL" if direction == "BEARISH" else "BUY"
                    signals.append(
                        TradeSignal(
                            event_id=event.event_id,
                            asset_class="STOCK",
                            symbol=symbol,
                            side=stock_side,
                            strength=abs(event.score),
                            confidence=adj_confidence,
                            urgency=event.urgency,
                            reason=f"[{axis.axis_id}|옵션→주식] {plan.recommendation_reason}{cal_note}",
                            event_type=event.event_type,
                            axis_id=event.axis_id,
                            qty=plan.qty,
                            sector=_sector,
                            industry=_industry,
                            option_plan=plan.to_dict(),
                        )
                    )
            else:
                # 축별 + V4 + 캘린더 제약을 option_plan에 기록
                plan_dict = plan.to_dict()
                plan_dict["axis_modifiers"] = axis.to_dict()

                # V4 시장 환경 정보 기록
                if market_ctx:
                    ms = market_ctx["market_score"]
                    plan_dict["market_context"] = {
                        "combined_score": market_ctx["combined_score"],
                        "vix_score": ms.vix_score,
                        "tlt_score": ms.tlt_score,
                        "index_score": ms.index_score,
                        "sector_score": ms.sector_score,
                        "calendar_score": ms.calendar_score,
                        "rotation_score": ms.rotation_score,
                        "total_market": ms.total,
                        "vix_level": ms.vix_level,
                        "conviction": ms.conviction,
                        "qty_factor": market_ctx["qty_factor"],
                        "hold_days": market_ctx["hold_days"],
                        "is_opex": ms.is_opex,
                        "is_triple_witching": ms.is_triple_witching,
                    }

                if cal_constraint["constrained"]:
                    plan_dict["calendar_constraint"] = {
                        "action": cal_constraint["action"],
                        "event_name": cal_constraint["event_name"],
                        "hours_until": cal_constraint["hours_until"],
                        "reason": cal_constraint["reason"],
                    }

                # 출처 신뢰도 기록
                plan_dict["source_reliability"] = source_info

                # V4 방향 정보를 reason에 포함
                v4_note = ""
                if market_ctx:
                    ms = market_ctx["market_score"]
                    v4_note = f" | V4={market_ctx['combined_score']:+d}(VIX{ms.vix_score:+d}/금리{ms.tlt_score:+d}/지수{ms.index_score:+d}/업종{ms.sector_score:+d}/달력{ms.calendar_score:+d}/섹터{ms.rotation_score:+d})"

                signals.append(
                    TradeSignal(
                        event_id=event.event_id,
                        asset_class="OPTION",
                        symbol=symbol,
                        side=option_side,
                        strength=abs(event.score),
                        confidence=adj_confidence,
                        urgency=event.urgency,
                        reason=f"[{axis.axis_id}] {event.event_type} {direction.lower()} | {plan.strike_preference} {plan.expiry_guidance}{cal_note}{v4_note}",
                        event_type=event.event_type,
                        axis_id=event.axis_id,
                        option_expiry_type=plan.expiry_type,
                        option_right=option_right,
                        qty=plan.qty,
                        sector=_sector,
                        industry=_industry,
                        option_plan=plan_dict,
                    )
                )

        # ===================================================================
        # 5. 중복 등록 — 시그널 생성 성공 시 등록
        # ===================================================================
        if signals:
            self._dedup.register(
                news_id=event.source_news_id,
                headline=event.headline,
                symbols=event.symbols,
                direction=event.direction,
            )

        return signals

    def _get_calendar_constraint(self) -> dict[str, Any]:
        """Get active calendar constraint, or empty if no calendar."""
        if self.calendar is None:
            return {
                "constrained": False, "action": "NONE", "event_name": "",
                "category": "", "impact_level": 0, "hours_until": 999,
                "event_time": "", "qty_reduction_pct": 0.0,
                "hold_reduction_pct": 0.0, "reason": "",
            }
        return self.calendar.get_active_constraints()
