from __future__ import annotations

import time

import config
from news_bridge.alerter import TelegramAlerter
from news_bridge.brokers.kiwoom_option_bridge import KiwoomBridgeConfig, KiwoomOptionBridgeBroker
from news_bridge.consumers import JsonSignalConsumer
from news_bridge.file_bus import DailyJsonBus
from news_bridge.rate_limiter import OrderRateLimiter
from news_bridge.utils import setup_logging

logger = setup_logging("option_consumer")


def main() -> None:
    bus = DailyJsonBus(config.INTERFACE_DIR, config.LOG_DIR)
    consumer = JsonSignalConsumer(bus, signal_file_name="option_signals", consumer_name="option_consumer")
    broker = KiwoomOptionBridgeBroker(
        KiwoomBridgeConfig(
            mode=config.OPTION_BROKER_MODE,
            command=config.KIWOOM_COMMAND,
            command_timeout_sec=config.KIWOOM_COMMAND_TIMEOUT_SEC,
            webhook_url=config.KIWOOM_WEBHOOK_URL,
            command_working_dir=config.KIWOOM_COMMAND_WORKING_DIR,
            command_success_returncodes=config.KIWOOM_COMMAND_SUCCESS_RETURNCODES,
            command_capture_stdout_json=config.KIWOOM_COMMAND_CAPTURE_STDOUT_JSON,
            command_extra_env_json=config.KIWOOM_COMMAND_EXTRA_ENV_JSON,
            command_delete_payload_after_run=config.KIWOOM_COMMAND_DELETE_PAYLOAD_AFTER_RUN,
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
        logger.info("Processing option signal=%s %s", symbol, side)

        rejection = limiter.check(symbol)
        if rejection:
            logger.warning("RATE LIMITED %s: %s", symbol, rejection["reason"])
            alerter.notify_rate_limited(symbol, rejection["reason"])
            return {"broker": "KIWOOM_BRIDGE", "underlying": symbol, "side": side, "status": "RATE_LIMITED", "reason": rejection["reason"]}

        result = broker.place_order(
            symbol=symbol,
            side=side,
            qty=int(signal.get("qty", 1)),
            reason=str(signal.get("reason", "")),
            signal_id=str(signal.get("signal_id", "")),
            expiry_type=str(signal.get("option_expiry_type", "MONTHLY")),
            reference_price=float(signal.get("reference_price", 0.0)),
            option_right=str(signal.get("option_right", "CALL")),
        )
        limiter.record(symbol)
        alerter.notify_order(result)
        return result

    while True:
        try:
            processed = consumer.run_once(handle)
            if processed:
                logger.info("Processed %s option signal(s)", processed)
        except Exception as exc:
            logger.exception("option consumer error: %s", exc)
        time.sleep(3)


if __name__ == "__main__":
    main()
