from __future__ import annotations

import time

import config
from news_bridge.classifier import classify_news
from news_bridge.event_calendar import EventCalendarState
from news_bridge.file_bus import DailyJsonBus
from news_bridge.orchestrator import SignalOrchestrator
from news_bridge.dedup_guard import DedupGuard
from news_bridge.market_context import MarketContextEngine
from news_bridge.sources.calendar_source import fetch_finnhub_calendar, fetch_sample_calendar
from news_bridge.sources.finnhub_source import fetch_finnhub_news
from news_bridge.sources.insider_source import scan_watchlist_insiders
from news_bridge.sources.insider_scraper import scan_insider_web
from news_bridge.sources.sample_source import fetch_sample_news
from news_bridge.sources.financialjuice_source import fetch_financialjuice_rss
from news_bridge.sources.fintel_scraper import scan_short_volume
from news_bridge.sources.ark_trades_scraper import scan_ark_trades
from news_bridge.utils import setup_logging

logger = setup_logging("news_radar")

# --- 스캔 주기 (부하 최소화: 뒤에 매매엔진 2개 더 돌아감) ---
CALENDAR_REFRESH_SEC = 3600   # 캘린더 1시간마다
FJ_NEWS_POLL_SEC = 600        # FinancialJuice RSS 10분 (가장 빠른 뉴스)
INSIDER_SCAN_SEC = 7200       # Finnhub 내부자 API 2시간
INSIDER_WEB_SCAN_SEC = 3600   # finviz+dataroma 1시간
SHORT_VOL_SCAN_SEC = 86400    # fintel 공매도 하루 1회
ARK_TRADES_SCAN_SEC = 86400   # ARK 매매 하루 1회


