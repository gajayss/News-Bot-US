from __future__ import annotations

import re
from typing import Any

from .axes import detect_event_type
from .models import NewsEvent

NEG_WORDS = {
    "drop", "drops", "falls", "fall", "slumps", "slump", "miss", "cut", "cuts", "probe", "lawsuit",
    "ban", "war", "missile", "attack", "strikes", "downgrade", "fraud", "recall", "weak", "warns",
    "warning", "delay", "fears", "conflict", "disruption", "slam", "flee", "crash", "plunge", "threat",
    "threatens", "banning",
}
POS_WORDS = {
    "beat", "beats", "surge", "surges", "jumps", "jump", "raises", "raise", "approval", "wins",
    "record", "guidance", "growth", "upgrades", "upgrade", "partnership", "rally", "soars",
    "accelerates", "spending", "contracts",
}
EVENT_RULES = {
    "GEOPOLITICAL": ["iran", "missile", "war", "attack", "israel", "strait", "trump", "middle east",
                     "pentagon", "military", "defense", "conflict", "red sea", "hormuz", "sanctions",
                     "executive order"],
    "FED": ["powell", "fomc", "fed", "pce", "cpi", "inflation", "rate cut", "rates"],
    "EARNINGS": ["earnings", "guidance", "quarter", "revenue", "eps", "supply chain"],
    "ANALYST": ["downgrade", "upgrade", "price target", "analyst"],
    "INSIDER": ["insider", "director sold", "ceo sold", "stake sold"],
    "REGULATION": ["probe", "lawsuit", "antitrust", "tariff", "restriction", "ban", "banning",
                   "executive order"],
}
SYMBOL_ALIASES = {
    # --- CHIPS (반도체) ---
    "nvidia": "NVDA", "amd": "AMD", "broadcom": "AVGO", "micron": "MU",
    "qualcomm": "QCOM", "intel": "INTC", "texas instruments": "TXN",
    "analog devices": "ADI", "applied materials": "AMAT", "lam research": "LRCX",
    "kla": "KLAC", "nxp": "NXPI", "monolithic": "MPWR",
    "semiconductor": "SOXL", "chip": "SOXL", "chips": "SOXL", "gpu": "SOXL",
    # --- SOFTWARE ---
    "microsoft": "MSFT", "salesforce": "CRM", "servicenow": "NOW",
    "workday": "WDAY", "palantir": "PLTR", "adobe": "ADSK",
    "crowdstrike": "CRWD", "palo alto": "PANW", "zscaler": "ZS",
    "fortinet": "FTNT",
    # --- INTERNET/TELECOM ---
    "google": "GOOGL", "alphabet": "GOOGL", "meta": "META",
    "netflix": "NFLX", "disney": "DIS",
    "apple": "AAPL", "verizon": "VZ", "at&t": "T", "t-mobile": "TMUS",
    # --- RETAIL ---
    "amazon": "AMZN", "walmart": "WMT", "costco": "COST", "target": "TGT",
    "home depot": "HD", "lowe's": "LOW", "starbucks": "SBUX",
    "mcdonald": "MCD",
    # --- MEDICAL/제약/바이오 ---
    "eli lilly": "LLY", "johnson & johnson": "JNJ", "abbvie": "ABBV",
    "pfizer": "PFE", "merck": "MRK", "amgen": "AMGN", "gilead": "GILD",
    "unitedhealth": "UNH", "abbott": "ABT", "danaher": "DHR",
    "intuitive surgical": "ISRG", "stryker": "SYK", "medtronic": "MDT",
    "vertex": "VRTX", "regeneron": "REGN", "moderna": "MRNA",
    "biotech": "XBI", "glp-1": "NVO", "ozempic": "NVO",
    # --- ENERGY (에너지) ---
    "exxon": "XOM", "chevron": "CVX", "occidental": "OXY",
    "conocophillips": "COP", "eog": "EOG", "devon": "DVN",
    "schlumberger": "SLB", "halliburton": "HAL",
    "oil": "USO", "crude": "USO", "energy": "XLE",
    "natural gas": "UNG", "gas": "BOIL",
    # --- AEROSPACE/DEFENSE (항공우주/방산) ---
    "boeing": "BA", "general electric": "GE", "general dynamics": "GD",
    "lockheed": "LMT", "northrop": "NOC", "raytheon": "RTX",
    "l3harris": "LHX", "transdigm": "TDG", "howmet": "HWM",
    "defense": "ITA", "rocket lab": "RKLB",
    # --- BANKS/FINANCE (금융) ---
    "jpmorgan": "JPM", "bank of america": "BAC", "goldman": "GS",
    "morgan stanley": "MS", "wells fargo": "WFC", "citigroup": "C",
    "visa": "V", "mastercard": "MA", "american express": "AXP",
    "blackrock": "BLK", "kkr": "KKR", "apollo": "APO",
    "blackstone": "BX", "schwab": "SCHW",
    "cme group": "CME", "intercontinental": "ICE",
    # --- INSURANCE (보험) ---
    "chubb": "CB", "travelers": "TRV", "allstate": "ALL",
    "aon": "AON", "marsh": "AJG", "progressive": "PGR",
    # --- AUTO (자동차) ---
    "tesla": "TSLA", "general motors": "GM", "ford": "F", "paccar": "PCAR",
    "electric vehicle": "TSLA", "ev battery": "TSLA",
    # --- LEISURE (레저/여행) ---
    "uber": "UBER", "royal caribbean": "RCL",
    "booking": "BKNG", "marriott": "MAR", "hilton": "HLT",
    # --- MACHINE/INDUSTRIAL (산업재) ---
    "parker hannifin": "PH", "illinois tool": "ITW", "eaton": "ETN",
    "rockwell": "ROK", "hubbell": "HUBB", "caterpillar": "CAT",
    "deere": "DE", "honeywell": "HON",
    # --- UTILITY/원전/전력 ---
    "nextera": "NEE", "duke energy": "DUK", "southern company": "SO",
    "nuclear": "SMR", "smr": "SMR", "oklo": "OKLO", "cameco": "CCJ",
    "uranium": "URA", "power grid": "VRT", "vertiv": "VRT",
    # --- 금리/채권 ---
    "treasury": "TLT", "bond yield": "TLT", "mortgage rate": "TLT",
    # --- 원자재/귀금속/희토류 ---
    "gold": "GLD", "bullion": "GLD", "gold miner": "NUGT",
    "silver": "SLV", "rare earth": "REMX", "lithium": "LIT",
    "copper": "COPX", "freeport": "FCX", "nucor": "NUE",
    # --- 크립토 ---
    "bitcoin": "COIN", "crypto": "COIN", "coinbase": "COIN",
    "microstrategy": "MSTR",
    # --- 로봇/자동화 ---
    "robotics": "BOTZ", "robot": "BOTZ", "humanoid": "BOTZ",
    # --- 양자컴 ---
    "quantum": "IONQ", "ionq": "IONQ",
    # --- 데이터센터/리츠 ---
    "data center": "EQIX", "equinix": "EQIX", "digital realty": "DLR",
    "reit": "PLD", "prologis": "PLD", "american tower": "AMT",
    # --- 필수소비재/소비재 ---
    "procter": "PG", "coca-cola": "KO", "pepsi": "PEP",
    # --- 인덱스 ETF ---
    "nasdaq": "TQQQ", "qqq": "QQQ", "spy": "SPY",
}


