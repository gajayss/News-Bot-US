from __future__ import annotations

import time

import config
from news_bridge.classifier import classify_news
from news_bridge.event_calendar import EventCalendarState
from news_bridge.file_bus import DailyJsonBus
from news_bridge.orchestrator import SignalOrchestrator
from news_bridge.sources.calendar_source import fetch_finnhub_calendar, fetch_sample_calendar
from news_bridge.sources.finnhub_source import fetch_finnhub_news
from news_bridge.sources.sample_source import fetch_sample_news
from news_bridge.utils import setup_logging

logger = setup_logging("news_radar")

CALENDAR_REFRESH_SEC = 3600  # 캘린더 1시간마다 갱신


def _load_seen_ids(bus: DailyJsonBus) -> set[str]:
    """Rebuild seen set from today's news_events file (survives restart)."""
    seen: set[str] = set()
    for item in bus.read_items("news_events"):
        nid = item.get("source_news_id") or item.get("headline", "")
        if nid:
            seen.add(nid)
    return seen


def _load_calendar() -> EventCalendarState:
    """Load economic calendar from source."""
    calendar = EventCalendarState(
        pre_event_block_hours=config.PRE_EVENT_BLOCK_HOURS,
        post_event_boost_hours=config.POST_EVENT_BOOST_HOURS,
    )
    try:
        if config.NEWS_SOURCE_MODE == "sample":
            events = fetch_sample_calendar()
        else:
            events = fetch_finnhub_calendar(config.FINNHUB_KEY, days_ahead=config.CALENDAR_DAYS_AHEAD)
        calendar.load_events(events)

        # Log upcoming events
        for ev in calendar.get_event_summary():
            if ev["status"] == "UPCOMING":
                logger.info(
                    "CALENDAR [%s] %s (충격%d) → %s %s UTC, %.1fh후",
                    ev["category"], ev["event_name"], ev["impact_level"],
                    ev["date"], ev["time"], ev["hours_until"],
                )
    except Exception as exc:
        logger.warning("Calendar load failed: %s", exc)
    return calendar


def main() -> None:
    bus = DailyJsonBus(config.INTERFACE_DIR, config.LOG_DIR)
    calendar = _load_calendar()
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
        calendar=calendar,
    )
    seen = _load_seen_ids(bus)
    last_calendar_refresh = time.time()

    # Log active constraints on startup
    constraint = calendar.get_active_constraints()
    if constraint["constrained"]:
        logger.warning("ACTIVE CONSTRAINT: %s", constraint["reason"])

    logger.info("News radar started. source_mode=%s, restored %d seen IDs", config.NEWS_SOURCE_MODE, len(seen))
    while True:
        try:
            # 캘린더 주기적 갱신
            if time.time() - last_calendar_refresh > CALENDAR_REFRESH_SEC:
                calendar = _load_calendar()
                orchestrator.calendar = calendar
                last_calendar_refresh = time.time()
                constraint = calendar.get_active_constraints()
                if constraint["constrained"]:
                    logger.warning("CALENDAR CONSTRAINT: %s", constraint["reason"])

            raw_news = fetch_sample_news() if config.NEWS_SOURCE_MODE == "sample" else fetch_finnhub_news(config.FINNHUB_KEY)
            for raw in raw_news:
                news_id = str(raw.get("id") or raw.get("headline") or raw.get("title") or "")
                if not news_id or news_id in seen:
                    continue
                event = classify_news(raw, config.WATCHLIST)
                seen.add(news_id)
                bus.append_item("news_events", event.to_dict())
                logger.info("EVENT [%s/%s] %s score=%.2f symbols=%s", event.axis_id, event.event_type, event.direction, event.score, event.symbols)
                for signal in orchestrator.build_signals(event):
                    target_file = "stock_signals" if signal.asset_class == "STOCK" else "option_signals"
                    bus.append_item(target_file, signal.to_dict())
                    logger.info("SIGNAL %s %s %s qty=%d", signal.asset_class, signal.symbol, signal.side, signal.qty)
        except Exception as exc:
            logger.exception("news_radar loop error: %s", exc)
        time.sleep(config.NEWS_POLL_SEC)


if __name__ == "__main__":
    main()
