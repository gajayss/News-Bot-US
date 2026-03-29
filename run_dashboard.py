"""News Bot US — Web Dashboard.

5축 뉴스 분류 + 시그널 + 캘린더 + RRG 실시간 모니터링 UI.
http://127.0.0.1:6100
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from flask import Flask, render_template_string, jsonify

import config
from news_bridge.axes import AXES
from news_bridge.event_calendar import EventCalendarState

app = Flask(__name__)
INTERFACE_DIR = config.INTERFACE_DIR
DASHBOARD_PORT = 6100

# ---------------------------------------------------------------------------
# RRG 데이터 엔진 — 뉴스 감성 기반 상대강도 그래프
# ---------------------------------------------------------------------------
# X축: RS-Ratio = 100 + (평균 감성 스코어 × SCALE)  → 100 중심
# Y축: RS-Momentum = 100 + (최근-이전 점수 변화 × SCALE) → 100 중심
# 트레일: 매 갱신 시 포인트 추가 (최대 50개 유지)
# ---------------------------------------------------------------------------
_RRG_SCALE = 8.0    # score [-1,+1] → [92,108] 범위 (IBEX 동일 수준)
_RRG_MOM_SCALE = 1.5 # dx → Y 변환 (너무 크면 수직 움직임)
_RRG_MAX_TRAIL = 50
_RRG_SMOOTH_W = 3    # 3점 롤링 스무딩 (IBEX 동일)

# 종목별 스코어 이력 {symbol: [(timestamp, score), ...]}
_symbol_score_history: dict[str, list[tuple[float, float]]] = defaultdict(list)
# 종목별 RRG 트레일 {symbol: [{x, y, quadrant, news_count}, ...]}
_symbol_rrg_trails: dict[str, list[dict]] = {}
# 마지막으로 처리한 뉴스 수 (중복 계산 방지)
_last_news_count: int = 0


def _calc_rrg_quadrant(x: float, y: float) -> str:
    if x >= 100 and y >= 100:
        return "leading"
    if x < 100 and y >= 100:
        return "improving"
    if x >= 100 and y < 100:
        return "weakening"
    return "lagging"


def _build_incremental_trail(scores: list[float]) -> list[dict]:
    """뉴스 1건씩 점진적으로 추가하면서 트레일 포인트 생성.

    RRG 회전 원리 (IBEX_US 동일):
      X = RS-Ratio  = 감성 점수의 가중 평균 (최근 가중치 높음)
      Y = RS-Momentum = X의 변화율 (= X의 미분)
      + 3점 롤링 스무딩 적용 (IBEX 동일)

    자연스러운 4분면 회전:
      강세 뉴스 유입 → X↑ Y↑ (Leading)
      강세 유지/둔화 → X↑ Y↓ (Weakening)
      약세 뉴스 유입 → X↓ Y↓ (Lagging)
      약세 바닥/반등 → X↓ Y↑ (Improving)
    """
    if not scores:
        return [{"x": 100.0, "y": 100.0, "quadrant": "leading", "news_count": 0}]

    # 1단계: 원시 X, Y 시퀀스 계산
    raw_xs = [100.0]  # 시작점
    raw_ys = [100.0]

    prev_x = 100.0
    for i in range(1, len(scores) + 1):
        sub = scores[:i]
        n = len(sub)

        # X: 가중 이동평균 (최근 뉴스에 가중치) — 클램핑
        weights = [(j + 1) for j in range(n)]
        wsum = sum(s * w for s, w in zip(sub, weights))
        x = 100 + (wsum / sum(weights)) * _RRG_SCALE
        x = max(90, min(110, x))  # 90~110 범위 제한

        # Y: X의 변화율 (모멘텀) — 클램핑으로 극단치 방지
        dx = x - prev_x
        y = 100 + dx * _RRG_MOM_SCALE
        y = max(90, min(110, y))  # 90~110 범위 제한 (IBEX 동일 수준)
        prev_x = x

        raw_xs.append(x)
        raw_ys.append(y)

    # 2단계: 3점 롤링 스무딩 (IBEX 동일)
    trail = []
    for i in range(len(raw_xs)):
        w = min(i + 1, _RRG_SMOOTH_W)
        sx = sum(raw_xs[i - w + 1:i + 1]) / w
        sy = sum(raw_ys[i - w + 1:i + 1]) / w

        # 마지막 점은 raw 값 사용 (IBEX 동일: 테이블/차트 일관성)
        if i == len(raw_xs) - 1:
            sx, sy = raw_xs[i], raw_ys[i]

        quadrant = _calc_rrg_quadrant(sx, sy)
        nc = i  # 0=시작점, 1=1건째, ...
        pt = {"x": round(sx, 2), "y": round(sy, 2), "quadrant": quadrant, "news_count": nc}

        # 너무 가까운 점 제거
        if trail and abs(trail[-1]["x"] - pt["x"]) < 0.03 and abs(trail[-1]["y"] - pt["y"]) < 0.03:
            trail[-1] = pt
        else:
            trail.append(pt)

    if len(trail) > _RRG_MAX_TRAIL:
        trail = trail[-_RRG_MAX_TRAIL:]

    return trail


def _update_rrg_data(news_events: list[dict]) -> None:
    """뉴스 이벤트로부터 RRG 데이터 갱신 — 점진적 트레일 생성."""
    global _last_news_count

    if len(news_events) == _last_news_count:
        return  # 변경 없음
    _last_news_count = len(news_events)

    now = time.time()

    # 1) 종목별 스코어 이력 구축
    _symbol_score_history.clear()  # 매번 재구축 (순서 보장)
    for ev in news_events:
        score = ev.get("score", 0)
        symbols = ev.get("symbols", [])
        ts = ev.get("_ts", now)
        for sym in symbols:
            _symbol_score_history[sym].append((ts, score))

    # 2) 워치리스트 종목에 대해 점진적 트레일 생성
    for sym in config.WATCHLIST:
        hist = _symbol_score_history.get(sym, [])
        if not hist:
            # 뉴스 없는 종목 → 중앙 고정
            _symbol_rrg_trails[sym] = [{"x": 100, "y": 100, "quadrant": "leading", "news_count": 0}]
            continue

        scores = [s for _, s in hist]
        _symbol_rrg_trails[sym] = _build_incremental_trail(scores)


def _get_rrg_trails() -> list[dict]:
    """프론트엔드용 RRG 트레일 데이터 반환 — 뉴스 있는 종목만."""
    result = []
    for sym in config.WATCHLIST:
        trail = _symbol_rrg_trails.get(sym, [])
        if not trail:
            continue
        # 뉴스 0건인 종목(중앙 고정)은 제외 → RRG를 깔끔하게
        last = trail[-1]
        if last.get("news_count", 0) == 0:
            continue
        result.append({"ticker": sym, "trail": trail})
    return result


def _read_json_items(name: str) -> list[dict]:
    path = INTERFACE_DIR / f"{name}.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("items", []))
    except Exception:
        return []


def _read_signals_store() -> list[dict]:
    """선분이력 signals_store.json 읽기 (날짜 초기화 없는 영구 파일)."""
    path = INTERFACE_DIR / "signals_store.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("items", []))
    except Exception:
        return []


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>News Bot US</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
:root{--bg:#0a0f1a;--panel:#131b2e;--hdr:#0d1322;--bdr:#1e2d4a;--t:#cbd5e1;--td:#64748b;--tw:#f1f5f9}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--t);font:17px/1.5 'Segoe UI',system-ui,sans-serif}

.top{background:var(--hdr);padding:14px 32px;display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid var(--bdr)}
.top h1{font-size:20px;color:var(--tw)}
.top .info{font-size:13px;color:var(--td);display:flex;gap:18px;align-items:center}
.dot{width:8px;height:8px;border-radius:50%;background:#22c55e;display:inline-block;animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}

.wrap{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:14px 14px;margin:0}
.fw{grid-column:1/-1}
.pnl{background:var(--panel);border-radius:6px;border:1px solid var(--bdr);overflow:hidden}
.ph{padding:10px 16px;background:var(--hdr);border-bottom:1px solid var(--bdr);display:flex;justify-content:space-between;align-items:center}
.ph b{font-size:14px;color:var(--tw);letter-spacing:.5px}
.ph small{font-size:12px;color:var(--td)}
.pb{max-height:500px;overflow-y:auto}
.pb::-webkit-scrollbar{width:5px}
.pb::-webkit-scrollbar-thumb{background:#334155;border-radius:3px}

/* Desc */
.desc{padding:8px 16px;font-size:12px;color:var(--td);background:rgba(255,255,255,.02);border-bottom:1px solid var(--bdr);line-height:1.6}
.desc b{color:#94a3b8}

/* Axis chips */
.axr{display:flex;gap:10px;padding:14px 18px;flex-wrap:wrap}
.axc{padding:9px 16px;border-radius:6px;font:700 14px/1 inherit;display:flex;align-items:center;gap:6px;flex-direction:column}
.axc .al{font-size:11px;opacity:.7;font-weight:400}
.axc i{background:rgba(255,255,255,.12);padding:2px 10px;border-radius:4px;font:700 14px/1.4 inherit;font-style:normal}
.axc .ar{display:flex;align-items:center;gap:6px}

/* Source status */
.src{display:flex;gap:8px;padding:10px 18px;flex-wrap:wrap}
.si{padding:5px 12px;border-radius:4px;font-size:12px;background:rgba(255,255,255,.04);border:1px solid var(--bdr);display:flex;align-items:center;gap:6px}
.si .sd{width:6px;height:6px;border-radius:50%}
.si .sd.on{background:#22c55e}.si .sd.off{background:#ef4444}.si .sd.wait{background:#f59e0b}

/* Table */
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:9px 12px;background:var(--hdr);color:var(--td);font-size:14px;letter-spacing:.3px;position:sticky;top:0;z-index:1;cursor:pointer;user-select:none;white-space:nowrap;border-bottom:1px solid var(--bdr)}
th:hover{color:var(--tw)}
th.r{text-align:right}
th .sa{font-size:9px;margin-left:4px;opacity:.5}
td{padding:8px 12px;border-top:1px solid rgba(255,255,255,.03);font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
td.r{text-align:right;font-variant-numeric:tabular-nums}
tr:hover td{background:rgba(255,255,255,.03)}

/* Resize */
th{position:relative}
.rz{position:absolute;right:0;top:0;width:4px;height:100%;cursor:col-resize}
.rz:hover,.rz.on{background:#3b82f6}

/* Badges */
.b{display:inline-block;padding:3px 9px;border-radius:4px;font:700 13px/1.4 inherit}
.bb{background:#14532d;color:#4ade80}.br{background:#7f1d1d;color:#fca5a5}.bn{background:#1f2937;color:#6b7280}
.bx{padding:3px 9px;border-radius:4px;font:700 12px/1.4 inherit}
.sp{color:#4ade80;font-weight:700}.sn{color:#f87171;font-weight:700}.sz{color:var(--td)}
.sym{font:700 15px/1 inherit;color:var(--tw)}
.rsn{font-size:12px;color:var(--td);max-width:300px;overflow:hidden;text-overflow:ellipsis}

/* Calendar */
.cr{padding:10px 16px;border-top:1px solid rgba(255,255,255,.04);display:flex;justify-content:space-between;align-items:center}
.cr:first-child{border-top:none}
.cr.past{opacity:.3}
.st{letter-spacing:1px;font-size:14px}
.s5{color:#ef4444}.s4{color:#f97316}.s3{color:#eab308}.s2{color:#64748b}.s1{color:#374151}
.cn{font:600 14px/1.4 inherit}.cc{font-size:12px;color:var(--td)}
.ct{text-align:right;font-size:13px}.cu{font-weight:600}.cu.urg{color:#ef4444}

/* Constraint */
.cx{padding:14px 18px;margin:10px;border-radius:6px}
.cx.ok{background:#052e16;border:1px solid #166534;color:#4ade80;font:600 15px/1.4 inherit}
.cx.w{font-size:14px}

/* Stats */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:12px 18px}
.stat{text-align:center;padding:10px;border-radius:6px;background:rgba(255,255,255,.03);border:1px solid var(--bdr)}
.stat .sv{font:700 24px/1.2 inherit;color:var(--tw)}
.stat .sl{font-size:11px;color:var(--td);margin-top:2px}

/* RRG */
.rrg-wrap{position:relative;width:100%;height:500px;overflow:hidden}
#rrg-plot{width:100%;height:100%}

/* Signal filter buttons */
.sfbtn{padding:3px 10px;border-radius:4px;font-size:11px;cursor:pointer;border:1px solid var(--bdr);background:transparent;color:var(--td);transition:all .15s}
.sfbtn.on{background:#1e40af;color:#93c5fd;border-color:#3b82f6}
.sfbtn:hover:not(.on){background:rgba(255,255,255,.06);color:var(--tw)}

/* Sector badge */
.sec{display:inline-block;padding:2px 6px;border-radius:3px;font-size:11px;font-weight:600;background:rgba(99,102,241,.15);color:#a5b4fc}
.ind{font-size:11px;color:var(--td)}

/* Active / expired badge */
.bact{background:#052e16;color:#4ade80;padding:2px 7px;border-radius:3px;font-size:11px;font-weight:600}
.bexp{background:#1c1917;color:#78716c;padding:2px 7px;border-radius:3px;font-size:11px}
</style></head>
<body>

<div class="top">
<h1>News Bot US - 뉴스 자동매매 시스템</h1>
<div class="info"><span><span class="dot"></span> 실시간</span><span>{{ source_mode }}</span><span>{{ watchlist_count }}종목</span><span id="rt"></span></div>
</div>

<div class="wrap">

<!-- Row 0: 5축 + 소스 + 통계 (full width) -->
<div class="pnl fw">
<div class="ph"><b>5축 뉴스 분류</b><small id="te"></small></div>
<div class="desc"><b>GOVERN</b>(정부/전쟁) > <b>FEDWALL</b>(연준/월가) > <b>ECONOMY</b>(경제지표) > <b>CORPORATE</b>(기업/내부자/헤지펀드) > <b>THEME</b>(테마/신기술) 순 우선순위</div>
<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
<div class="axr" id="ab" style="flex:1"></div>
<div class="stats" id="stb" style="flex:1"></div>
</div>
<div class="src" id="srcb"></div>
</div>

<!-- Row 1 Left: 매매 시그널 (이전 위치에서 위로) -->
<div class="pnl">
<div class="ph"><b>매매 시그널</b><small id="sc"></small>
<div style="display:flex;gap:6px;align-items:center">
<span style="font-size:11px;color:var(--td)">필터:</span>
<button id="sf_active" onclick="setSigFilter('active')" class="sfbtn on">활성만</button>
<button id="sf_today"  onclick="setSigFilter('today')"  class="sfbtn">오늘</button>
<button id="sf_3day"   onclick="setSigFilter('3day')"   class="sfbtn">3일</button>
<button id="sf_all"    onclick="setSigFilter('all')"    class="sfbtn">전체</button>
</div></div>
<div class="desc"><b>BUY_PUT</b>=풋매수(하락 베팅) <b>BUY_CALL</b>=콜매수(상승 베팅) <b>SELL</b>=주식 매도. 선분이력 관리: 동일(종목+방향) 열린 시그널은 중복 미등록.</div>
<div class="pb"><table id="st"><thead><tr>
<th data-c="0" data-t="s" style="width:60px">종목<div class="rz"></div></th>
<th data-c="1" data-t="s" style="width:55px">섹터<div class="rz"></div></th>
<th data-c="2" data-t="s" style="width:70px">산업군<div class="rz"></div></th>
<th data-c="3" data-t="s" style="width:90px">방향<div class="rz"></div></th>
<th data-c="4" data-t="s" style="width:60px">유형<div class="rz"></div></th>
<th data-c="5" data-t="s" style="width:70px">축<div class="rz"></div></th>
<th data-c="6" data-t="n" class="r" style="width:40px">수량<div class="rz"></div></th>
<th data-c="7" data-t="n" class="r" style="width:50px">강도<div class="rz"></div></th>
<th data-c="8" data-t="s" style="width:85px">발생일시<div class="rz"></div></th>
<th data-c="9" data-t="s" style="width:85px">종료<div class="rz"></div></th>
<th data-c="10" data-t="s">사유<div class="rz"></div></th>
</tr></thead><tbody id="sb"></tbody></table></div></div>

<!-- Row 1 Right: RRG 뉴스 상대강도 (NEW) -->
<div class="pnl">
<div class="ph"><b>종목 RRG (뉴스 상대강도)</b><small id="rrg_cnt"></small></div>
<div class="desc">워치리스트 종목의 뉴스 감성을 4분면으로 표시. <b style="color:#4ade80">Leading</b>(강세+가속) → <b style="color:#f87171">Weakening</b>(강세+감속) → <b style="color:#fb923c">Lagging</b>(약세+감속) → <b style="color:#38bdf8">Improving</b>(약세+가속) 순환. 원 크기=뉴스 건수.</div>
<div class="rrg-wrap"><div id="rrg-plot"></div></div></div>

<!-- Row 2 Left: 뉴스 이벤트 (이전 위치에서 아래로) -->
<div class="pnl">
<div class="ph"><b>뉴스 이벤트</b><small id="nc"></small></div>
<div class="desc"><b>BEAR</b>=약세, <b>BULL</b>=강세. Score -1.0=최강 약세, +1.0=최강 강세. 내부자/CEO 매도는 강제 -1.0.</div>
<div class="pb"><table id="nt"><thead><tr>
<th data-c="0" data-t="s" style="width:75px">축<span class="sa"></span><div class="rz"></div></th>
<th data-c="1" data-t="s" style="width:95px">유형<span class="sa"></span><div class="rz"></div></th>
<th data-c="2" data-t="s" style="width:65px">방향<span class="sa"></span><div class="rz"></div></th>
<th data-c="3" data-t="n" class="r" style="width:55px">점수<span class="sa"></span><div class="rz"></div></th>
<th data-c="4" data-t="s" style="width:80px">출처<span class="sa"></span><div class="rz"></div></th>
<th data-c="5" data-t="s" style="width:75px">시간<span class="sa"></span><div class="rz"></div></th>
<th data-c="6" data-t="s" style="width:90px">종목<span class="sa"></span><div class="rz"></div></th>
<th data-c="7" data-t="s">헤드라인 + 한글요약<span class="sa"></span><div class="rz"></div></th>
</tr></thead><tbody id="nb"></tbody></table></div></div>

<!-- Row 2 Right: 캘린더 + 제약 -->
<div style="display:flex;flex-direction:column;gap:14px">
<div class="pnl" style="flex:1">
<div class="ph"><b>경제 캘린더</b><small id="cc"></small></div>
<div class="desc">별 5개=최고 충격(FOMC/NFP). 4시간 전 신규진입 차단, 직후 2시간 시그널 강화.</div>
<div class="pb" id="cb"></div>
</div>
<div class="pnl">
<div class="ph"><b>진입 제약</b></div>
<div class="desc">FOMC/NFP 등 고충격 이벤트 전 자동 차단.</div>
<div id="xb"></div>
</div>
</div>

</div>

<script>
const AC={ECONOMY:'#f59e0b',CORPORATE:'#06b6d4',GOVERN:'#ef4444',FEDWALL:'#a855f7',THEME:'#22c55e',UNKNOWN:'#4b5563'};
const AK={ECONOMY:'경제지표',CORPORATE:'기업/내부자',GOVERN:'정부/전쟁',FEDWALL:'연준/월가',THEME:'테마/신기술',UNKNOWN:'미분류'};

// 영어 뉴스 한글 요약 변환
const KR_MAP=[
// 지정학
[/missile\s*(attack|launch|strike)/i,'미사일 공격'],[/drone\s*(attack|strike)/i,'드론 공격'],
[/air\s*strike/i,'공습'],[/ceasefire/i,'휴전'],[/sanctions?\b/i,'제재'],
[/tariff/i,'관세'],[/trade war/i,'무역전쟁'],[/escalat/i,'긴장 고조'],
[/retaliat/i,'보복'],[/nuclear\s*(threat|weapon|test)/i,'핵 위협'],
[/houthi/i,'후티 반군'],[/yemen/i,'예멘'],[/iran/i,'이란'],[/israel/i,'이스라엘'],
[/ukraine/i,'우크라이나'],[/russia/i,'러시아'],[/china/i,'중국'],[/taiwan/i,'대만'],
[/north korea/i,'북한'],[/trump/i,'트럼프'],[/pentagon/i,'펜타곤'],
[/executive order/i,'행정명령'],[/white house/i,'백악관'],
// 경제
[/rate\s*(cut|hike|decision)/i,'금리 결정'],[/inflation/i,'인플레이션'],
[/recession/i,'경기침체'],[/unemployment/i,'실업률'],[/jobless/i,'실업수당'],
[/payroll/i,'고용지표'],[/consumer price/i,'소비자물가(CPI)'],
[/gdp\b/i,'GDP'],[/retail sales/i,'소매판매'],[/housing/i,'주택시장'],
[/fed\b|federal reserve/i,'연준'],[/powell/i,'파월'],[/fomc/i,'FOMC'],
[/hawkish/i,'매파적'],[/dovish/i,'비둘기파'],
[/treasury yield/i,'국채금리'],[/bond yield/i,'채권수익률'],
// 기업
[/earnings?\s*(beat|miss|surpass)/i,'실적 발표'],[/guidance/i,'가이던스'],
[/revenue/i,'매출'],[/profit/i,'이익'],[/loss/i,'손실'],
[/downgrade/i,'투자등급 하향'],[/upgrade/i,'투자등급 상향'],
[/price target/i,'목표가'],[/analyst/i,'애널리스트'],
[/insider\s*(sell|sold|dump)/i,'내부자 매도'],[/ceo\s*(sell|sold)/i,'CEO 매도'],
[/buyback/i,'자사주매입'],[/dividend/i,'배당'],
[/short\s*(sell|report|squeeze)/i,'공매도'],[/hedge fund/i,'헤지펀드'],
[/13f/i,'13F 공시'],[/cathie wood|ark invest/i,'캐시우드/ARK'],
[/buffett|berkshire/i,'버핏'],[/burry/i,'마이클 버리'],
// 섹터
[/oil\s*(price|surge|drop|jump|fall)/i,'유가'],[/crude/i,'원유'],
[/natural gas/i,'천연가스'],[/gold\s*(price|surge|rally)/i,'금값'],
[/bitcoin|crypto/i,'암호화폐'],[/semiconductor|chip\b/i,'반도체'],
[/ai\b|artificial intelligence/i,'AI'],[/quantum/i,'양자컴퓨팅'],
[/nuclear\s*(power|energy|plant)/i,'원전'],[/ev\b|electric vehicle/i,'전기차'],
[/biotech|pharma/i,'바이오/제약'],[/fda\s*approv/i,'FDA 승인'],
[/data center/i,'데이터센터'],[/cloud/i,'클라우드'],
// 일반
[/surge|soar|rally|jump/i,'급등'],[/plunge|crash|plummet|tumble/i,'급락'],
[/drop|fall|decline|slip/i,'하락'],[/rise|gain|climb/i,'상승'],
[/record high/i,'신고가'],[/record low/i,'신저가'],
[/ban\b|restrict/i,'규제/금지'],[/lawsuit|sue|indict/i,'소송/기소'],
[/fraud/i,'사기'],[/bankrupt/i,'파산'],[/default/i,'디폴트'],
[/attack/i,'공격'],[/war\b/i,'전쟁'],[/conflict/i,'분쟁'],
[/threat/i,'위협'],[/defense|military/i,'군사/방위'],
];
function krTag(headline){
const tags=[];const seen=new Set();
for(const[re,kr]of KR_MAP){if(re.test(headline)&&!seen.has(kr)){tags.push(kr);seen.add(kr);if(tags.length>=3)break}}
return tags.length?'<span style="color:#fbbf24;font-size:13px;margin-left:6px">['+tags.join(' | ')+']</span>':''}
const SRCS=[
{n:'FinancialJuice',d:'실시간 매크로 뉴스 (RSS)',cy:'10분'},
{n:'Finnhub',d:'뉴스 + 캘린더 API',cy:'3분'},
{n:'Finviz',d:'SEC Form 4 내부자 매도',cy:'1시간'},
{n:'Dataroma',d:'슈퍼인베스터 82명 추적',cy:'1시간'},
{n:'Finnhub Insider',d:'내부자 거래 API',cy:'2시간'},
{n:'ARK Invest',d:'캐시우드 매매 추적',cy:'1일'},
{n:'HedgeFollow',d:'헤지펀드 13F 추적',cy:'수동'},
{n:'Fintel',d:'공매도 비율 (준비중)',cy:'-'}
];
let ss={};
const OPEN_SENTINEL='9999-12-31T23:59:59';
let sigFilter='active';  // 활성만 | today | 3day | all

function setSigFilter(f){
  sigFilter=f;
  document.querySelectorAll('.sfbtn').forEach(b=>b.classList.remove('on'));
  document.getElementById('sf_'+f).classList.add('on');
  if(window._lastSignals) renderSignals(window._lastSignals);
}

function fmtDt(iso){
  if(!iso||iso===OPEN_SENTINEL) return '';
  const d=new Date(iso);
  const mm=String(d.getMonth()+1).padStart(2,'0');
  const dd=String(d.getDate()).padStart(2,'0');
  const hh=String(d.getHours()).padStart(2,'0');
  const mi=String(d.getMinutes()).padStart(2,'0');
  return `${mm}-${dd} ${hh}:${mi}`;
}

function renderSignals(sigs){
  window._lastSignals=sigs;
  const now=new Date();
  const filtered=sigs.filter(s=>{
    const ea=s.expired_at||OPEN_SENTINEL;
    const ca=s.created_at||'';
    const isActive=(ea===OPEN_SENTINEL);
    if(sigFilter==='active') return isActive;
    if(sigFilter==='today'){
      const t=new Date(ca);
      return t.toDateString()===now.toDateString();
    }
    if(sigFilter==='3day'){
      const t=new Date(ca);
      return (now-t)<3*86400*1000;
    }
    return true; // all
  });
  const rev=filtered.slice().reverse();
  let sh='';
  for(const s of rev){
    const ib=s.side&&s.side.includes('BUY');
    const ac=AC[s.axis_id||'UNKNOWN']||'#4b5563';
    const ea=s.expired_at||OPEN_SENTINEL;
    const isActive=(ea===OPEN_SENTINEL);
    const expBadge=isActive
      ?'<span class="bact">활성중</span>'
      :`<span class="bexp" title="${ea}">${fmtDt(ea)}</span>`;
    const sect=s.sector||'';
    const ind=s.industry||'';
    sh+=`<tr>
<td class="sym">${s.symbol||''}</td>
<td><span class="sec">${sect}</span></td>
<td class="ind">${ind}</td>
<td><span class="b ${ib?'bb':'br'}">${s.side||''}</span></td>
<td style="font-size:12px">${s.asset_class||''}</td>
<td><span class="bx" style="background:${ac}18;color:${ac};font-size:11px">${s.axis_id||''}</span></td>
<td class="r" data-v="${s.qty||1}">${s.qty||1}</td>
<td class="r" data-v="${s.strength||0}">${(s.strength||0).toFixed(2)}</td>
<td style="font-size:11px;color:var(--td)">${fmtDt(s.created_at||'')}</td>
<td>${expBadge}</td>
<td class="rsn" title="${(s.reason||'').replace(/"/g,'&quot;')}">${(s.reason||'').substring(0,50)}</td></tr>`}
  document.getElementById('sb').innerHTML=sh||'<tr><td colspan="11" style="color:var(--td);padding:20px;text-align:center">시그널 대기중...</td></tr>';
}

document.querySelectorAll('th[data-c]').forEach(h=>{
h.addEventListener('click',e=>{
if(e.target.classList.contains('rz'))return;
const t=h.closest('table'),c=+h.dataset.c,tp=h.dataset.t,id=t.id;
const p=ss[id],d=(p&&p.c===c&&p.d==='asc')?'desc':'asc';
ss[id]={c,d,tp};
t.querySelectorAll('th .sa').forEach(a=>a.textContent='');
h.querySelector('.sa').textContent=d==='asc'?'\u25B2':'\u25BC';
const tb=t.querySelector('tbody'),rows=[...tb.querySelectorAll('tr')];
rows.sort((a,b)=>{let va=a.cells[c]?.dataset?.v||a.cells[c]?.textContent||'',vb=b.cells[c]?.dataset?.v||b.cells[c]?.textContent||'';
if(tp==='n'){va=parseFloat(va)||0;vb=parseFloat(vb)||0}else{va=va.toLowerCase();vb=vb.toLowerCase()}
return va<vb?(d==='asc'?-1:1):va>vb?(d==='asc'?1:-1):0});
rows.forEach(r=>tb.appendChild(r))})});

document.querySelectorAll('.rz').forEach(r=>{let sx,sw,th;
r.addEventListener('mousedown',e=>{th=r.parentElement;sx=e.pageX;sw=th.offsetWidth;r.classList.add('on');
const mv=e2=>{th.style.width=Math.max(40,sw+e2.pageX-sx)+'px'};
const up=()=>{r.classList.remove('on');document.removeEventListener('mousemove',mv);document.removeEventListener('mouseup',up)};
document.addEventListener('mousemove',mv);document.addEventListener('mouseup',up);e.preventDefault()})});

// 소스 현황 렌더
let srch='';
SRCS.forEach(s=>{
const st=s.cy==='-'?'off':s.cy==='수동'?'wait':'on';
srch+=`<div class="si"><span class="sd ${st}"></span>${s.n}<span style="color:var(--td);font-size:11px">${s.d} | ${s.cy}</span></div>`;
});
document.getElementById('srcb').innerHTML=srch;

// ── RRG 렌더링 (IBEX_US buildRRGWithTrails 적용) ──────────────
function buildNewsRRG(elId, trails) {
  const el = document.getElementById(elId);
  if (!el) return;

  const quadColors = { leading: '#4ade80', improving: '#facc15', weakening: '#3b82f6', lagging: '#ef4444' };
  const RECENT_N = 10;

  // 선형 매핑 — 뉴스 데이터는 90~110 범위이므로 stretch 불필요
  const sxf = v => v;
  const syf = v => v;

  // 범위 계산 — 고정 범위 (85~115) + 데이터 확장
  // 충분한 여백으로 점이 모서리에 붙지 않게
  const allX = [], allY = [];
  (trails || []).forEach(({trail}) => {
    (trail || []).forEach(pt => { allX.push(pt.x); allY.push(pt.y); });
  });

  const xMin = allX.length ? Math.min(...allX) : 95;
  const xMax = allX.length ? Math.max(...allX) : 105;
  const yMin = allY.length ? Math.min(...allY) : 95;
  const yMax = allY.length ? Math.max(...allY) : 105;

  // 최소 85~115 보장, 데이터가 넘으면 확장
  const xlo = Math.min(85, xMin - 3);
  const xhi = Math.max(115, xMax + 3);
  const ylo = Math.min(85, yMin - 3);
  const yhi = Math.max(115, yMax + 3);

  // 분면별 TOP2 — 변화 속도(speed) 기준 선별
  const quadGroups = { leading: [], improving: [], weakening: [], lagging: [] };
  (trails || []).forEach(({ticker, trail}) => {
    if (!trail || !trail.length) return;
    const last = trail[trail.length - 1];
    const q = (last.quadrant || '').toLowerCase();
    // 속도 계산 (trail 2개 이상이면)
    let spd = 0;
    if (trail.length >= 2) {
      const lb = Math.min(4, trail.length - 1);
      const prev = trail[trail.length - 1 - lb];
      const dx = (last.x - prev.x) / lb;
      const dy = (last.y - prev.y) / lb;
      spd = Math.sqrt(dx * dx + dy * dy);
    }
    if (quadGroups[q]) quadGroups[q].push({ ticker, x: last.x, cnt: last.news_count || 0, speed: spd });
  });
  const topSet = new Set();
  Object.values(quadGroups).forEach(group => {
    group.sort((a, b) => b.speed - a.speed || b.cnt - a.cnt);  // 속도 우선, 동률이면 뉴스 건수
    group.slice(0, 5).forEach(g => topSet.add(g.ticker));  // 분면별 TOP5
  });
  // Leading 분면 TOP2 (IBEX 동일 — 폰트/마커 추가 강조)
  const leadingTop2Set = new Set();
  (quadGroups['leading'] || []).slice(0, 2).forEach(g => leadingTop2Set.add(g.ticker));

  // RS Score 1위 (전체 기준: 뉴스 건수 + 속도 종합)
  const _ranked = (trails || [])
    .filter(t => t.trail && t.trail.length)
    .map(t => ({ ticker: t.ticker, cnt: t.trail[t.trail.length - 1].news_count || 0 }))
    .sort((a, b) => b.cnt - a.cnt);
  const topTicker = _ranked.length ? _ranked[0].ticker : null;

  // 비-TOP5 종목은 완전 숨김 (뉴스 있어도 TOP5 밖이면 안 보임)
  const visibleTickers = new Set([...topSet]);
  if (topTicker) visibleTickers.add(topTicker);

  // 렌더 순서 — TOP5만 표시 (나머지 숨김)
  const orderedTrails = [
    ...(trails || []).filter(t => t.trail && t.trail.length && visibleTickers.has(t.ticker) && t.ticker !== topTicker),
    ...(trails || []).filter(t => t.ticker === topTicker && t.trail && t.trail.length),
  ];

  const traces = [];

  orderedTrails.forEach(({ticker, trail}) => {
    if (!trail || !trail.length) return;
    const last = trail[trail.length - 1];
    const q = (last.quadrant || '').toLowerCase();
    const color = quadColors[q] || '#64748b';
    const isTop        = ticker === topTicker;
    const isTop2       = topSet.has(ticker);
    const isLeadTop2   = leadingTop2Set.has(ticker);
    const nc = last.news_count || 0;
    const hoverTxt = `${ticker}<br>뉴스 ${nc}건<br>감성: ${(last.x - 100).toFixed(1)}<br>모멘텀: ${(last.y - 100).toFixed(1)}`;

    const n = trail.length;
    const splitAt = Math.max(0, n - RECENT_N);
    const oldPart = trail.slice(0, splitAt);
    const nowPart = trail.slice(splitAt, n - 1);
    const px = p => sxf(p.x);
    const py = p => syf(p.y);

    // 과거 트레일 — 비TOP2는 거의 안 보이게
    if (oldPart.length > 1) {
      traces.push({
        x: oldPart.map(px), y: oldPart.map(py),
        mode: 'lines', type: 'scatter',
        line: { color, width: 1, dash: 'dot' },
        opacity: isTop2 ? 0.07 : 0.04,
        showlegend: false, hoverinfo: 'skip',
      });
    }

    // 최근 트레일 — DOT + 선
    if (nowPart.length > 0) {
      const recentLine = [oldPart.length ? oldPart[oldPart.length - 1] : null, ...nowPart].filter(Boolean);
      traces.push({
        x: recentLine.map(px), y: recentLine.map(py),
        mode: 'lines', type: 'scatter',
        line: { color: isTop ? '#fbbf24' : color, width: isTop ? 1.5 : isTop2 ? 1.2 : 0.8, dash: 'dot' },
        opacity: isTop ? 0.30 : isTop2 ? 0.22 : 0.10,
        showlegend: false, hoverinfo: 'skip',
      });
      traces.push({
        x: nowPart.map(px), y: nowPart.map(py),
        mode: 'markers', type: 'scatter',
        marker: { size: isTop ? 5 : isTop2 ? 4 : 2, color: isTop ? '#fbbf24' : color, opacity: isTop ? 0.60 : isTop2 ? 0.45 : 0.12 },
        showlegend: false, hoverinfo: 'skip',
      });
    }

    // 현재 위치 마커 — IBEX 동일 스타일 (TOP2만 highlight)
    traces.push({
      x: [sxf(last.x)], y: [syf(last.y)],
      mode: 'markers+text', type: 'scatter',
      name: ticker,
      hovertext: [hoverTxt],
      hovertemplate: '%{hovertext}<extra></extra>',
      text: [isTop ? `★ ${ticker}` : ticker],
      textposition: 'top center',
      textfont: {
        size:   isTop ? 11 : isLeadTop2 ? 12 : isTop2 ? 10 : 9,
        color:  isTop ? '#fbbf24' : isLeadTop2 ? '#f1f5f9' : isTop2 ? '#e2e8f0' : '#94a3b8',
        family: 'Segoe UI',
      },
      marker: {
        size:    isTop ? 16 : isLeadTop2 ? 12 : isTop2 ? 9 : 7,
        symbol:  isTop ? 'star' : 'circle',
        color:   isTop ? '#fbbf24' : color,
        opacity: isTop ? 1.0 : isLeadTop2 ? 1.0 : isTop2 ? 0.92 : 0.70,
        line: {
          color: isTop ? 'rgba(251,191,36,0.7)' : isLeadTop2 ? 'rgba(255,255,255,0.5)' : isTop2 ? 'rgba(255,255,255,0.35)' : 'rgba(255,255,255,0.15)',
          width: isTop ? 2 : isLeadTop2 ? 2 : isTop2 ? 1.5 : 1,
        },
      },
      showlegend: false,
    });
  });

  // 모멘텀 화살표 — 변화 방향 + 속도 강조 (IBEX 동일)
  const arrowAnnotations = [];
  orderedTrails.forEach(({ticker, trail}) => {
    if (!trail || trail.length < 2) return;
    const n = trail.length;
    const last = trail[n - 1];
    const lookback = Math.min(4, n - 1);
    const prev = trail[n - 1 - lookback];
    const raw_dx = (last.x - prev.x) / lookback;
    const raw_dy = (last.y - prev.y) / lookback;
    const speed = Math.sqrt(raw_dx * raw_dx + raw_dy * raw_dy);
    if (speed < 0.02) return;   // 움직임 없으면 생략

    const q = (last.quadrant || '').toLowerCase();
    const color = quadColors[q] || '#64748b';
    const isTop2 = topSet.has(ticker);

    // 화살표 끝점: 속도 비례, 최대 2.0 (IBEX 동일)
    const tipScale = Math.min(speed * 2.5, 2.0);
    const tip_raw_x = last.x + raw_dx * tipScale;
    const tip_raw_y = last.y + raw_dy * tipScale;

    arrowAnnotations.push({
      x: sxf(tip_raw_x), y: syf(tip_raw_y),
      ax: sxf(last.x), ay: syf(last.y),
      axref: 'x', ayref: 'y', xref: 'x', yref: 'y',
      text: '', showarrow: true,
      arrowhead: 2,
      arrowsize:  isTop2 ? 1.0 : 0.7,
      arrowwidth: isTop2 ? 1.8 : 0.9,
      arrowcolor: color,
      opacity: isTop2 ? 0.85 : 0.40,
    });
  });

  const C = 100;
  const layout = {
    paper_bgcolor: 'transparent', plot_bgcolor: '#0a0d14',
    font: { family: 'Segoe UI', size: 10, color: '#64748b' },
    margin: { l: 10, r: 10, t: 10, b: 10 },
    xaxis: {
      title: { text: '← 약세 (Bearish)    감성 점수    강세 (Bullish) →', font: { size: 10, color: '#475569' } },
      range: [xlo, xhi], showticklabels: false,
      gridcolor: 'transparent', zerolinecolor: 'transparent',
    },
    yaxis: {
      title: { text: '모멘텀 (Momentum)', font: { size: 10, color: '#475569' } },
      range: [ylo, yhi], showticklabels: false,
      gridcolor: 'transparent', zerolinecolor: 'transparent',
    },
    shapes: [
      { type:'rect', x0:C, x1:xhi, y0:C, y1:yhi, xref:'x', yref:'y', layer:'below', fillcolor:'rgba(74,222,128,0.18)',  line:{width:0} },
      { type:'rect', x0:xlo, x1:C,  y0:C, y1:yhi, xref:'x', yref:'y', layer:'below', fillcolor:'rgba(250,204,21,0.12)', line:{width:0} },
      { type:'rect', x0:C, x1:xhi, y0:ylo, y1:C,  xref:'x', yref:'y', layer:'below', fillcolor:'rgba(59,130,246,0.12)', line:{width:0} },
      { type:'rect', x0:xlo, x1:C,  y0:ylo, y1:C,  xref:'x', yref:'y', layer:'below', fillcolor:'rgba(239,68,68,0.18)',  line:{width:0} },
      { type:'line', x0:C, x1:C, y0:ylo, y1:yhi, xref:'x', yref:'y', line:{color:'#4a5568', width:1.5, dash:'dot'} },
      { type:'line', x0:xlo, x1:xhi, y0:C, y1:C, xref:'x', yref:'y', line:{color:'#4a5568', width:1.5, dash:'dot'} },
    ],
    annotations: [
      { x:xhi, y:yhi, xref:'x', yref:'y', text:'LEADING 강세가속',   showarrow:false, font:{size:11,color:'#4ade80'}, xanchor:'right', yanchor:'top' },
      { x:xlo, y:yhi, xref:'x', yref:'y', text:'IMPROVING 회복중', showarrow:false, font:{size:11,color:'#facc15'}, xanchor:'left',  yanchor:'top' },
      { x:xhi, y:ylo, xref:'x', yref:'y', text:'WEAKENING 약세전환', showarrow:false, font:{size:11,color:'#3b82f6'}, xanchor:'right', yanchor:'bottom' },
      { x:xlo, y:ylo, xref:'x', yref:'y', text:'LAGGING 약세지속',   showarrow:false, font:{size:11,color:'#ef4444'}, xanchor:'left',  yanchor:'bottom' },
      ...arrowAnnotations,
    ],
  };

  if (el._hasPlot) {
    Plotly.react(el, traces, layout, { displayModeBar: false, responsive: true });
  } else {
    Plotly.newPlot(el, traces, layout, { displayModeBar: false, responsive: true });
    el._hasPlot = true;
  }
}

function go(){
fetch('/api/state').then(r=>r.json()).then(d=>{
let ah='',tot=0;
for(const[id,n]of Object.entries(d.axis_counts)){tot+=n;const c=AC[id]||'#4b5563';const kr=AK[id]||id;
ah+=`<div class="axc" style="background:${c}18;color:${c};border:1px solid ${c}40"><div class="ar">${id}<i>${n}</i></div><div class="al">${kr}</div></div>`}
document.getElementById('ab').innerHTML=ah;
document.getElementById('te').textContent=tot+'건 수집';

// 통계
const ev=d.news_events||[];
const sg=[...d.stock_signals||[],...d.option_signals||[]];
const bears=ev.filter(e=>e.direction==='BEARISH').length;
const bulls=ev.filter(e=>e.direction==='BULLISH').length;
const puts=sg.filter(s=>s.side&&s.side.includes('PUT')).length;
const calls=sg.filter(s=>s.side&&s.side.includes('CALL')).length;
document.getElementById('stb').innerHTML=`
<div class="stat"><div class="sv">${ev.length}</div><div class="sl">수집 뉴스</div></div>
<div class="stat"><div class="sv" style="color:#f87171">${bears}</div><div class="sl">약세 (BEAR)</div></div>
<div class="stat"><div class="sv" style="color:#4ade80">${bulls}</div><div class="sl">강세 (BULL)</div></div>
<div class="stat"><div class="sv">${sg.length}</div><div class="sl">매매 시그널 (PUT ${puts} / CALL ${calls})</div></div>`;

const evr=ev.slice().reverse();
document.getElementById('nc').textContent=evr.length+'건';
let nh='';
for(const e of evr){const c=AC[e.axis_id||'UNKNOWN']||'#4b5563';
const dc=e.direction==='BULLISH'?'bb':e.direction==='BEARISH'?'br':'bn';
const sc2=e.score>0?'sp':e.score<0?'sn':'sz';
const sv=(e.score>=0?'+':'')+(e.score||0).toFixed(2);
const syms=(e.symbols||[]).slice(0,5).join(', ')+(((e.symbols||[]).length>5)?'...':'');
const src=(e.source||'').replace('FinancialJuice','FJ').replace('SEC Form 4','SEC').substring(0,12);
const pub=e.published_at||'';
const pubShort=pub?pub.replace(/T/,' ').substring(0,16).replace(/^\d{4}-/,''):'';
nh+=`<tr><td><span class="bx" style="background:${c}18;color:${c}">${e.axis_id||'?'}</span></td>
<td style="font-size:13px">${e.event_type||''}</td>
<td><span class="b ${dc}">${(e.direction||'').slice(0,4)}</span></td>
<td class="r ${sc2}" data-v="${e.score||0}">${sv}</td>
<td style="font-size:12px;color:var(--td)">${src}</td>
<td style="font-size:12px;color:var(--td)" title="${pub}">${pubShort}</td>
<td style="font-size:13px" title="${(e.symbols||[]).join(', ')}">${syms}</td>
<td title="${(e.headline||'').replace(/"/g,'&quot;')}" style="font-size:14px;white-space:normal;max-width:700px">${e.headline||''}${krTag(e.headline||'')}</td></tr>`}
document.getElementById('nb').innerHTML=nh;

// 시그널 렌더링 — 선분이력 기반 필터 적용
renderSignals(d.all_signals||[]);
document.getElementById('sc').textContent=(d.all_signals||[]).length+'건';

const cl=d.calendar_events||[];
document.getElementById('cc').textContent=cl.length+'건';
let ch='';
for(const c of cl){const ps=c.status==='PAST';const uc=!ps&&c.hours_until<4?'urg':'';
ch+=`<div class="cr ${ps?'past':''}"><div><span class="st s${c.impact_level}">${'\u2605'.repeat(c.impact_level||1)}${'\u2606'.repeat(5-(c.impact_level||1))}</span> <span class="cn">${c.event_name||''}</span> <span class="cc">${c.category||''}</span></div>
<div class="ct"><div>${c.date||''} ${c.time||''}</div><div class="cu ${uc}">${ps?'지남':(c.hours_until).toFixed(1)+'시간 후'}</div></div></div>`}
document.getElementById('cb').innerHTML=ch||'<div style="padding:20px;color:var(--td)">예정된 이벤트 없음</div>';

const con=d.constraint||{};let cx;
if(con.constrained){const cc2=con.action==='BLOCK'?'#ef4444':con.action==='REDUCE'?'#f97316':'#eab308';
const act_kr=con.action==='BLOCK'?'신규 진입 차단':con.action==='REDUCE'?'수량 축소':'주의';
cx=`<div class="cx w" style="border:1px solid ${cc2}60;background:${cc2}0a"><div style="color:${cc2};font:700 16px/1.4 inherit;margin-bottom:6px">${act_kr}</div><div>${con.reason||''}</div><div style="margin-top:4px;color:var(--td);font-size:12px">${con.event_name} | ${con.hours_until}시간 후 | 충격도 ${con.impact_level}</div></div>`}
else{cx='<div class="cx ok">제약 없음 - 정상 매매 가능</div>'}
document.getElementById('xb').innerHTML=cx;

// RRG 렌더링
const rrg=d.rrg_trails||[];
document.getElementById('rrg_cnt').textContent=rrg.length+'종목';
buildNewsRRG('rrg-plot', rrg);

document.getElementById('rt').textContent=new Date().toLocaleTimeString()})
.catch(e=>{document.getElementById('rt').textContent='Err: '+e.message})}

go();setInterval(go,5000);
</script></body></html>"""


