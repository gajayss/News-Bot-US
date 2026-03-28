"""5-axis system integration test."""
from __future__ import annotations

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import config
from news_bridge.axes import AXES, detect_event_type, classify_axis
from news_bridge.classifier import classify_news
from news_bridge.orchestrator import SignalOrchestrator
from news_bridge.sources.sample_source import fetch_sample_news

AXIS_COLORS = {
    "ECONOMY": "\033[33m",    # yellow
    "CORPORATE": "\033[36m",  # cyan
    "GOVERN": "\033[31m",     # red
    "FEDWALL": "\033[35m",    # magenta
    "THEME": "\033[32m",      # green
    "UNKNOWN": "\033[37m",    # white
}
RESET = "\033[0m"
BOLD = "\033[1m"

def main():
    print(f"\n{'='*80}")
    print(f"{BOLD}  5-AXIS NEWS CLASSIFICATION SYSTEM TEST{RESET}")
    print(f"{'='*80}\n")

    # Show axis definitions
    print(f"{BOLD}[AXIS DEFINITIONS]{RESET}")
    for ax_id, ax in AXES.items():
        c = AXIS_COLORS.get(ax_id, "")
        print(f"  {c}{ax_id:12s}{RESET} | {ax.axis_name_kr:12s} | speed={ax.speed:6s} | "
              f"SL={ax.sl_modifier:.2f} TP={ax.tp_modifier:.2f} Hold={ax.hold_modifier:.2f} Qty={ax.qty_modifier:.2f} | "
              f"fear={ax.fear_eligible}")
    print()

    # Classify all sample news
    watchlist = config.WATCHLIST
    news_items = fetch_sample_news()

    orchestrator = SignalOrchestrator(
        confidence_threshold=config.CONFIDENCE_THRESHOLD,
        neg_stock_threshold=config.NEGATIVE_STOCK_THRESHOLD,
        pos_stock_threshold=config.POSITIVE_STOCK_THRESHOLD,
        neg_option_threshold=config.NEGATIVE_OPTION_THRESHOLD,
        pos_option_threshold=config.POSITIVE_OPTION_THRESHOLD,
        max_signals_per_event=config.MAX_SIGNALS_PER_EVENT,
        base_qty=config.BASE_QTY,
        max_qty=config.MAX_QTY,
        max_premium_pct=config.MAX_PREMIUM_PCT,
        stop_loss_pct=config.STOP_LOSS_PCT,
        take_profit_pct=config.TAKE_PROFIT_PCT,
        max_hold_days=config.MAX_HOLD_DAYS,
        fear_regime=config.FEAR_REGIME,
    )

    axis_counts = {ax: 0 for ax in list(AXES.keys()) + ["UNKNOWN"]}

    print(f"{BOLD}[NEWS CLASSIFICATION]{RESET}")
    print(f"{'─'*80}")
    for raw in news_items:
        event = classify_news(raw, watchlist)
        c = AXIS_COLORS.get(event.axis_id, AXIS_COLORS["UNKNOWN"])
        axis_counts[event.axis_id] = axis_counts.get(event.axis_id, 0) + 1

        print(f"  {c}[{event.axis_id:10s}/{event.event_type:14s}]{RESET} "
              f"score={event.score:+.2f} {event.direction:8s} "
              f"conf={event.confidence:.2f} trad={'Y' if event.tradable else 'N'} "
              f"sym={','.join(event.symbols) or '-':15s} "
              f"| {event.headline[:50]}")

        signals = orchestrator.build_signals(event)
        for sig in signals:
            plan_info = ""
            if sig.option_plan:
                p = sig.option_plan
                plan_info = f" | {p.get('strike_preference','')} {p.get('expiry_guidance','')} SL={p.get('stop_loss_pct',0):.0%} TP={p.get('take_profit_pct',0):.0%}"
            print(f"    -> {sig.asset_class:6s} {sig.symbol:6s} {sig.side:10s} qty={sig.qty} | {sig.reason[:60]}{plan_info}")

    print(f"\n{'─'*80}")
    print(f"{BOLD}[AXIS DISTRIBUTION]{RESET}")
    for ax_id, cnt in axis_counts.items():
        if cnt > 0:
            c = AXIS_COLORS.get(ax_id, "")
            bar = "█" * cnt
            print(f"  {c}{ax_id:12s}{RESET} {bar} ({cnt})")

    print(f"\n{BOLD}Total: {len(news_items)} news items{RESET}\n")


if __name__ == "__main__":
    main()
