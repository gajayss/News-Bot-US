from __future__ import annotations

import math
from typing import Any

from .event_calendar import EventCalendarState
from .models import NewsEvent, TradeSignal
from .option_strategy import build_option_plan


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

    def build_signals(self, event: NewsEvent) -> list[TradeSignal]:
        if not event.tradable or event.confidence < self.confidence_threshold:
            return []

        # --- 캘린더 제약 조건 확인 ---
        cal_constraint = self._get_calendar_constraint()

        signals: list[TradeSignal] = []
        selected_symbols = event.symbols[: self.max_signals_per_event]

        is_recovery = (
            0.20 <= event.score <= 0.50
            and event.event_type in {"GEOPOLITICAL", "REGULATION"}
        )

        for symbol in selected_symbols:
            # --- STOCK signals ---
            stock_qty = self.base_qty
            if event.score <= self.neg_stock_threshold:
                signals.append(
                    TradeSignal(
                        event_id=event.event_id,
                        asset_class="STOCK",
                        symbol=symbol,
                        side="SELL",
                        strength=abs(event.score),
                        confidence=event.confidence,
                        urgency=event.urgency,
                        reason=f"{event.event_type} bearish news",
                        event_type=event.event_type,
                        qty=stock_qty,
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
                        confidence=event.confidence,
                        urgency=event.urgency,
                        reason=f"{event.event_type} bullish news",
                        event_type=event.event_type,
                        qty=stock_qty,
                    )
                )

            # --- OPTION signals ---
            option_side = None
            option_right = None
            direction = None

            if event.score <= self.neg_option_threshold:
                option_side = "BUY_PUT"
                option_right = "PUT"
                direction = "BEARISH"
            elif event.score >= self.pos_option_threshold:
                option_side = "BUY_CALL"
                option_right = "CALL"
                direction = "BULLISH"

            if option_side:
                # 캘린더 제약 적용
                effective_max_qty = self.max_qty
                effective_hold = self.max_hold_days
                cal_note = ""

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
                                    confidence=event.confidence,
                                    urgency=event.urgency,
                                    reason=f"[이벤트차단→주식] {cal_constraint['reason']}",
                                    event_type=event.event_type,
                                    qty=self.base_qty,
                                )
                            )
                        continue  # skip option signal for this symbol

                    elif action == "REDUCE":
                        qty_red = cal_constraint["qty_reduction_pct"]
                        hold_red = cal_constraint["hold_reduction_pct"]
                        effective_max_qty = max(1, math.floor(self.max_qty * (1 - qty_red)))
                        effective_hold = max(2, math.floor(self.max_hold_days * (1 - hold_red)))
                        cal_note = f" | [이벤트축소] {cal_constraint['reason']}"

                plan = build_option_plan(
                    event_type=event.event_type,
                    direction=direction,
                    score=event.score,
                    confidence=event.confidence,
                    urgency=event.urgency,
                    is_recovery=is_recovery,
                    base_qty=self.base_qty,
                    max_qty=effective_max_qty,
                    max_premium_pct=self.max_premium_pct,
                    stop_loss_pct=self.stop_loss_pct,
                    take_profit_pct=self.take_profit_pct,
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
                                confidence=event.confidence,
                                urgency=event.urgency,
                                reason=f"[옵션→주식 전환] {plan.recommendation_reason}{cal_note}",
                                event_type=event.event_type,
                                qty=plan.qty,
                                option_plan=plan.to_dict(),
                            )
                        )
                else:
                    # 캘린더 제약을 option_plan에 기록
                    plan_dict = plan.to_dict()
                    if cal_constraint["constrained"]:
                        plan_dict["calendar_constraint"] = {
                            "action": cal_constraint["action"],
                            "event_name": cal_constraint["event_name"],
                            "hours_until": cal_constraint["hours_until"],
                            "reason": cal_constraint["reason"],
                        }

                    signals.append(
                        TradeSignal(
                            event_id=event.event_id,
                            asset_class="OPTION",
                            symbol=symbol,
                            side=option_side,
                            strength=abs(event.score),
                            confidence=event.confidence,
                            urgency=event.urgency,
                            reason=f"{event.event_type} {direction.lower()} | {plan.strike_preference} {plan.expiry_guidance}{cal_note}",
                            event_type=event.event_type,
                            option_expiry_type=plan.expiry_type,
                            option_right=option_right,
                            qty=plan.qty,
                            option_plan=plan_dict,
                        )
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