@app.route("/")
def index():
    return render_template_string(
        DASHBOARD_HTML,
        source_mode=config.NEWS_SOURCE_MODE,
        watchlist_count=len(config.WATCHLIST),
    )


@app.route("/api/state")
def api_state():
    news_events = _read_json_items("news_events")
    stock_signals = _read_json_items("stock_signals")
    option_signals = _read_json_items("option_signals")

    # 선분이력 시그널 store (중복 제거된 영구 보관본)
    all_signals = _read_signals_store()

    # RRG 데이터 갱신
    _update_rrg_data(news_events)
    rrg_trails = _get_rrg_trails()

    axis_counts: dict[str, int] = {}
    for ev in news_events:
        ax = ev.get("axis_id", "UNKNOWN")
        axis_counts[ax] = axis_counts.get(ax, 0) + 1
    for ax_id in AXES:
        axis_counts.setdefault(ax_id, 0)

    try:
        cal = EventCalendarState(
            pre_event_block_hours=config.PRE_EVENT_BLOCK_HOURS,
            post_event_boost_hours=config.POST_EVENT_BOOST_HOURS,
        )
        if config.NEWS_SOURCE_MODE == "sample":
            from news_bridge.sources.calendar_source import fetch_sample_calendar
            cal.load_events(fetch_sample_calendar())
        cal_events = cal.get_event_summary()
        constraint = cal.get_active_constraints()
    except Exception:
        cal_events = []
        constraint = {"constrained": False}

    return jsonify({
        "news_events": news_events[-100:],
        "stock_signals": stock_signals[-50:],
        "option_signals": option_signals[-50:],
        "all_signals": all_signals,           # 선분이력 기반 전체 시그널 (중복 제거)
        "axis_counts": axis_counts,
        "calendar_events": cal_events,
        "constraint": constraint,
        "rrg_trails": rrg_trails,
    })


if __name__ == "__main__":
    print("=" * 60)
    print(f"  News Bot US — Dashboard")
    print(f"  http://127.0.0.1:{DASHBOARD_PORT}")
    print("=" * 60)
    app.run(host="127.0.0.1", port=DASHBOARD_PORT, debug=False)
