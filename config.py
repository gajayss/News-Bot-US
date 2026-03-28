from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / os.getenv("RUNTIME_DIR", "runtime")
INTERFACE_DIR = RUNTIME_DIR / "interface"
LOG_DIR = RUNTIME_DIR / "logs"

WATCHLIST = [s.strip().upper() for s in os.getenv("WATCHLIST", "NVDA,TSLA,AAPL,QQQ").split(",") if s.strip()]

NEWS_SOURCE_MODE = os.getenv("NEWS_SOURCE_MODE", "sample").strip().lower()
FINNHUB_KEY = os.getenv("FINNHUB_KEY", "")
NEWS_POLL_SEC = int(os.getenv("NEWS_POLL_SEC", "20"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.55"))
NEGATIVE_STOCK_THRESHOLD = float(os.getenv("NEGATIVE_STOCK_THRESHOLD", "-0.70"))
POSITIVE_STOCK_THRESHOLD = float(os.getenv("POSITIVE_STOCK_THRESHOLD", "0.70"))
NEGATIVE_OPTION_THRESHOLD = float(os.getenv("NEGATIVE_OPTION_THRESHOLD", "-0.75"))
POSITIVE_OPTION_THRESHOLD = float(os.getenv("POSITIVE_OPTION_THRESHOLD", "0.75"))
MAX_SIGNALS_PER_EVENT = int(os.getenv("MAX_SIGNALS_PER_EVENT", "3"))

KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_CANO = os.getenv("KIS_CANO", "")
KIS_ACNT_PRDT_CD = os.getenv("KIS_ACNT_PRDT_CD", "01")
KIS_OVERSEAS_EXCHANGE = os.getenv("KIS_OVERSEAS_EXCHANGE", "NASD")
KIS_OVERSEAS_ORDER_BUY_TR_ID = os.getenv("KIS_OVERSEAS_ORDER_BUY_TR_ID", "TTTT1002U")
KIS_OVERSEAS_ORDER_SELL_TR_ID = os.getenv("KIS_OVERSEAS_ORDER_SELL_TR_ID", "TTTT1006U")
KIS_ORDER_PRICE = os.getenv("KIS_ORDER_PRICE", "0")
KIS_ORDER_TYPE = os.getenv("KIS_ORDER_TYPE", "00")
KIS_SIMULATE = os.getenv("KIS_SIMULATE", "false").lower() == "true"

OPTION_BROKER_MODE = os.getenv("OPTION_BROKER_MODE", "command").strip().lower()
KIWOOM_COMMAND = os.getenv("KIWOOM_COMMAND", "")
KIWOOM_COMMAND_TIMEOUT_SEC = int(os.getenv("KIWOOM_COMMAND_TIMEOUT_SEC", "25"))
KIWOOM_COMMAND_WORKING_DIR = os.getenv("KIWOOM_COMMAND_WORKING_DIR", "")
KIWOOM_COMMAND_SUCCESS_RETURNCODES = os.getenv("KIWOOM_COMMAND_SUCCESS_RETURNCODES", "0")
KIWOOM_COMMAND_CAPTURE_STDOUT_JSON = os.getenv("KIWOOM_COMMAND_CAPTURE_STDOUT_JSON", "true").lower() == "true"
KIWOOM_COMMAND_EXTRA_ENV_JSON = os.getenv("KIWOOM_COMMAND_EXTRA_ENV_JSON", "{}")
KIWOOM_COMMAND_DELETE_PAYLOAD_AFTER_RUN = os.getenv("KIWOOM_COMMAND_DELETE_PAYLOAD_AFTER_RUN", "false").lower() == "true"
KIWOOM_WEBHOOK_URL = os.getenv("KIWOOM_WEBHOOK_URL", "http://127.0.0.1:8011/trade/option")
KIWOOM_REST_BASE_URL = os.getenv("KIWOOM_REST_BASE_URL", "https://api.kiwoom.com")
KIWOOM_APP_KEY = os.getenv("KIWOOM_APP_KEY", "")
KIWOOM_APP_SECRET = os.getenv("KIWOOM_APP_SECRET", "")
KIWOOM_ACCOUNT_NO = os.getenv("KIWOOM_ACCOUNT_NO", "")
KIWOOM_OPTION_ORDER_TR_CODE = os.getenv("KIWOOM_OPTION_ORDER_TR_CODE", "")

# --- Rate Limiter ---
MAX_ORDERS_PER_MINUTE = int(os.getenv("MAX_ORDERS_PER_MINUTE", "5"))
SYMBOL_COOLDOWN_SEC = float(os.getenv("SYMBOL_COOLDOWN_SEC", "60"))

# --- Telegram Alerts (optional) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