def _emit_signal(bus: DailyJsonBus, signal) -> None:
    """시그널을 소비자용 daily 파일 + 선분이력 store 양쪽에 기록."""
    target_file = "stock_signals" if signal.asset_class == "STOCK" else "option_signals"
    sig_dict = signal.to_dict()
    bus.append_item(target_file, sig_dict)        # 소비자용 (기존 호환)
    result = bus.upsert_signal(sig_dict)          # 선분이력 store (중복 제거)
    return result


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
    market_engine = MarketContextEngine()
    dedup_guard = DedupGuard()
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
        market_engine=market_engine,
        dedup_guard=dedup_guard,
        use_market_context=True,
    )
    seen = _load_seen_ids(bus)
    last_calendar_refresh = time.time()
    last_insider_scan = 0.0      # 첫 루프에서 즉시 스캔
    last_insider_web_scan = 0.0  # 웹 크롤링도 즉시
    last_short_vol_scan = 0.0   # fintel 공매도 즉시
    last_fj_scan = 0.0          # financialjuice 즉시
    last_ark_scan = 0.0         # ARK 매매 즉시

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

            # --- 내부자 거래 스캔 (1시간마다) ---
            if config.FINNHUB_KEY and time.time() - last_insider_scan > INSIDER_SCAN_SEC:
                try:
                    insider_alerts = scan_watchlist_insiders(
                        api_key=config.FINNHUB_KEY,
                        watchlist=config.WATCHLIST,
                        lookback_days=30,
                    )
                    for alert in insider_alerts:
                        alert_id = str(alert.get("id", ""))
                        if alert_id and alert_id not in seen:
                            event = classify_news(alert, config.WATCHLIST)
                            seen.add(alert_id)
                            bus.append_item("news_events", event.to_dict())
                            logger.warning(
                                "INSIDER ALERT [%s/%s] %s score=%.2f symbols=%s",
                                event.axis_id, event.event_type, event.direction,
                                event.score, event.symbols,
                            )
                            for signal in orchestrator.build_signals(event):
                                r = _emit_signal(bus, signal)
                                logger.warning(
                                    "INSIDER SIGNAL %s %s %s qty=%d [%s]",
                                    signal.asset_class, signal.symbol, signal.side, signal.qty, r,
                                )
                except Exception as exc:
                    logger.warning("Insider scan error: %s", exc)
                last_insider_scan = time.time()

            # --- finviz + dataroma 웹 크롤링 (30분마다) ---
            if time.time() - last_insider_web_scan > INSIDER_WEB_SCAN_SEC:
                try:
                    web_alerts = scan_insider_web(
                        watchlist=config.WATCHLIST,
                        min_value=1_000_000,
                    )
                    for alert in web_alerts:
                        alert_id = str(alert.get("id", ""))
                        if alert_id and alert_id not in seen:
                            event = classify_news(alert, config.WATCHLIST)
                            seen.add(alert_id)
                            bus.append_item("news_events", event.to_dict())
                            logger.warning(
                                "WEB INSIDER [%s/%s] %s score=%.2f symbols=%s | %s",
                                event.axis_id, event.event_type, event.direction,
                                event.score, event.symbols, event.headline[:60],
                            )
                            for signal in orchestrator.build_signals(event):
                                r = _emit_signal(bus, signal)
                                logger.warning(
                                    "WEB INSIDER SIGNAL %s %s %s qty=%d | %s [%s]",
                                    signal.asset_class, signal.symbol, signal.side,
                                    signal.qty, signal.reason[:60], r,
                                )
                except Exception as exc:
                    logger.warning("Web insider scan error: %s", exc)
                last_insider_web_scan = time.time()

            # --- FinancialJuice RSS (5분마다) — 가장 빠른 뉴스 소스 ---
            if time.time() - last_fj_scan > FJ_NEWS_POLL_SEC:
                try:
                    fj_news = fetch_financialjuice_rss(max_items=30)
                    for raw in fj_news:
                        news_id = str(raw.get("id", ""))
                        if not news_id or news_id in seen:
                            continue
                        event = classify_news(raw, config.WATCHLIST)
                        seen.add(news_id)
                        bus.append_item("news_events", event.to_dict())
                        if event.symbols:
                            logger.info(
                                "FJ NEWS [%s/%s] %s score=%.2f symbols=%s | %s",
                                event.axis_id, event.event_type, event.direction,
                                event.score, event.symbols, event.headline[:60],
                            )
                            for signal in orchestrator.build_signals(event):
                                r = _emit_signal(bus, signal)
                                logger.info(
                                    "FJ SIGNAL %s %s %s qty=%d [%s]",
                                    signal.asset_class, signal.symbol, signal.side, signal.qty, r,
                                )
                except Exception as exc:
                    logger.warning("FinancialJuice scan error: %s", exc)
                last_fj_scan = time.time()

            # --- Fintel 공매도 볼륨 (비활성화 — 403 차단, Chrome MCP 필요) ---
            # fintel.io는 requests 403 차단. 추후 Chrome MCP 기반으로 전환 예정.
            # if time.time() - last_short_vol_scan > SHORT_VOL_SCAN_SEC:
            #     ...
            last_short_vol_scan = time.time()  # 타이머만 리셋

            # --- ARK Invest 매매 추적 (하루 1회) ---
            if time.time() - last_ark_scan > ARK_TRADES_SCAN_SEC:
                try:
                    ark_alerts = scan_ark_trades(watchlist=config.WATCHLIST)
                    for alert in ark_alerts:
                        alert_id = str(alert.get("id", ""))
                        if alert_id and alert_id not in seen:
                            event = classify_news(alert, config.WATCHLIST)
                            seen.add(alert_id)
                            bus.append_item("news_events", event.to_dict())
                            direction_tag = alert.get("_meta", {}).get("direction", "")
                            logger.warning(
                                "ARK %s [%s/%s] %s score=%.2f symbols=%s | %s",
                                direction_tag, event.axis_id, event.event_type,
                                event.direction, event.score, event.symbols,
                                event.headline[:60],
                            )
                            for signal in orchestrator.build_signals(event):
                                r = _emit_signal(bus, signal)
                                logger.warning(
                                    "ARK SIGNAL %s %s %s qty=%d [%s]",
                                    signal.asset_class, signal.symbol, signal.side, signal.qty, r,
                                )
                except Exception as exc:
                    logger.warning("ARK trades scan error: %s", exc)
                last_ark_scan = time.time()

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
                    r = _emit_signal(bus, signal)
                    logger.info("SIGNAL %s %s %s qty=%d [%s]", signal.asset_class, signal.symbol, signal.side, signal.qty, r)
        except Exception as exc:
            logger.exception("news_radar loop error: %s", exc)
        time.sleep(config.NEWS_POLL_SEC)


if __name__ == "__main__":
    main()
