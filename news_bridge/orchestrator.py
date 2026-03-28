from __future__ import annotations

from .models import NewsEvent, TradeSignal


class SignalOrchestrator:
    def __init__(
        self,
        confidence_threshold: float,
        neg_stock_threshold: float,
        pos_stock_threshold: float,
        neg_option_threshold: float,
        pos_option_threshold: float,
        max_signals_per_event: int = 3,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.neg_stock_threshold = neg_stock_threshold
        self.pos_stock_threshold = pos_stock_threshold
        self.neg_option_threshold = neg_option_threshold
        self.pos_option_threshold = pos_option_threshold
        self.max_signals_per_event = max_signals_per_event

    def build_signals(self, event: NewsEvent) -> list[TradeSignal]:
        if not event.tradable or event.confidence < self.confidence_threshold:
            return []

        signals: list[TradeSignal] = []
        selected_symbols = event.symbols[: self.max_signals_per_event]
        for symbol in selected_symbols:
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
                    )
                )

            if event.score <= self.neg_option_threshold:
                signals.append(
                    TradeSignal(
                        event_id=event.event_id,
                        asset_class="OPTION",
                        symbol=symbol,
                        side="BUY_PUT",
                        strength=abs(event.score),
                        confidence=event.confidence,
                        urgency=event.urgency,
                        reason=f"{event.event_type} bearish option hedge/speculation",
                        event_type=event.event_type,
                        option_expiry_type="WEEKLY" if event.event_type in {"GEOPOLITICAL", "FED"} else "MONTHLY",
                        option_right="PUT",
                    )
                )
            elif event.score >= self.pos_option_threshold:
                signals.append(
                    TradeSignal(
                        event_id=event.event_id,
                        asset_class="OPTION",
                        symbol=symbol,
                        side="BUY_CALL",
                        strength=abs(event.score),
                        confidence=event.confidence,
                        urgency=event.urgency,
                        reason=f"{event.event_type} bullish option speculation",
                        event_type=event.event_type,
                        option_expiry_type="WEEKLY" if event.event_type in {"GEOPOLITICAL", "FED"} else "MONTHLY",
                        option_right="CALL",
                    )
                )
        return signals
