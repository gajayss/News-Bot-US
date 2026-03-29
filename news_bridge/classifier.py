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
    # 공매도/헤지펀드 약세 키워드
    "short", "shorts", "shorting", "shorted", "overvalued", "inflated", "dilution",
    "exits", "trims", "closes", "sells", "dumps", "bearish",
    "sold", "dumped", "exited", "trimmed", "unloaded", "liquidated",
    "selling", "insider",
}
POS_WORDS = {
    "beat", "beats", "surge", "surges", "jumps", "jump", "raises", "raise", "approval", "wins",
    "record", "guidance", "growth", "upgrades", "upgrade", "partnership", "rally", "soars",
    "accelerates", "spending", "contracts",
    # 공매도 스퀴즈/매수 키워드
    "squeeze", "builds", "increases", "buys", "bullish", "accumulates",
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
    "super micro": "SMCI", "supermicro": "SMCI", "smci": "SMCI",
    "crowdstrike": "CRWD", "palo alto": "PANW", "zscaler": "ZS",
    "fortinet": "FTNT",
    # --- INTERNET/TELECOM ---
    "google": "GOOGL", "alphabet": "GOOGL", "meta": "META",
    "netflix": "NFLX", "disney": "DIS",
    "apple": "AAPL", "verizon": "VZ", "at&t": "T", "t-mobile": "TMUS",
    # --- RETAIL ---
    "amazon": "AMZN", "walmart": "WMT", "costco": "COST", "target corp": "TGT", "target stores": "TGT",
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
    "oil price": "USO", "crude oil": "USO", "oil surge": "USO", "oil drop": "USO",
    "energy sector": "XLE", "energy stocks": "XLE",
    "natural gas price": "UNG", "gas price": "BOIL",
    # --- AEROSPACE/DEFENSE (항공우주/방산) ---
    "boeing": "BA", "general electric": "GE", "general dynamics": "GD",
    "lockheed": "LMT", "northrop": "NOC", "raytheon": "RTX",
    "l3harris": "LHX", "transdigm": "TDG", "howmet": "HWM",
    "defense stocks": "ITA", "defense sector": "ITA", "defense spending": "ITA",
    "rocket lab": "RKLB",
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
    "nuscale": "SMR", "smr reactor": "SMR", "small modular reactor": "SMR",
    "oklo": "OKLO", "cameco": "CCJ",
    "uranium price": "URA", "uranium stocks": "URA",
    "power grid": "VRT", "vertiv": "VRT",
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
    "robotics stock": "BOTZ", "robotics sector": "BOTZ", "humanoid robot": "BOTZ",
    # --- 양자컴 ---
    "quantum": "IONQ", "ionq": "IONQ",
    "rigetti": "RGTI", "rgti": "RGTI",
    "d-wave": "QBTS", "dwave": "QBTS",
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


# 날짜/시간 약어 — 티커와 혼동 방지 (Jan~Dec, Mon~Sun)
_DATE_ABBRS = {
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
}

# 1글자 티커 (C, F, T, V 등)는 이니셜·약어 오탐 위험 → 대문자 단어로만 허용
# ex) "McMillon C Douglas" 의 'C' → 이니셜, 'CITIGROUP' 뉴스의 'C' → 허용
_SINGLE_CHAR_TICKERS = {"C", "F", "T", "V", "U", "K", "X", "A", "E"}


def _extract_symbols(text: str, watchlist: list[str]) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    # 1) alias matching (multi-word aliases are safe)
    for alias, symbol in SYMBOL_ALIASES.items():
        if alias in lowered and symbol in watchlist and symbol not in found:
            found.append(symbol)
    # 2) ticker matching — short tickers (<=3 chars) need word boundary check
    for symbol in watchlist:
        if symbol in found:
            continue
        sym_lower = symbol.lower()
        if len(symbol) == 1:
            # 1글자 티커: 원문에서 대문자 단독 단어로만 허용 (이니셜 오탐 방지)
            if symbol in _SINGLE_CHAR_TICKERS:
                if re.search(rf'\b{re.escape(symbol)}\b', text):
                    # 주변이 이름/이니셜 패턴이면 제외 (알파벳 단일 문자 + 공백 + 이름)
                    # 예: "McMillon C Douglas" → 제외, "Citigroup (C)" → 허용
                    ctx_match = re.search(
                        rf'(?:[A-Z][a-z]+ ){re.escape(symbol)}(?: [A-Z][a-z]+)',
                        text,
                    )
                    if not ctx_match:
                        found.append(symbol)
        elif len(symbol) <= 3:
            # 2~3글자 티커: 날짜 약어 제외 + 워드바운더리
            if sym_lower not in _DATE_ABBRS:
                if re.search(rf'\b{re.escape(sym_lower)}\b', lowered):
                    found.append(symbol)
        else:
            if sym_lower in lowered:
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

    # 헤지펀드/공매도/내부자 매도 → urgency 최고 (즉시 대응 필요)
    if event_type in {"HEDGEFUND", "SHORT_SELLING"}:
        urgency = 0.95
    elif event_type == "INSIDER":
        urgency = 0.90

    confidence = min(0.95, 0.45 + abs(score) * 0.35 + (0.15 if event_type != "GENERAL" else 0.0) + (0.10 if symbols else 0.0))

    # 공매도 리포트/내부자 대량매도 → confidence 부스트 (실전에서 거의 100% 하락)
    if event_type in {"SHORT_SELLING", "HEDGEFUND"}:
        confidence = min(0.95, confidence + 0.15)
    elif event_type == "INSIDER":
        confidence = min(0.95, confidence + 0.10)

    # 내부자 매도 / 공매도 맥락 강제 보정
    # "insider selling surges" 에서 surges가 POS로 잡히는 문제 해결
    # CEO/창업자 매도 = 최강 약세 신호 (언론에서 성장 떠들면서 본인은 매도)
    _ceo_sell_context = {
        "ceo sold", "ceo sells", "ceo unloads", "ceo dumps",
        "founder sold", "founder sells", "founder unloads",
        "ceo selling", "founder selling",
        "musk sold", "musk sells",
        "huang sold", "huang sells",
        "liang sold", "liang sells",  # SMCI CEO
        "karp sold", "karp sells",    # PLTR CEO
        "beck sold", "spice sold",    # RKLB insiders
    }
    # 일반 내부자/공매도 매도
    _sell_context = {
        "insider sell", "insider sold", "insider dump",
        "cfo sold", "coo sold", "cto sold",
        "executive sold", "officer sold", "director sold",
        "officers sold", "executives sold",
        "stake sold", "unloads shares", "dumps shares",
        "short seller", "short report", "short target",
        "fraud alleged", "accounting fraud", "overvalued",
        "insider selling",
    }
    lowered_full = text.lower()
    if any(kw in lowered_full for kw in _ceo_sell_context):
        score = -1.00  # CEO 매도 = 최강 약세, 무조건 -1.0
        direction = "BEARISH"
    elif any(kw in lowered_full for kw in _sell_context):
        score = min(score, -0.80)  # 일반 내부자/공매도 = -0.80 이하 강제
        direction = "BEARISH"

    # 지정학 뉴스 점수 보정 — 중동/우크라이나 뉴스가 미국 주식에 -1.00은 과격
    # 직접 미국 관련(트럼프/제재)이 아닌 해외 지정학은 점수 완화
    if event_type == "GEOPOLITICAL" and abs(score) >= 0.80:
        _us_direct = {"trump", "white house", "pentagon", "us military",
                      "american", "united states", "congress", "senate"}
        if not any(kw in lowered_full for kw in _us_direct):
            score = max(-0.50, min(0.50, score * 0.5))  # 해외 지정학은 절반으로
            if abs(score) < 0.20:
                direction = "NEUTRAL"

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
