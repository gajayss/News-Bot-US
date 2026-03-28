from __future__ import annotations

import time

import config
from news_bridge.classifier import classify_news
from news_bridge.file_bus import DailyJsonBus
from news_bridge.orchestrator import SignalOrchestrator
from news_bridge.sources.finnhub_source import fetch_finnhub_news
from news_bridge.sources.sample_source import fetch_sample_news
from news_bridge.utils import setup_logging

logger = setup_logging("news_radar")


def _load_seen_ids(bus: DailyJsonBus) -> set[str]:
    """Rebuild seen set from today's news_events file (survives restart)."""
    seen: set[str] = set()
    for item in bus.read_items("news_events"):
        nid = item.get("source_news_id") or item.get("headline", "")
        if nid:
            seen.add(nid)
    return seen


def main() -> None:
    bus = DailyJsonBus(config.INTERFACE_DIR, config.LOG_DIR)
    orchestrator = SignalOrchestrator(
        confidence_threshold=config.CONFIDENCE_THRESHOLD,
        neg_stock_threshold=config.NEGATIVE_STOCK_THRESHOLD,
        pos_stock_threshold=config.POSITIVE_STOCK_THRESHOLD,
        neg_option_threshold=config.NEGATIVE_OPTION_THRESHOLD,
        pos_option_threshold=config.POSITIVE_OPTION_THRESHOLD,
        max_signals_per_event=config.MAX_SIGNALS_PER_EVENT,
    )
    seen = _load_seen_ids(bus)

    logger.info("News radar started. source_mode=%s, restored %d seen IDs", config.NEWS_SOURCE_MODE, len(seen))
    while True:
        try:
            raw_news = fetch_sample_news() if config.NEWS_SOURCE_MODE == "sample" else fetch_finnhub_news(config.FINNHUB_KEY)
            for raw in raw_news:
                news_id = str(raw.get("id") or raw.get("headline") or raw.get("title") or "")
                if not news_id or news_id in seen:
                    continue
                event = classify_news(raw, config.WATCHLIST)
                seen.add(news_id)
                bus.append_item("news_events", event.to_dict())
                logger.info("EVENT %s %s score=%.2f symbols=%s", event.event_type, event.direction, event.score, event.symbols)
                for signal in orchestrator.build_signals(event):
                    target_file = "stock_signals" if signal.asset_class == "STOCK" else "option_signals"
                    bus.append_item(target_file, signal.to_dict())
                    logger.info("SIGNAL %s %s %s", signal.asset_class, signal.symbol, signal.side)
        except Exception as exc:
            logger.exception("news_radar loop error: %s", exc)
        time.sleep(config.NEWS_POLL_SEC)


if __name__ == "__main__":
    main()
