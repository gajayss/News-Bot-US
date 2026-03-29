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
    """signals_store.json 단일 소스에서 시그널 읽기.

    run_news_radar 기동 시 backfill_from_daily()로 daily 파일이 항상 이미 마이그레이션됨.
    따라서 fallback 없이 signals_store만 사용 — 단일 진실(Single Source of Truth).
    """
    path = INTERFACE_DIR / "signals_store.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = list(payload.get("items", []))
        # created_at 기준 최신순 정렬
        items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return items
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
/* 4색 방향 뱃지: Stock Buy=Green, Option Buy=Yellow, Stock Sell=Red, Option Sell=Blue */
.bsg{background:#14532d;color:#4ade80}        /* Stock BUY_CALL — Green */
.bsy{background:#713f12;color:#fde68a}        /* Option BUY (PUT/CALL) — Yellow */
.bsr{background:#7f1d1d;color:#fca5a5}        /* Stock SELL — Red */
.bsb{background:#1e3a5f;color:#93c5fd}        /* Option SELL — Blue */
.bn{background:#1f2937;color:#6b7280}
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
/* Asset class filter (전체/STOCK/OPTION) */
.afbtn{padding:3px 10px;border-radius:4px;font-size:11px;cursor:pointer;border:1px solid var(--bdr);background:transparent;color:var(--td);transition:all .15s}
.afbtn.on{background:#134e4a;color:#5eead4;border-color:#14b8a6}
.afbtn:hover:not(.on){background:rgba(255,255,255,.06);color:var(--tw)}
/* Emoji side-filter bar */
.emj-bar{display:flex;gap:6px;padding:7px 16px;border-bottom:1px solid var(--bdr);align-items:center;flex-wrap:wrap}
.emjbtn{padding:4px 12px;border-radius:20px;font-size:13px;cursor:pointer;border:1px solid transparent;background:rgba(255,255,255,.04);color:var(--t);transition:all .15s;white-space:nowrap}
.emjbtn.on{border-color:rgba(255,255,255,.25);background:rgba(255,255,255,.12);color:var(--tw)}
.emjbtn:hover:not(.on){background:rgba(255,255,255,.08)}
/* ROC (변화율) cell */
.roc-up{color:#4ade80;font-weight:700;font-size:12px}
.roc-dn{color:#f87171;font-weight:700;font-size:12px}
.roc-na{color:var(--td);font-size:11px}

/* Sector badge */
.sec{display:inline-block;padding:2px 6px;border-radius:3px;font-size:11px;font-weight:600;background:rgba(99,102,241,.15);color:#a5b4fc}
.ind{font-size:11px;color:var(--td)}

/* Active / expired badge */
.bact{background:#052e16;color:#4ade80;padding:2px 7px;border-radius:3px;font-size:11px;font-weight:600}
.bexp{background:#1c1917;color:#78716c;padding:2px 7px;border-radius:3px;font-size:11px}

/* ── 경제 캘린더 (재설계) ───────────────────────────── */
.cal-fbar{display:flex;gap:5px;flex-wrap:wrap;padding:8px 14px;border-bottom:1px solid var(--bdr);background:rgba(255,255,255,.01)}
.cfbtn{padding:3px 10px;border-radius:4px;font-size:11px;cursor:pointer;border:1px solid var(--bdr);background:transparent;color:var(--td);transition:all .15s;white-space:nowrap}
.cfbtn.on{color:#fff;border-color:currentColor}
.cfbtn:hover:not(.on){background:rgba(255,255,255,.06);color:var(--tw)}
.cfbtn[data-k="FOMC"].on{background:#7f1d1d;border-color:#ef4444;color:#fca5a5}
.cfbtn[data-k="FED_SPEAK"].on{background:#3b0764;border-color:#a855f7;color:#d8b4fe}
.cfbtn[data-k="PCE"].on,.cfbtn[data-k="CPI"].on{background:#1e3a5f;border-color:#3b82f6;color:#93c5fd}
.cfbtn[data-k="NFP"].on,.cfbtn[data-k="EMPLOYMENT"].on{background:#14532d;border-color:#22c55e;color:#86efac}
.cfbtn[data-k="ISM"].on,.cfbtn[data-k="GDP"].on,.cfbtn[data-k="RETAIL"].on{background:#422006;border-color:#f97316;color:#fdba74}
.cfbtn[data-k="TRUMP"].on{background:#1c1917;border-color:#78716c;color:#d6d3d1}
.cfbtn[data-k="EARNINGS"].on{background:#1e1b4b;border-color:#6366f1;color:#a5b4fc}
.cfbtn[data-k="ALL"].on{background:#1e293b;border-color:#475569;color:#f1f5f9}

/* Date group separator */
.cal-day-hdr{display:flex;align-items:center;gap:8px;padding:10px 14px 4px;color:#475569;font-size:11px;letter-spacing:.5px}
.cal-day-hdr .cdl{flex:1;height:1px;background:var(--bdr)}
.cal-day-hdr .cdt{white-space:nowrap;font-weight:600;color:#64748b}

/* Event card grid */
.cal-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;padding:0 10px 8px}
@media(max-width:900px){.cal-grid{grid-template-columns:1fr}}

/* Individual event card */
.cal-card{background:rgba(255,255,255,.025);border-radius:6px;border:1px solid var(--bdr);padding:10px 12px;display:flex;flex-direction:column;gap:6px;transition:border-color .2s;position:relative}
.cal-card:hover{border-color:#334155}
.cal-card.past{opacity:.35;filter:saturate(0)}
.cal-card.urgent{border-color:#ef444460;animation:urg-pulse 2s ease-in-out infinite}
@keyframes urg-pulse{0%,100%{box-shadow:0 0 0 0 rgba(239,68,68,.15)}50%{box-shadow:0 0 0 6px rgba(239,68,68,.0)}}

.cal-card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:6px}
.cal-imp{font-size:13px;letter-spacing:1px;line-height:1}
.cal-cat{padding:2px 7px;border-radius:3px;font-size:10px;font-weight:700;letter-spacing:.5px}
.cal-name{font-size:13px;color:var(--tw);line-height:1.35;font-weight:500}
.cal-vals{display:flex;gap:10px;flex-wrap:wrap}
.cv-box{display:flex;flex-direction:column;align-items:center;gap:1px;min-width:48px}
.cv-lbl{font-size:9px;color:#475569;letter-spacing:.5px;font-weight:600}
.cv-val{font-size:13px;font-weight:700;color:#94a3b8}
.cv-val.act-beat{color:#4ade80}
.cv-val.act-miss{color:#f87171}
.cv-val.act-val{color:#f1f5f9}
.cv-val.fcst-val{color:#93c5fd}
.cv-val.prev-val{color:#64748b}
.cal-footer{display:flex;align-items:center;justify-content:space-between}
.cal-time-txt{font-size:11px;color:#475569}

/* Animated SVG clock */
.clk-wrap{display:flex;align-items:center;gap:5px}
.clk-svg{flex-shrink:0}
.clk-label{font-size:11px;font-weight:600;min-width:36px;text-align:right}
.clk-mhand{transform-box:fill-box;transform-origin:50% 100%}

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

<!-- Row 1 Left: 매매 시그널 -->
<div class="pnl">
<div class="ph"><b>매매 시그널</b><small id="sc"></small>
<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
<button id="af_all"    onclick="setAssetFilter('all')"    class="afbtn on">전체</button>
<button id="af_STOCK"  onclick="setAssetFilter('STOCK')"  class="afbtn">📌 STOCK</button>
<button id="af_OPTION" onclick="setAssetFilter('OPTION')" class="afbtn">⚡ OPTION</button>
<span style="width:1px;background:var(--bdr);height:16px;display:inline-block;margin:0 2px"></span>
<button id="sf_active" onclick="setSigFilter('active')" class="sfbtn on">활성만</button>
<button id="sf_today"  onclick="setSigFilter('today')"  class="sfbtn">오늘</button>
<button id="sf_3day"   onclick="setSigFilter('3day')"   class="sfbtn">3일</button>
<button id="sf_all"    onclick="setSigFilter('all')"    class="sfbtn">전체</button>
</div></div>
<!-- 이모지 필터바 — 클릭 시 해당 사이드만 표시 (다시 클릭 시 해제) -->
<div class="emj-bar">
<span style="font-size:11px;color:var(--td);margin-right:4px">유형 필터:</span>
<button id="emj_all"      onclick="setSideFilter('all')"      class="emjbtn on">🔲 전체</button>
<button id="emj_BUY_PUT"  onclick="setSideFilter('BUY_PUT')"  class="emjbtn">📉 BUY_PUT <span style="font-size:10px;color:var(--td)">풋매수·하락베팅</span></button>
<button id="emj_BUY_CALL" onclick="setSideFilter('BUY_CALL')" class="emjbtn">📈 BUY_CALL <span style="font-size:10px;color:var(--td)">콜매수·상승베팅</span></button>
<button id="emj_SELL"     onclick="setSideFilter('SELL')"     class="emjbtn">💸 SELL <span style="font-size:10px;color:var(--td)">주식매도</span></button>
</div>
<div class="pb"><table id="st"><thead><tr>
<th data-c="0" data-t="s" style="width:60px">종목<div class="rz"></div></th>
<th data-c="1" data-t="s" style="width:55px">섹터<div class="rz"></div></th>
<th data-c="2" data-t="s" style="width:70px">산업군<div class="rz"></div></th>
<th data-c="3" data-t="s" style="width:90px">방향<div class="rz"></div></th>
<th data-c="4" data-t="s" style="width:55px">유형<div class="rz"></div></th>
<th data-c="5" data-t="s" style="width:70px">축<div class="rz"></div></th>
<th data-c="6" data-t="n" class="r" style="width:40px">수량<div class="rz"></div></th>
<th data-c="7" data-t="n" class="r" style="width:50px">강도<div class="rz"></div></th>
<th data-c="8" data-t="n" class="r" style="width:65px" title="동일 종목 이전 시그널 대비 강도 변화율 — 가속도 측정">변화율%<div class="rz"></div></th>
<th data-c="9" data-t="s" style="width:85px">발생일시<div class="rz"></div></th>
<th data-c="10" data-t="s" style="width:85px">종료<div class="rz"></div></th>
<th data-c="11" data-t="s">사유<div class="rz"></div></th>
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
<div class="cal-fbar" id="cal-fbar"></div>
<div class="pb" style="max-height:540px" id="cb"></div>
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
// ── 날짜/시간 공통 포맷 (전체 통일) ────────────────────────────
// 모든 날짜: YYYY-MM-DD, 시각: HH:MM, 짧은날짜: MM-DD HH:MM
function fmtDate(s){if(!s)return '';return String(s).substring(0,10)}
function fmtTime(s){if(!s)return '';return String(s).substring(0,5)}
function fmtDateTime(iso){
  if(!iso||iso==='9999-12-31T23:59:59') return '';
  const d=new Date(iso);
  const mm=String(d.getMonth()+1).padStart(2,'0'),dd=String(d.getDate()).padStart(2,'0');
  const hh=String(d.getHours()).padStart(2,'0'),mi=String(d.getMinutes()).padStart(2,'0');
  return `${mm}-${dd} ${hh}:${mi}`;
}
function fmtDayOfWeek(dateStr){
  if(!dateStr) return '';
  const days=['일','월','화','수','목','금','토'];
  const d=new Date(dateStr+'T00:00:00Z');
  return days[d.getUTCDay()]||'';
}

// ── 경제 캘린더 ──────────────────────────────────────────────────
const CAL_CATS=[
  {k:'ALL',v:'전체'},{k:'FOMC',v:'FOMC'},{k:'FED_SPEAK',v:'연준연설'},
  {k:'PCE',v:'PCE'},{k:'CPI',v:'CPI'},{k:'NFP',v:'NFP'},
  {k:'EMPLOYMENT',v:'고용'},{k:'GDP',v:'GDP'},{k:'ISM',v:'ISM'},
  {k:'RETAIL',v:'소매판매'},{k:'TRUMP',v:'트럼프'},{k:'EARNINGS',v:'실적'},
];
const CAT_COLOR={
  FOMC:'#ef4444',FED_SPEAK:'#a855f7',PCE:'#3b82f6',CPI:'#3b82f6',
  NFP:'#22c55e',EMPLOYMENT:'#22c55e',GDP:'#f97316',ISM:'#f97316',
  RETAIL:'#f97316',TRUMP:'#78716c',EARNINGS:'#6366f1',OTHER:'#475569',
};
// 중요도 이모지 (사용자 요청)
const IMP_EMOJI=['','⚫','⚫⚫','🟡','🟠','🔴'];
const IMP_COL=['','#475569','#64748b','#eab308','#f97316','#ef4444'];

let calFilter='ALL';
let _calInitialized=false;

function initCalFilter(events){
  if(_calInitialized) return;
  _calInitialized=true;
  const bar=document.getElementById('cal-fbar');
  if(!bar) return;
  const present=new Set((events||[]).map(e=>e.category||'OTHER'));
  let h='';
  for(const {k,v} of CAL_CATS){
    if(k!=='ALL' && !present.has(k)) continue;
    const c=k==='ALL'?'#475569':(CAT_COLOR[k]||'#475569');
    h+=`<button class="cfbtn ${k===calFilter?'on':''}" data-k="${k}" onclick="setCalFilter('${k}')" style="--cc:${c}">${v}</button>`;
  }
  bar.innerHTML=h;
}

function setCalFilter(k){
  calFilter=k;
  document.querySelectorAll('.cfbtn').forEach(b=>{b.classList.toggle('on',b.dataset.k===k)});
  if(window._lastCalEvents) renderCalendar(window._lastCalEvents);
}

// 원형 시계 SVG (회전하는 분침, 진행 링)
function clockSvg(hu){
  const isP=hu<0;
  const color=isP?'#334155':hu<=1?'#ef4444':hu<=4?'#f97316':hu<=24?'#eab308':'#22c55e';
  const maxH=72, pct=isP?1:Math.max(0,Math.min(1,1-hu/maxH));
  const R=15,cx=18,cy=18,circ=2*Math.PI*R;
  const dash=circ*pct, gap=circ*(1-pct);

  // 분침 회전속도 — 급할수록 빠르게
  const spinDur=isP?'999s':hu<1?'4s':hu<4?'12s':hu<24?'30s':'60s';

  // 남은 시간 텍스트 (통일 형식)
  const lbl=isP?'지남':hu<1?Math.round(hu*60)+'분':hu<24?hu.toFixed(1)+'h':Math.floor(hu/24)+'일';

  return `<div class="clk-wrap">
<svg class="clk-svg" viewBox="0 0 36 36" width="36" height="36">
  <circle cx="${cx}" cy="${cy}" r="${R}" fill="#0d1322" stroke="#1e2d4a" stroke-width="2"/>
  <circle cx="${cx}" cy="${cy}" r="${R}" fill="none" stroke="${color}" stroke-width="2.5"
    stroke-dasharray="${dash.toFixed(1)} ${gap.toFixed(1)}"
    transform="rotate(-90 ${cx} ${cy})" style="transition:stroke-dashoffset .8s;opacity:.85"/>
  <circle cx="${cx}" cy="${cy}" r="2" fill="${color}"/>
  <line x1="${cx}" y1="${cy}" x2="${cx}" y2="${cy-9}" stroke="${color}" stroke-width="1.5" stroke-linecap="round" opacity=".7">
    <animateTransform attributeName="transform" type="rotate"
      from="0 ${cx} ${cy}" to="360 ${cx} ${cy}" dur="${spinDur}" repeatCount="indefinite"/>
  </line>
  <line x1="${cx}" y1="${cy}" x2="${cx}" y2="${cy-12}" stroke="${color}" stroke-width="1" stroke-linecap="round" opacity=".5">
    <animateTransform attributeName="transform" type="rotate"
      from="0 ${cx} ${cy}" to="360 ${cx} ${cy}" dur="3s" repeatCount="indefinite"/>
  </line>
</svg>
<span class="clk-label" style="color:${color}">${lbl}</span>
</div>`;
}

function fmtVal(v, unit){
  if(v===null||v===undefined) return '–';
  return (typeof v==='number'?v.toFixed(v%1===0?0:2):String(v))+(unit||'');
}

function renderCalendar(events){
  window._lastCalEvents=events;
  initCalFilter(events);
  const filtered=calFilter==='ALL'?events:events.filter(e=>(e.category||'OTHER')===calFilter);

  // 날짜별로 그룹핑
  const byDate={};
  for(const ev of filtered){
    const d=fmtDate(ev.date)||'미정';
    if(!byDate[d]) byDate[d]=[];
    byDate[d].push(ev);
  }
  const dateKeys=Object.keys(byDate).sort();

  let h='';
  for(const dk of dateKeys){
    const dow=fmtDayOfWeek(dk);
    h+=`<div class="cal-day-hdr"><div class="cdl"></div><div class="cdt">${dk} (${dow})</div><div class="cdl"></div></div>`;
    h+=`<div class="cal-grid">`;
    for(const ev of byDate[dk]){
      const ps=ev.status==='PAST';
      const hu=ev.hours_until||0;
      const urgent=!ps&&hu>=0&&hu<4;
      const imp=ev.impact_level||1;
      const impEm=IMP_EMOJI[imp]||'⚫';
      const impCol=IMP_COL[imp]||'#475569';
      const catCol=CAT_COLOR[ev.category||'OTHER']||'#475569';
      const cat=ev.category||'OTHER';

      // ACT / FCST / PREV 값
      const unit=ev.unit||'';
      const actV=ev.actual!==undefined&&ev.actual!==null;
      const fcsV=ev.estimate!==undefined&&ev.estimate!==null;
      const prvV=ev.prev!==undefined&&ev.prev!==null;

      let actCls='cv-val act-val';
      if(actV && fcsV){
        actCls=ev.actual>ev.estimate?'cv-val act-beat':'cv-val act-miss';
      }

      const valHtml=`<div class="cal-vals">
        <div class="cv-box"><div class="cv-lbl">ACT</div><div class="${actCls}">${actV?fmtVal(ev.actual,unit):'–'}</div></div>
        <div class="cv-box"><div class="cv-lbl">FCST</div><div class="cv-val fcst-val">${fcsV?fmtVal(ev.estimate,unit):'–'}</div></div>
        <div class="cv-box"><div class="cv-lbl">PREV</div><div class="cv-val prev-val">${prvV?fmtVal(ev.prev,unit):'–'}</div></div>
      </div>`;

      h+=`<div class="cal-card ${ps?'past':''} ${urgent?'urgent':''}">
<div class="cal-card-top">
  <span class="cal-imp" title="충격도 ${imp}">${impEm}</span>
  <span class="cal-cat" style="background:${catCol}22;color:${catCol}">${cat}</span>
</div>
<div class="cal-name">${ev.event_name||''}</div>
${valHtml}
<div class="cal-footer">
  <div class="cal-time-txt">${fmtDate(ev.date)} ${fmtTime(ev.time)} UTC</div>
  ${clockSvg(hu)}
</div>
</div>`;
    }
    h+='</div>';
  }
  document.getElementById('cb').innerHTML=h||'<div style="padding:20px;color:var(--td)">해당 유형의 이벤트 없음</div>';
}

let ss={};
const OPEN_SENTINEL='9999-12-31T23:59:59';
let sigFilter='active';   // active | today | 3day | all
let assetFilter='all';    // all | STOCK | OPTION
let sideFilter='all';     // all | BUY_PUT | BUY_CALL | SELL

function setSigFilter(f){
  sigFilter=f;
  document.querySelectorAll('.sfbtn').forEach(b=>b.classList.remove('on'));
  document.getElementById('sf_'+f).classList.add('on');
  if(window._lastSignals) renderSignals(window._lastSignals);
}

function setAssetFilter(f){
  assetFilter=f;
  document.querySelectorAll('.afbtn').forEach(b=>b.classList.remove('on'));
  document.getElementById('af_'+f).classList.add('on');
  if(window._lastSignals) renderSignals(window._lastSignals);
}

function setSideFilter(f){
  // 같은 버튼 다시 클릭 시 해제 (토글)
  sideFilter=(sideFilter===f)?'all':f;
  document.querySelectorAll('.emjbtn').forEach(b=>b.classList.remove('on'));
  document.getElementById('emj_'+(sideFilter==='all'?'all':sideFilter)).classList.add('on');
  if(window._lastSignals) renderSignals(window._lastSignals);
}

/* ----------------------------------------------------------------
 * sideBadgeClass — asset_class + side 조합으로 4색 뱃지 결정
 * Stock  BUY_CALL → Green  (.bsg)
 * Option BUY_PUT/BUY_CALL → Yellow (.bsy)
 * Stock  SELL     → Red    (.bsr)
 * Option SELL     → Blue   (.bsb)
 * ---------------------------------------------------------------- */
function sideBadgeClass(side, ac){
  const isBuy=side.includes('BUY');
  const isStk=(ac==='STOCK');
  if(isStk && isBuy)  return 'bsg';
  if(!isStk && isBuy) return 'bsy';
  if(isStk && !isBuy) return 'bsr';
  return 'bsb';
}

/* ----------------------------------------------------------------
 * buildRocMap — 동일(종목+방향) 시그널 간 강도 변화율% 사전 계산
 * RRG 4계절처럼 "가속도(기울기)"를 측정:
 *   처음 약하게 시작 → 점점 강해지면 → 확신 신호
 *   반대로 감속 → 전환 경고
 * ---------------------------------------------------------------- */
function buildRocMap(allSigs){
  const byKey={};
  // created_at 오름차순 정렬
  const sorted=[...allSigs].sort((a,b)=>(a.created_at||'')>(b.created_at||'')?1:-1);
  for(const s of sorted){
    const key=(s.symbol||'')+'|'+(s.side||'');
    (byKey[key]=byKey[key]||[]).push(s);
  }
  const rocMap={};
  for(const sigs of Object.values(byKey)){
    for(let i=0;i<sigs.length;i++){
      const s=sigs[i];
      const id=s.signal_id||(s.symbol+s.created_at);
      if(i===0){rocMap[id]=null;}  // 신규 — 이전 없음
      else{
        const ps=sigs[i-1].strength||0;
        const cs=s.strength||0;
        rocMap[id]=(ps===0)?null:((cs-ps)/Math.abs(ps)*100);
      }
    }
  }
  return rocMap;
}


function renderSignals(sigs){
  window._lastSignals=sigs;
  const now=new Date();
  // ROC 사전 계산 — 전체 sigs 기준 (필터 전)
  const rocMap=buildRocMap(sigs);

  const filtered=sigs.filter(s=>{
    const ea=s.expired_at||OPEN_SENTINEL;
    const ca=s.created_at||'';
    const isActive=(ea===OPEN_SENTINEL);
    // 기간 필터
    if(sigFilter==='active' && !isActive) return false;
    if(sigFilter==='today' && new Date(ca).toDateString()!==now.toDateString()) return false;
    if(sigFilter==='3day' && (now-new Date(ca))>3*86400*1000) return false;
    // 자산유형 필터
    if(assetFilter!=='all' && s.asset_class!==assetFilter) return false;
    // 방향 필터
    if(sideFilter!=='all' && s.side!==sideFilter) return false;
    return true;
  });

  // 발생일시 내림차순 (최신 상단)
  const rev=filtered.slice().sort((a,b)=>(b.created_at||'')>(a.created_at||'')?1:-1);
  let sh='';
  for(const s of rev){
    const axColor=AC[s.axis_id||'UNKNOWN']||'#4b5563';
    const ea=s.expired_at||OPEN_SENTINEL;
    const isActive=(ea===OPEN_SENTINEL);
    const expBadge=isActive
      ?'<span class="bact">활성중</span>'
      :`<span class="bexp" title="${ea}">${fmtDateTime(ea)}</span>`;
    const sect=s.sector||'';
    const ind=s.industry||'';

    // 4색 방향 뱃지
    const bc=sideBadgeClass(s.side||'',s.asset_class||'');

    // 변화율% — RRG 가속도 개념 (동일 종목+방향의 이전 시그널 대비)
    const sigId=s.signal_id||(s.symbol+s.created_at);
    const roc=rocMap[sigId];
    let rocCell,rocVal=0;
    if(roc===null||roc===undefined){
      rocCell='<td class="r roc-na" data-v="0">신규</td>';
    } else {
      rocVal=roc;
      const arrow=roc>0?'▲':'▼';
      const cls=roc>0?'roc-up':'roc-dn';
      rocCell=`<td class="r ${cls}" data-v="${roc.toFixed(1)}">${arrow}${Math.abs(roc).toFixed(1)}%</td>`;
    }

    sh+=`<tr>
<td class="sym">${s.symbol||''}</td>
<td><span class="sec">${sect}</span></td>
<td class="ind">${ind}</td>
<td><span class="b ${bc}">${s.side||''}</span></td>
<td style="font-size:11px;color:var(--td)">${s.asset_class||''}</td>
<td><span class="bx" style="background:${axColor}18;color:${axColor};font-size:11px">${s.axis_id||''}</span></td>
<td class="r" data-v="${s.qty||1}">${s.qty||1}</td>
<td class="r" data-v="${s.strength||0}">${(s.strength||0).toFixed(2)}</td>
${rocCell}
<td style="font-size:11px;color:var(--td)">${fmtDateTime(s.created_at||'')}</td>
<td>${expBadge}</td>
<td class="rsn" title="${(s.reason||'').replace(/"/g,'&quot;')}">${(s.reason||'').substring(0,50)}</td></tr>`;
  }
  document.getElementById('sb').innerHTML=sh||'<tr><td colspan="12" style="color:var(--td);padding:20px;text-align:center">시그널 대기중...</td></tr>';
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
const allSigs=d.all_signals||[];
renderSignals(allSigs);
document.getElementById('sc').textContent=allSigs.length+'건';

const cl=d.calendar_events||[];
document.getElementById('cc').textContent=cl.length+'건';
renderCalendar(cl);

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
