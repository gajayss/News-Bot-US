"""5-Axis News Classification System.

축1: ECONOMY   — 경제 이벤트 (CPI, PCE, NFP, GDP, ISM, Retail, Jobless Claims)
축2: CORPORATE — 기업 실적/이벤트 (Earnings, Guidance, M&A, Insider, Analyst)
축3: GOVERN    — 미국정부 (트럼프, 정책, 전쟁, 제재, 관세, 행정명령)
축4: FEDWALL   — FOMC/월가 기관 (금리결정, Fed연설, 월가 리포트, 기관 포지션)
축5: THEME     — 테마/신기술 (AI, 양자컴퓨팅, 전력, 데이터센터, 원전, 항공우주, 로봇, EV, 바이오)

각 축은 독립적으로:
  - 뉴스를 분류 (키워드 매칭)
  - 충격등급 판정 (1~5)
  - 전략 수정자 적용 (손절/익절/보유기간/수량 조정)
  - 포지션 사전 대응 규칙 보유
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# -----------------------------------------------------------------------
# Axis definition
# -----------------------------------------------------------------------

@dataclass(slots=True)
class AxisProfile:
    """One axis's classification and strategy parameters."""
    axis_id: str                    # ECONOMY, CORPORATE, GOVERN, FEDWALL
    axis_name_kr: str               # 한글 이름
    event_types: list[str]          # 이 축에 속하는 이벤트 유형들
    speed: str                      # FAST / MEDIUM / SLOW
    fear_eligible: bool             # FEAR regime 자동 적용 여부
    sl_modifier: float              # 손절 배수 (1.0 = 기본, 0.7 = 타이트)
    tp_modifier: float              # 익절 배수
    hold_modifier: float            # 보유기간 배수
    qty_modifier: float             # 수량 배수
    pre_event_block_hours: float    # 이벤트 전 차단 시작 (시간)
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_id": self.axis_id,
            "axis_name_kr": self.axis_name_kr,
            "speed": self.speed,
            "fear_eligible": self.fear_eligible,
            "sl_modifier": self.sl_modifier,
            "tp_modifier": self.tp_modifier,
            "hold_modifier": self.hold_modifier,
            "qty_modifier": self.qty_modifier,
            "pre_event_block_hours": self.pre_event_block_hours,
        }


# -----------------------------------------------------------------------
# 5-Axis definitions
# -----------------------------------------------------------------------

AXES: dict[str, AxisProfile] = {
    "ECONOMY": AxisProfile(
        axis_id="ECONOMY",
        axis_name_kr="경제 이벤트",
        event_types=["CPI", "PCE", "NFP", "GDP", "ISM", "EMPLOYMENT", "RETAIL", "COMMODITY"],
        speed="FAST",
        fear_eligible=True,
        sl_modifier=0.70,       # 타이트 손절 (-30% × 0.7 = -21%)
        tp_modifier=0.85,       # 익절도 빡빡 (40% × 0.85 = 34%)
        hold_modifier=0.50,     # 보유기간 절반 (10일 → 5일)
        qty_modifier=1.0,       # 수량 유지
        pre_event_block_hours=4.0,
        description="CPI/PCE/NFP 등 매크로 지표. 발표 직후 급변, 방향 빠르게 결정",
    ),
    "CORPORATE": AxisProfile(
        axis_id="CORPORATE",
        axis_name_kr="기업 실적/이벤트",
        event_types=["EARNINGS", "ANALYST", "INSIDER"],
        speed="MEDIUM",
        fear_eligible=False,    # 어닝은 공포가 아니라 서프라이즈/쇼크
        sl_modifier=0.80,       # 어닝 후 갭 크니까 좀 넓게
        tp_modifier=1.00,       # 어닝 서프라이즈 시 큰 수익 가능
        hold_modifier=0.70,     # 어닝 반응 1~3일이 핵심
        qty_modifier=0.50,      # 어닝 플레이는 수량 절반 (세력 장난질 리스크)
        pre_event_block_hours=3.0,
        description="어닝/가이던스/애널리스트. 세력+헤지펀드 장난질 주의, 수량 보수적",
    ),
    "GOVERN": AxisProfile(
        axis_id="GOVERN",
        axis_name_kr="미국정부",
        event_types=["GEOPOLITICAL", "REGULATION", "TRUMP"],
        speed="FAST",
        fear_eligible=True,     # 전쟁/제재 = 공포
        sl_modifier=0.65,       # 가장 타이트 (-30% × 0.65 = -19.5%)
        tp_modifier=0.85,       # 빨리 먹고 나와
        hold_modifier=0.40,     # 보유 최단 (10일 × 0.4 = 4일)
        qty_modifier=1.0,       # 방향 맞으면 크게 움직임
        pre_event_block_hours=2.0,
        description="트럼프/전쟁/제재/관세/행정명령. 예측 불가, 빠른 대응 필수",
    ),
    "FEDWALL": AxisProfile(
        axis_id="FEDWALL",
        axis_name_kr="FOMC/월가 기관",
        event_types=["FED", "FOMC", "FED_SPEAK", "RATES", "BANKS_FINANCE"],
        speed="FAST",
        fear_eligible=True,
        sl_modifier=0.70,       # FOMC 후 방향 빠르게 결정
        tp_modifier=0.90,
        hold_modifier=0.50,     # FOMC 후 1~3일 승부
        qty_modifier=1.0,
        pre_event_block_hours=4.0,
        description="FOMC 금리결정/Fed연설/월가 리포트. 변곡점, 매파·비둘기 핵심",
    ),
    "THEME": AxisProfile(
        axis_id="THEME",
        axis_name_kr="테마/신기술",
        event_types=["NUCLEAR", "QUANTUM", "SPACE_DEFENSE", "ROBOTICS",
                     "EV", "BIOTECH", "CYBERSECURITY", "SOFTWARE_CLOUD",
                     "CRYPTO", "ENERGY_INFRA", "DATA_CENTER", "AI_TECH"],
        speed="MEDIUM",
        fear_eligible=False,    # 테마는 공포가 아니라 모멘텀
        sl_modifier=0.85,       # 테마주는 변동성 크지만 추세 길다
        tp_modifier=1.10,       # 추세 타면 크게 먹을 수 있음
        hold_modifier=0.80,     # 보유 약간 축소 (10일 → 8일)
        qty_modifier=0.70,      # 테마주 변동성 고려 수량 30% 축소
        pre_event_block_hours=1.0,
        description="AI/양자컴/전력/데이터센터/원전/항공우주/로봇/EV/바이오. 모멘텀 추세 매매",
    ),
}

