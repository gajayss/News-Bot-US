from __future__ import annotations

import time

import config
from news_bridge.alerter import TelegramAlerter
from news_bridge.brokers.kis_rest_stock import KISConfig, KISRestStockBroker
from news_bridge.consumers import JsonSignalConsumer
from news_bridge.file_bus import DailyJsonBus
from news_bridge.rate_limiter import OrderRateLimiter
from news_bridge.utils import setup_logging

logger = setup_logging("stock_consumer")


def main() -> None:
    bus = DailyJsonBus(config.INTERFACE_DIR, config.LOG_DIR)
    consumer = JsonSignalConsumer(bus, signal_file_name="stock_signals", consumer_name="stock_consumer")
    broker = KISRestStockBroker(
        KISConfig(
            base_url=config.KIS_BASE_URL,
            app_key=config.KIS_APP_KEY,
            app_secret=config.KIS_APP_SECRET,
            cano=config.KIS_CANO,
            acnt_prdt_cd=config.KIS_ACNT_PRDT_CD,
            exchange_code=config.KIS_OVERSEAS_EXCHANGE,
            buy_tr_id=config.KIS_OVERSEAS_ORDER_BUY_TR_ID,
            sell_tr_id=config.KIS_OVERSEAS_ORDER_SELL_TR_ID,
            order_price=config.KIS_ORDER_PRICE,
            order_type=config.KIS_ORDER_TYPE,
            simulate=config.KIS_SIMULATE,
        )
    )
    limiter = OrderRateLimiter(
        max_orders_per_minute=config.MAX_ORDERS_PER_MINUTE,
        symbol_cooldown_sec=config.SYMBOL_COOLDOWN_SEC,
    )
    alerter = TelegramAlerter(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)

    def handle(signal: dict) -> dict:
        symbol = str(signal.get("symbol", ""))
        side = str(signal.get("side", ""))
        logger.info("Processing stock signal=%s %s", symbol, side)

        rejection = limiter.check(symbol)
        if rejection:
            logger.warning("RATE LIMITED %s: %s", symbol, rejection["reason"])
            alerter.notify_rate_limited(symbol, rejection["reason"])
            return {"broker": "KIS", "symbol": symbol, "side": side, "status": "RATE_LIMITED", "reason": rejection["reason"]}

        result = broker.place_order(
            symbol=symbol,
            side=side,
            qty=int(signal.get("qty", 1)),
            reason=str(signal.get("reason", "")),
            signal_id=str(signal.get("signal_id", "")),
        )
        limiter.record(symbol)
        alerter.notify_order(result)
        return result

    while True:
        try:
            processed = consumer.run_once(handle)
            if processed:
                logger.info("Processed %s stock signal(s)", processed)
        except Exception as exc:
            logger.exception("stock consumer error: %s", exc)
        time.sleep(3)


if __name__ == "__main__":
    main()