def _score_text(text: str) -> float:
    tokens = re.findall(r"[A-Za-z']+", text.lower())
    pos = sum(1 for t in tokens if t in POS_WORDS)
    neg = sum(1 for t in tokens if t in NEG_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / total))


def _detect_event_type(text: str) -> tuple[str, str]:
    """Detect event type using 5-axis system. Returns (event_type, axis_id)."""
    # Primary: 5-axis keyword matching (priority-ordered)
    event_type, axis_id = detect_event_type(text)
    if event_type != "GENERAL":
        return event_type, axis_id

    # Fallback: legacy EVENT_RULES for edge cases
    lowered = text.lower()
    for et, keywords in EVENT_RULES.items():
        if any(k in lowered for k in keywords):
            return et, "UNKNOWN"
    return "GENERAL", "UNKNOWN"


def _extract_symbols(text: str, watchlist: list[str]) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for alias, symbol in SYMBOL_ALIASES.items():
        if alias in lowered and symbol in watchlist:
            found.append(symbol)
    for symbol in watchlist:
        if symbol.lower() in lowered and symbol not in found:
            found.append(symbol)
    return found


def classify_news(raw: dict[str, Any], watchlist: list[str]) -> NewsEvent:
    headline = str(raw.get("headline") or raw.get("title") or "").strip()
    summary = str(raw.get("summary") or raw.get("description") or "").strip()
    text = f"{headline} {summary}".strip()
    score = _score_text(text)
    event_type, axis_id = _detect_event_type(text)
    symbols = _extract_symbols(text, watchlist)

    direction = "NEUTRAL"
    if score >= 0.20:
        direction = "BULLISH"
    elif score <= -0.20:
        direction = "BEARISH"

    urgency = 0.55 if event_type in {"GENERAL", "ANALYST"} else 0.80
    confidence = min(0.95, 0.45 + abs(score) * 0.35 + (0.15 if event_type != "GENERAL" else 0.0) + (0.10 if symbols else 0.0))
    tradable = bool(symbols) and confidence >= 0.50 and abs(score) >= 0.20
    horizon = "INTRADAY"
    if event_type in {"EARNINGS", "INSIDER", "REGULATION"}:
        horizon = "SWING"
    elif axis_id == "THEME":
        horizon = "SWING"  # 테마주는 추세 매매

    return NewsEvent(
        source=str(raw.get("source") or "unknown"),
        source_news_id=str(raw.get("id") or raw.get("news_id") or headline[:30]),
        headline=headline,
        summary=summary,
        url=str(raw.get("url") or ""),
        published_at=str(raw.get("datetime") or raw.get("published_at") or raw.get("publishedAt") or ""),
        symbols=symbols,
        event_type=event_type,
        axis_id=axis_id,
        direction=direction,
        score=round(score, 4),
        confidence=round(confidence, 4),
        urgency=round(urgency, 4),
        horizon=horizon,
        tradable=tradable,
        raw=raw,
    )