# Fallback for unclassified events
DEFAULT_AXIS = AxisProfile(
    axis_id="UNKNOWN",
    axis_name_kr="기타",
    event_types=["GENERAL"],
    speed="SLOW",
    fear_eligible=False,
    sl_modifier=1.0,
    tp_modifier=1.0,
    hold_modifier=1.0,
    qty_modifier=1.0,
    pre_event_block_hours=1.0,
    description="분류 불가 이벤트",
)


# -----------------------------------------------------------------------
# Event type → Axis mapping (keyword-based)
# -----------------------------------------------------------------------

EVENT_TYPE_KEYWORDS: dict[str, list[str]] = {
    # 축1: ECONOMY
    "CPI":        ["cpi", "consumer price index"],
    "PCE":        ["pce", "personal consumption"],
    "NFP":        ["non-farm", "nonfarm", "payroll", "payrolls"],
    "GDP":        ["gdp", "gross domestic"],
    "ISM":        ["ism ", "pmi", "manufacturing pmi", "services pmi"],
    "EMPLOYMENT": ["jobless claims", "unemployment rate", "employment situation"],
    "RETAIL":     ["retail sales"],
    "COMMODITY":  ["opec", "crude oil", "oil price", "natural gas price",
                   "wti", "brent", "oil surge", "oil drop", "oil jumps",
                   "oil falls", "lng export", "oil production",
                   "gold price", "gold surge", "gold rally", "gold drops",
                   "silver price", "silver surge", "silver rally",
                   "precious metal", "bullion", "gold futures", "silver futures",
                   "rare earth", "lithium", "cobalt", "copper price",
                   "commodity price", "iron ore", "palladium", "platinum"],
    # 축2: CORPORATE
    "EARNINGS":   ["earnings", "guidance", "quarter", "revenue", "eps",
                   "beats", "misses", "supply chain", "quarterly results"],
    "ANALYST":    ["downgrade", "upgrade", "price target", "analyst",
                   "overweight", "underweight", "buy rating", "sell rating"],
    "INSIDER":    ["insider", "director sold", "ceo sold", "stake sold",
                   "insider buying", "13f filing"],
    # 축3: GOVERN
    "GEOPOLITICAL": ["iran", "missile", "war", "attack", "israel", "strait",
                     "middle east", "pentagon", "military", "defense", "conflict",
                     "red sea", "hormuz", "sanctions", "north korea", "china",
                     "taiwan", "ukraine", "russia"],
    "REGULATION":   ["probe", "lawsuit", "antitrust", "tariff", "restriction",
                     " ban ", "bans ", "banning", "executive order", "indictment"],
    "TRUMP":        ["trump", "truth social", "mar-a-lago", "maga",
                     "white house", "president signs", "president threatens"],
    # 축4: FEDWALL
    "FED":          ["powell", "fomc", "fed", "rate cut", "rate hike",
                     "fed funds", "monetary policy", "quantitative"],
    "FED_SPEAK":    ["fed governor", "fed president", "fed chair",
                     "waller", "bowman", "barkin", "daly", "bostic",
                     "kashkari", "goolsbee", "williams", "speaks",
                     "testimony", "hawkish", "dovish"],
    "FOMC":         ["fomc minutes", "fomc meeting", "fomc decision",
                     "interest rate decision", "dot plot"],
    "RATES":        ["treasury yield", "10-year yield", "10 year yield",
                     "us10y", "bond yield", "yield surge", "yield spike",
                     "mortgage rate", "30-year mortgage", "housing rate",
                     "yield curve", "inverted yield", "2-year yield",
                     "treasury bond", "bond market"],
    # 축5: THEME (테마/신기술)
    "AI_TECH":      ["artificial intelligence", " ai ", "chatgpt", "openai",
                     "generative ai", "large language model", "llm", "gpu demand",
                     "ai chip", "ai server", "copilot", "machine learning",
                     "nvidia ai", "ai data center", "ai infrastructure"],
    "QUANTUM":      ["quantum computing", "quantum chip", "quantum processor",
                     "qubit", "ionq", "rigetti", "d-wave", "quantum supremacy",
                     "quantum advantage"],
    "ENERGY_INFRA": ["power grid", "electricity demand", "power plant",
                     "renewable energy", "solar", "wind farm", "battery storage",
                     "energy storage", "smart grid", "utilities surge",
                     "power shortage", "blackout"],
    "DATA_CENTER":  ["data center", "hyperscaler", "cloud infrastructure",
                     "server demand", "colocation", "equinix", "digital realty",
                     "data centre"],
    "NUCLEAR":      ["nuclear", "uranium", "small modular reactor", "smr",
                     "nuclear fusion", "nuclear power", "cameco", "uec",
                     "centrus energy"],
    "SPACE_DEFENSE": ["spacex", "rocket lab", "satellite", "space force",
                      "orbital", "aerospace", "boeing defense", "northrop",
                      "l3harris", "palantir defense"],
    "ROBOTICS":     ["robot", "robotics", "humanoid", "automation", "tesla bot",
                     "optimus", "figure ai", "boston dynamics", "industrial robot"],
    "EV":           ["electric vehicle", " ev ", "ev battery", "charging station",
                     "lidar", "autonomous driving", "self-driving",
                     "solid state battery", "ev sales"],
    "BIOTECH":      ["biotech", "gene therapy", "crispr", "mrna", "fda approval",
                     "clinical trial", "drug approval", "pharma breakthrough",
                     "weight loss drug", "glp-1", "ozempic"],
    "CYBERSECURITY": ["cybersecurity", "cyber attack", "ransomware", "data breach",
                      "hack ", "hacker", "crowdstrike", "palo alto networks",
                      "zscaler", "zero trust", "endpoint security"],
    "SOFTWARE_CLOUD": ["cloud computing", "saas", "cloud revenue", "azure",
                       "aws ", "google cloud", "cloud migration",
                       "software subscription", "servicenow", "salesforce"],
    "CRYPTO":       ["bitcoin", "crypto", "ethereum", "blockchain", "coinbase",
                     "microstrategy", "digital asset", "defi", "stablecoin",
                     "crypto regulation", "sec crypto"],
    "BANKS_FINANCE": ["bank earnings", "banking sector", "jpmorgan", "goldman sachs",
                      "bank of america", "credit loss", "loan growth",
                      "net interest", "financial sector"],
}

# Event type → Axis lookup
_EVENT_TO_AXIS: dict[str, str] = {}
for _axis in AXES.values():
    for _et in _axis.event_types:
        _EVENT_TO_AXIS[_et] = _axis.axis_id


def classify_axis(event_type: str) -> AxisProfile:
    """Get the axis profile for a given event type."""
    axis_id = _EVENT_TO_AXIS.get(event_type)
    if axis_id:
        return AXES[axis_id]
    return DEFAULT_AXIS


def detect_event_type(text: str) -> tuple[str, str]:
    """Detect event type and axis from text. Returns (event_type, axis_id)."""
    lowered = text.lower()

    # Priority order: GOVERN > FEDWALL > ECONOMY > CORPORATE > THEME
    # (정부/전쟁이 제일 급함, 그 다음 FOMC, 경제지표, 기업실적, 테마 순)
    priority_order = ["GOVERN", "FEDWALL", "ECONOMY", "CORPORATE", "THEME"]

    for axis_id in priority_order:
        axis = AXES[axis_id]
        for et in axis.event_types:
            keywords = EVENT_TYPE_KEYWORDS.get(et, [])
            if any(k in lowered for k in keywords):
                return et, axis_id

    return "GENERAL", "UNKNOWN"


def apply_axis_modifiers(
    axis: AxisProfile,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_hold_days: int,
    max_qty: int,
) -> tuple[float, float, int, int]:
    """Apply axis-specific modifiers to trading parameters.

    Returns (adjusted_sl, adjusted_tp, adjusted_hold, adjusted_qty).
    """
    adj_sl = round(stop_loss_pct * axis.sl_modifier, 2)
    adj_tp = round(take_profit_pct * axis.tp_modifier, 2)
    adj_hold = max(2, round(max_hold_days * axis.hold_modifier))
    adj_qty = max(1, round(max_qty * axis.qty_modifier))
    return adj_sl, adj_tp, adj_hold, adj_qty
