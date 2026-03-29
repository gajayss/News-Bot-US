"""News Bot US — Web Dashboard.

5축 뉴스 분류 + 시그널 + 캘린더 + RRG 실시간 모니터링 UI.
http://127.0.0.1:6100
"""
from __future__ import annotations

import json
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, render_template_string, jsonify

import config
from news_bridge.axes import AXES
from news_bridge.event_calendar import EventCalendarState

# ---------------------------------------------------------------------------
# Regime 국면 엔진 (IBEX_US 로직 이식)
# VIX × 0.40 + CNN F&G × 0.30 + QQQ 5일 slope × 0.30
# ---------------------------------------------------------------------------
_CBOE_VIX_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json"
_CNN_FNG_URL  = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_CNN_FNG_HDR  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                 "Accept": "application/json, text/plain, */*"}

_regime_cache: dict = {
    "regime": "NORMAL", "direction": "LONG",
    "vix": 0.0, "vix_grade": "—", "vix_grade_color": "#64748b",
    "fng_score": 50.0, "fng_rating": "neutral",
    "market_slope": 0.0, "composite": 1.0,
    "last_updated": None, "stale": True,
}
_regime_lock   = threading.Lock()
_regime_ts     = 0.0
_REGIME_TTL    = 600  # 10분 캐시


def _fetch_regime() -> dict:
    global _regime_ts
    if time.time() - _regime_ts < _REGIME_TTL:
        return dict(_regime_cache)

    vix = 0.0
    fng_score, fng_rating = 50.0, "neutral"
    slope = 0.0

    # ── VIX (CBOE) ──────────────────────────────────────────────
    try:
        r = requests.get(_CBOE_VIX_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        vix = float(r.json().get("data", {}).get("current_price", 0))
    except Exception:
        pass

    # ── CNN Fear & Greed (이력 포함) ──────────────────────────────
    fng_prev_close = fng_prev_1w = fng_prev_1m = 50.0
    put_call_score = None
    try:
        r   = requests.get(_CNN_FNG_URL, headers=_CNN_FNG_HDR, timeout=8)
        raw = r.json()
        fg  = raw.get("fear_and_greed", {})
        fng_score      = float(fg.get("score", 50))
        fng_rating     = str(fg.get("rating", "neutral")).lower()
        fng_prev_close = float(fg.get("previous_close",   fg.get("score", 50)))
        fng_prev_1w    = float(fg.get("previous_1_week",  fg.get("score", 50)))
        fng_prev_1m    = float(fg.get("previous_1_month", fg.get("score", 50)))
        pc = raw.get("put_call_options", {})
        if pc.get("score") is not None:
            put_call_score = round(float(pc["score"]), 1)
    except Exception:
        pass

    # ── US10Y (Yahoo Finance ^TNX) ────────────────────────────────
    tnx_value = tnx_chg = 0.0
    tnx_warning = False
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?range=3d&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        cl = [c for c in r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c is not None]
        if cl:
            tnx_value   = round(cl[-1], 3)
            tnx_chg     = round(cl[-1] - cl[-2], 3) if len(cl) >= 2 else 0.0
            tnx_warning = tnx_value >= 4.2
    except Exception:
        pass

    # ── QQQ 5일 Slope + SPY/QQQ 1일 등락률 ─────────────────────
    spy_chg = qqq_chg = 0.0
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=10d&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        closes = [c for c in r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) >= 6:
            slope = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2)
        if len(closes) >= 2:
            qqq_chg = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
    except Exception:
        pass
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=3d&interval=1d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        cl = [c for c in r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c is not None]
        if len(cl) >= 2:
            spy_chg = round((cl[-1] - cl[-2]) / cl[-2] * 100, 2)
    except Exception:
        pass

    # ── 위험 점수 → Composite ────────────────────────────────────
    vix_s  = 0.0 if vix < 20 else 1.0 if vix < 25 else 2.0 if vix < 30 else 3.0
    fng_s  = (0.0 if fng_score >= 75 else 0.5 if fng_score >= 55
              else 1.0 if fng_score >= 45 else 2.0 if fng_score >= 25 else 3.0)
    slp_s  = (0.0 if slope >= 2.0 else 1.0 if slope >= 0.0
              else 2.0 if slope >= -2.0 else 3.0)
    composite = round(vix_s * 0.40 + fng_s * 0.30 + slp_s * 0.30, 3)

    # ── 국면 판정 ─────────────────────────────────────────────────
    if   composite < 0.6:  regime, direction = "BULL",       "LONG"
    elif composite < 1.2:  regime, direction = "NORMAL",     "LONG"
    elif composite < 1.8:  regime, direction = "CAUTION",    "MIXED"
    elif composite < 2.4:  regime, direction = "BEAR_WATCH", "SHORT"
    else:                  regime, direction = "BEAR",        "SHORT"

    # ── VIX 등급 ──────────────────────────────────────────────────
    if   vix < 15: vg, vc = "SAFE",    "#22c55e"
    elif vix < 18: vg, vc = "CALM",    "#86efac"
    elif vix < 21: vg, vc = "CAUTION", "#f59e0b"
    elif vix < 25: vg, vc = "WARN",    "#f97316"
    elif vix < 30: vg, vc = "DANGER",  "#ef4444"
    else:          vg, vc = "FEAR",    "#dc2626"

    with _regime_lock:
        _regime_cache.update({
            "regime": regime, "direction": direction,
            "vix": round(vix, 2), "vix_grade": vg, "vix_grade_color": vc,
            "fng_score": round(fng_score, 1), "fng_rating": fng_rating,
            "fng_prev_close": round(fng_prev_close, 1),
            "fng_prev_1w":    round(fng_prev_1w,    1),
            "fng_prev_1m":    round(fng_prev_1m,    1),
            "put_call_score": put_call_score,
            "tnx_value":    tnx_value,  "tnx_chg":  tnx_chg,  "tnx_warning": tnx_warning,
            "spy_chg":      spy_chg,    "qqq_chg":  qqq_chg,
            "market_slope": slope, "composite": composite,
            "last_updated": datetime.now(timezone.utc).strftime("%H:%M"),
            "stale": vix <= 0,
        })
    _regime_ts = time.time()
    return dict(_regime_cache)

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

.dot{width:8px;height:8px;border-radius:50%;background:#22c55e;display:inline-block;animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.3}}
/* Regime 게이지 위젯 */
.rgm-wrap{display:flex;flex-direction:column;gap:6px;padding:10px 14px;min-width:210px;border-left:1px solid var(--bdr)}
.rgm-badge{display:inline-flex;align-items:center;gap:7px;padding:5px 12px;border-radius:6px;font:700 14px/1 inherit;letter-spacing:.5px;border:1px solid transparent}
.rgm-badge.blink{animation:rgm-blink 1s infinite}
@keyframes rgm-blink{0%,100%{opacity:1}50%{opacity:.4}}
.rgm-dir{font-size:11px;color:var(--td);margin-top:2px}
.rgm-row{display:flex;align-items:center;gap:8px;font-size:11px}
.rgm-lbl{color:var(--td);min-width:38px}
.rgm-bar-wrap{flex:1;height:6px;border-radius:3px;background:#1e2736;overflow:hidden;position:relative}
.rgm-bar{height:100%;border-radius:3px;transition:width .6s ease}
.rgm-val{min-width:36px;text-align:right;font-weight:600;font-size:11px}
.rgm-ts{font-size:10px;color:#334155;text-align:right;margin-top:3px}
/* VIX 반원 게이지 */
.vix-svg{display:block;margin:0 auto}

.wrap{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:10px;margin:0}
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
/* 5색 방향 뱃지 — STOCK/OPTION 계열 완전 분리
   STOCK 계열 : Green(매수) / Red(매도) — 직관적 매매 색상
   OPTION 계열: Cyan(BUY_CALL) / Purple(BUY_PUT) / Sky(SELL) — 파란 계열 고유 영역 */
.bsg {background:#14532d;color:#4ade80;border:1px solid #16a34a}   /* Stock BUY    — 🟢 Green  */
.bsr {background:#7f1d1d;color:#fca5a5;border:1px solid #dc2626}   /* Stock SELL   — 🔴 Red    */
.bsyc{background:#164e63;color:#67e8f9;border:1px solid #0e7490}   /* Opt BUY_CALL — 🔵 Cyan   */
.bsyp{background:#3b0764;color:#d8b4fe;border:1px solid #7c3aed}   /* Opt BUY_PUT  — 🟣 Purple */
.bsb {background:#1e3a5f;color:#93c5fd;border:1px solid #2563eb}   /* Opt SELL     — 💙 Blue   */
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
.rrg-wrap{position:relative;width:100%;height:540px;overflow:hidden}
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
<div class="wrap">

<!-- Row 0: 3열 레이아웃
     Col1(230px): [공포&탐욕] 위 / [MARKET REGIME] 아래
     Col2(420px): [5축 뉴스 분류] 전체 높이
     Col3(flex:1): [경제 캘린더]  전체 높이
-->
<div class="fw" style="display:flex;gap:10px;align-items:stretch;min-height:300px">

  <!-- ① 좌측 열: 공포&탐욕(위) + MARKET REGIME(아래) — 230px 고정 -->
  <div style="display:flex;flex-direction:column;gap:10px;width:230px;flex-shrink:0">
    <div class="pnl" style="flex:1;min-height:0">
      <div class="ph" style="padding:7px 14px">
        <span style="font-size:12px;color:var(--td)">● 공포 &amp; 탐욕 지수</span>
        <small>DAILY</small>
      </div>
      <div id="fng-body" style="padding:8px 14px 10px">로딩중…</div>
    </div>
    <div class="pnl" style="flex:1;min-height:0">
      <div class="ph" style="padding:7px 14px">
        <span style="font-size:12px;color:#ef4444">● MARKET REGIME</span>
        <small id="regime-ts">—</small>
      </div>
      <div id="regime-body" style="padding:8px 14px 10px">로딩중…</div>
    </div>
  </div>

  <!-- ② 중앙 열: 5축 뉴스 분류 — 420px 고정, 전체 높이 -->
  <div class="pnl" style="width:420px;flex-shrink:0">
    <div class="ph">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <b>5축 뉴스 분류</b><small id="te"></small>
        <span style="font-size:11px;color:var(--td)"><span class="dot"></span> {{ source_mode }} · {{ watchlist_count }}종목</span>
        <span id="rt" style="font-size:11px;color:var(--td)"></span>
      </div>
    </div>
    <div class="desc" style="font-size:11px"><b>GOVERN</b> &gt; <b>FEDWALL</b> &gt; <b>ECONOMY</b> &gt; <b>CORPORATE</b> &gt; <b>THEME</b></div>
    <div class="axr" id="ab"></div>
    <div class="stats" id="stb"></div>
    <div class="src" id="srcb"></div>
  </div>

  <!-- ③ 우측 열: 경제 캘린더 — 나머지 공간 전부, 전체 높이 -->
  <div class="pnl" style="flex:1;min-width:0;display:flex;flex-direction:column">
    <div class="ph"><b>경제 캘린더</b><small id="cc"></small></div>
    <div class="cal-fbar" id="cal-fbar"></div>
    <div id="cb" style="flex:1;overflow-y:auto;max-height:none"></div>
  </div>

</div>

<!-- Row 1 Left: 매매 시그널 (아래 행) -->
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
<button id="emj_BUY"      onclick="setSideFilter('BUY')"      class="emjbtn">🟢 BUY <span style="font-size:10px;color:var(--td)">주식매수·상승</span></button>
<button id="emj_SELL"     onclick="setSideFilter('SELL')"     class="emjbtn">🔴 SELL <span style="font-size:10px;color:var(--td)">주식매도·하락</span></button>
<button id="emj_BUY_CALL" onclick="setSideFilter('BUY_CALL')" class="emjbtn">📈 BUY_CALL <span style="font-size:10px;color:var(--td)">콜매수·상승베팅</span></button>
<button id="emj_BUY_PUT"  onclick="setSideFilter('BUY_PUT')"  class="emjbtn">📉 BUY_PUT <span style="font-size:10px;color:var(--td)">풋매수·하락베팅</span></button>
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

<!-- Row 2 Right: 진입 제약 -->
<div class="pnl">
<div class="ph"><b>진입 제약</b></div>
<div class="desc">FOMC/NFP 등 고충격 이벤트 전 자동 차단.</div>
<div id="xb"></div>
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
  const isStk=(ac==='STOCK');
  if(side==='BUY')      return 'bsg';   // Stock 매수 — Green
  if(side==='SELL' && isStk) return 'bsr';  // Stock 매도 — Red
  if(side==='BUY_CALL') return 'bsyc';  // Option 콜매수 — Yellow
  if(side==='BUY_PUT')  return 'bsyp';  // Option 풋매수 — Orange
  return 'bsb';                          // Option SELL — Blue
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


/* ================================================================
 * IBEX_US 이식 — 공포&탐욕 게이지 + MARKET REGIME 패널
 * ================================================================ */
const REGIME_CFG={
  BULL:       {color:'#22c55e',bg:'#14532d',label:'BULL'},
  NORMAL:     {color:'#3b82f6',bg:'#1e3a5f',label:'NORMAL'},
  CAUTION:    {color:'#f59e0b',bg:'#713f12',label:'CAUTION'},
  BEAR_WATCH: {color:'#f97316',bg:'#431407',label:'BEAR_WATCH'},
  BEAR:       {color:'#ef4444',bg:'#450a0a',label:'BEAR'},
};
const DIR_CFG={
  LONG: {color:'#22c55e',icon:'▲',label:'LONG'},
  MIXED:{color:'#f59e0b',icon:'↕',label:'MIXED'},
  SHORT:{color:'#ef4444',icon:'▼',label:'SHORT'},
};

/* F&G 반원 스피드계 게이지 SVG (0~100) */
function fngGaugeSvg(score){
  const W=220,H=128,cx=110,cy=115,R=92,rW=15;
  const cl=Math.max(0,Math.min(100,score||0));
  // 0→왼쪽(-π), 100→오른쪽(0)
  function s2a(s){return(s/100)*Math.PI-Math.PI;}
  function arcP(s1,s2){
    const a1=s2a(s1),a2=s2a(s2);
    const x1=cx+R*Math.cos(a1),y1=cy+R*Math.sin(a1);
    const x2=cx+R*Math.cos(a2),y2=cy+R*Math.sin(a2);
    return `M${x1},${y1} A${R},${R} 0 0,1 ${x2},${y2}`;
  }
  const na=s2a(cl);
  const nx=cx+(R-10)*Math.cos(na), ny=cy+(R-10)*Math.sin(na);
  const nc=cl<25?'#ef4444':cl<45?'#f97316':cl<55?'#94a3b8':cl<75?'#22c55e':'#10b981';
  const lbl=cl<25?'극도공포':cl<45?'공포':cl<55?'중립':cl<75?'탐욕':'극도탐욕';
  const segs=[
    [0,25,'#ef4444'],[25,45,'#f97316'],[45,55,'#94a3b8'],[55,75,'#22c55e'],[75,100,'#10b981']
  ];
  const segH=segs.map(([s1,s2,c])=>`<path d="${arcP(s1,s2)}" stroke="${c}" stroke-width="${rW}" fill="none" opacity="0.85"/>`).join('');
  // 눈금 라벨
  const ticks=[{v:0,t:'극공'},{v:25,t:'공포'},{v:50,t:'중립'},{v:75,t:'탐욕'},{v:100,t:'극탐'}];
  const tickH=ticks.map(({v,t})=>{
    const a=s2a(v); const r2=R+16;
    return `<text x="${cx+r2*Math.cos(a)}" y="${cy+r2*Math.sin(a)+3}" text-anchor="middle" fill="#334155" font-size="9">${t}</text>`;
  }).join('');
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="display:block;margin:0 auto">
  <path d="${arcP(0,100)}" stroke="#1a2030" stroke-width="${rW+3}" fill="none"/>
  ${segH}
  ${tickH}
  <line x1="${cx}" y1="${cy}" x2="${nx}" y2="${ny}" stroke="${nc}" stroke-width="3.5" stroke-linecap="round"/>
  <circle cx="${cx}" cy="${cy}" r="7" fill="${nc}" opacity="0.9"/>
  <text x="${cx}" y="${cy-22}" text-anchor="middle" fill="${nc}" font-size="30" font-weight="800" font-family="monospace">${cl>0?Math.round(cl):'—'}</text>
  <text x="${cx}" y="${cy-6}" text-anchor="middle" fill="${nc}" font-size="12" font-weight="600">${lbl}</text>
</svg>`;
}

function fngHistCell(score,label){
  const c=score<25?'#ef4444':score<45?'#f97316':score<55?'#94a3b8':score<75?'#22c55e':'#10b981';
  const t=score<25?'극도공포':score<45?'공포':score<55?'중립':score<75?'탐욕':'극도탐욕';
  return `<div style="text-align:center;flex:1">
    <div style="font:700 18px/1 monospace;color:${c}">${Math.round(score)}</div>
    <div style="font-size:9px;color:${c};margin-top:2px">${t}</div>
    <div style="font-size:10px;color:#475569;margin-top:1px">${label}</div>
  </div>`;
}

/* 미니 막대 (F&G/P-C 소형 표시) */
function miniBars(score){
  const c=score<25?'#ef4444':score<45?'#f97316':score<55?'#94a3b8':score<75?'#22c55e':'#10b981';
  return `<div style="display:inline-flex;gap:2px;vertical-align:middle;margin-left:6px">${
    [20,40,60,80,100].map(t=>`<div style="width:4px;height:10px;border-radius:1px;background:${score>=t?c:'#1e2736'}"></div>`).join('')
  }</div>`;
}

/* chg 포맷 */
function chgFmt(v){
  const c=v>0?'#22c55e':v<0?'#ef4444':'#94a3b8';
  const s=(v>0?'+':'')+v.toFixed(2)+'%';
  return `<span style="color:${c};font-weight:700">${s}</span>`;
}

/* ---- 공포&탐욕 패널 렌더 ---- */
function renderFngPanel(rm){
  const s=rm.fng_score||50, pc=rm.fng_prev_close||50, pw=rm.fng_prev_1w||50, pm=rm.fng_prev_1m||50;
  document.getElementById('fng-body').innerHTML=`
  ${fngGaugeSvg(s)}
  <div style="display:flex;gap:0;margin-top:8px;border-top:1px solid #1e2736;padding-top:8px">
    ${fngHistCell(s,'현재')}
    <div style="width:1px;background:#1e2736"></div>
    ${fngHistCell(pc,'D-1')}
    <div style="width:1px;background:#1e2736"></div>
    ${fngHistCell(pw,'-1W')}
    <div style="width:1px;background:#1e2736"></div>
    ${fngHistCell(pm,'-1M')}
  </div>`;
}

/* ---- MARKET REGIME 패널 렌더 ---- */
function renderRegimePanel(rm){
  const rc=REGIME_CFG[rm.regime]||REGIME_CFG.NORMAL;
  const dc=DIR_CFG[rm.direction]||DIR_CFG.LONG;
  const blink=(rm.regime==='BEAR'||rm.regime==='BEAR_WATCH')?'animation:rgm-blink 1s infinite':'';
  const compPct=Math.min((rm.composite||1)/3*100,100);
  const e1=Math.round((rm.engine1_ratio||0.5)*100), e2=100-e1;

  // regime 행 목록
  const rows=[
    ['BULL','LONG','100%','0%','80%','20%'],
    ['NORMAL','LONG','70%','0%','60%','40%'],
    ['CAUTION','MIXED','30%','30%','40%','60%'],
    ['BEAR_WATCH','SHORT','0%','60%','20%','80%'],
    ['BEAR','SHORT','0%','100%','0%','100%'],
  ];
  const tableRows=rows.map(([k,dir,lo,sh,e1r,e2r])=>{
    const cfg2=REGIME_CFG[k]; const act=k===rm.regime;
    const dc2=dir==='LONG'?'#22c55e':dir==='SHORT'?'#ef4444':'#f59e0b';
    const style=act?`font-weight:700;color:${cfg2.color}`:'color:#2d3d52';
    return `<div style="display:grid;grid-template-columns:90px 52px 36px 36px 36px 36px;gap:2px;padding:2px 0;border-bottom:1px solid #0c0f14;align-items:center">
      <span style="font-size:11px;font-family:monospace;${style}${act?';padding:1px 5px;border-radius:3px;background:'+cfg2.bg:''}">${k}</span>
      <span style="font-size:10px;font-family:monospace;color:${act?dc2:'#2d3d52'}">${dir}</span>
      <span style="font-size:10px;text-align:right;color:${act?'#22c55e':'#2d3d52'}">${lo}</span>
      <span style="font-size:10px;text-align:right;color:${act?'#ef4444':'#2d3d52'}">${sh}</span>
      <span style="font-size:10px;text-align:right;color:${act?'#22c55e':'#2d3d52'}">${e1r}</span>
      <span style="font-size:10px;text-align:right;color:${act?'#f59e0b':'#2d3d52'}">${e2r}</span>
    </div>`;
  }).join('');

  const tnxWarn=rm.tnx_warning?`<span style="color:#f59e0b;font-size:10px"> ≥4.2% ⚠</span>`:'';
  const tnxChgStr=rm.tnx_chg>0?`<span style="color:#ef4444;font-size:10px"> +${rm.tnx_chg.toFixed(3)}</span>`:
                  rm.tnx_chg<0?`<span style="color:#22c55e;font-size:10px"> ${rm.tnx_chg.toFixed(3)}</span>`:'';

  const row=(lbl,val,extra='')=>`
    <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid #0d1117">
      <span style="font-size:11px;color:#475569">${lbl}</span>
      <span style="font-size:13px;font-weight:700;font-family:monospace;color:var(--tw)">${val}${extra}</span>
    </div>`;

  document.getElementById('regime-ts').textContent=rm.last_updated||'—';
  document.getElementById('regime-body').innerHTML=`
  <div style="display:flex;gap:10px">
    <!-- 좌: 데이터 행 -->
    <div style="flex:1;min-width:0">
      ${row('VIX',`<span style="background:${rm.vix_grade_color||'#64748b'}22;color:${rm.vix_grade_color||'#64748b'};border:1px solid ${rm.vix_grade_color||'#64748b'}44;border-radius:3px;padding:1px 6px;font-size:11px">${rm.vix_grade||'—'}</span> ${(rm.vix||0).toFixed(1)}`)}
      ${row('US10Y',(rm.tnx_value||0).toFixed(3)+'%',tnxWarn+tnxChgStr)}
      ${row('F&G',`${Math.round(rm.fng_score||50)}${miniBars(rm.fng_score||50)}`)}
      ${rm.put_call_score!=null?row('P/C Ratio',`${rm.put_call_score}${miniBars(rm.put_call_score)}`,''):''}
      ${row('Slope',chgFmt(rm.market_slope||0)+` <span style="font-size:10px;color:#475569">5일</span>`)}
      ${row('Risk',`${(rm.composite||1).toFixed(2)} <span style="font-size:10px;color:#475569">/ 3.0</span>`)}
      ${row('SPY',chgFmt(rm.spy_chg||0))}
      ${row('QQQ',chgFmt(rm.qqq_chg||0))}
      <!-- E1:E2 바 -->
      <div style="margin-top:5px">
        <div style="display:flex;justify-content:space-between;font-size:10px;margin-bottom:3px">
          <span style="color:#22c55e">E1 ${e1}%</span><span style="color:#f59e0b">E2 ${e2}%</span>
        </div>
        <div style="height:6px;border-radius:3px;background:#0c0f14;overflow:hidden;border:1px solid #1e2736">
          <div style="height:100%;background:linear-gradient(to right,#22c55e ${e1}%,#f59e0b ${e1}%);transition:width .5s"></div>
        </div>
      </div>
    </div>
    <!-- 우: 5단계 국면 인디케이터 -->
    <div style="display:flex;flex-direction:column;gap:3px;min-width:80px">
      ${rows.map(([k])=>{
        const cfg2=REGIME_CFG[k]; const act=k===rm.regime;
        const blk2=act&&(k==='BEAR'||k==='BEAR_WATCH')?';animation:rgm-blink 1s infinite':'';
        return `<div style="padding:4px 8px;border-radius:4px;font-size:11px;font-family:monospace;font-weight:${act?700:400};
          background:${act?cfg2.bg:'#0d1117'};color:${act?cfg2.color:'#2d3d52'};
          border:1px solid ${act?cfg2.color+'44':'#1a2030'}${blk2}">
          ${cfg2.label}<br><span style="font-size:9px;color:${act?cfg2.color:'#1e2736'}">E1:${rows.find(r=>r[0]===k)[4]}</span>
        </div>`;
      }).join('')}
    </div>
  </div>
  <!-- Regime → Direction 맵 -->
  <div style="margin-top:8px;border-top:1px solid #1e2736;padding-top:6px">
    <div style="font-size:10px;color:#334155;margin-bottom:4px;letter-spacing:.05em">REGIME → DIRECTION MAP</div>
    ${tableRows}
    <div style="display:grid;grid-template-columns:90px 52px 36px 36px 36px 36px;gap:2px;margin-top:3px">
      ${['','Dir','Long','Short','E1','E2'].map(h=>`<span style="font-size:8px;color:#1e2736;text-align:${h?'right':'left'}">${h}</span>`).join('')}
    </div>
  </div>`;
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

  // ── TOP 선별 ─────────────────────────────────────────────────
  // 전체 RS 점수(last.x) 기준 TOP2 → 별(★) 마커, 최대 강조
  const allRanked = (trails||[])
    .filter(t=>t.trail&&t.trail.length)
    .map(t=>{
      const last=t.trail[t.trail.length-1];
      let spd=0;
      if(t.trail.length>=2){
        const lb=Math.min(4,t.trail.length-1);
        const prev=t.trail[t.trail.length-1-lb];
        spd=Math.sqrt(Math.pow(last.x-prev.x,2)+Math.pow(last.y-prev.y,2))/lb;
      }
      return {ticker:t.ticker, x:last.x, y:last.y, cnt:last.news_count||0, spd, q:(last.quadrant||'').toLowerCase()};
    })
    .sort((a,b)=>(b.x-100)-(a.x-100)||(b.cnt-a.cnt)); // RS-Ratio 내림차순

  // 전체 TOP2 → 별
  const top2Set = new Set(allRanked.slice(0,2).map(r=>r.ticker));

  // 분면별 TOP2 → 강조 표시
  const quadGroups={leading:[],improving:[],weakening:[],lagging:[]};
  allRanked.forEach(r=>{if(quadGroups[r.q])quadGroups[r.q].push(r);});
  const quadTopSet = new Set();
  Object.values(quadGroups).forEach(g=>g.slice(0,2).forEach(r=>quadTopSet.add(r.ticker)));

  // 표시 범위: top2 + 분면별 top2 + 나머지 뉴스 있는 종목 (최대 20)
  const visibleTickers = new Set([...top2Set,...quadTopSet,...allRanked.slice(0,20).map(r=>r.ticker)]);

  // 렌더 순서 — top2 마지막(맨 위에 표시)
  const orderedTrails = [
    ...(trails||[]).filter(t=>t.trail&&t.trail.length&&visibleTickers.has(t.ticker)&&!top2Set.has(t.ticker)),
    ...(trails||[]).filter(t=>top2Set.has(t.ticker)&&t.trail&&t.trail.length),
  ];

  const traces = [];

  orderedTrails.forEach(({ticker, trail}) => {
    if (!trail || !trail.length) return;
    const last = trail[trail.length - 1];
    const q = (last.quadrant || '').toLowerCase();
    const color = quadColors[q] || '#64748b';
    const nc = last.news_count || 0;
    const hoverTxt = `${ticker}<br>뉴스 ${nc}건<br>감성: ${(last.x - 100).toFixed(1)}<br>모멘텀: ${(last.y - 100).toFixed(1)}`;

    const isTop2      = top2Set.has(ticker);      // 전체 TOP2 → ★ 별
    const isQuadTop   = quadTopSet.has(ticker);    // 분면 TOP2 → 강조
    const n = trail.length;
    const splitAt = Math.max(0, n - RECENT_N);
    const oldPart = trail.slice(0, splitAt);
    const nowPart = trail.slice(splitAt, n - 1);
    const px = p => sxf(p.x);
    const py = p => syf(p.y);

    // 트레일 선 — TOP2는 밝고 굵게
    if (oldPart.length > 1) {
      traces.push({
        x: oldPart.map(px), y: oldPart.map(py),
        mode:'lines', type:'scatter',
        line:{color, width:isTop2?1.5:0.8, dash:'dot'},
        opacity: isTop2?0.35:isQuadTop?0.20:0.08,
        showlegend:false, hoverinfo:'skip',
      });
    }
    if (nowPart.length > 0) {
      const recentLine=[oldPart.length?oldPart[oldPart.length-1]:null,...nowPart].filter(Boolean);
      traces.push({
        x:recentLine.map(px), y:recentLine.map(py),
        mode:'lines', type:'scatter',
        line:{color:isTop2?'#fbbf24':color, width:isTop2?2.2:isQuadTop?1.5:0.8, dash:'dot'},
        opacity:isTop2?0.65:isQuadTop?0.45:0.18,
        showlegend:false, hoverinfo:'skip',
      });
      // 트레일 점
      traces.push({
        x:nowPart.map(px), y:nowPart.map(py),
        mode:'markers', type:'scatter',
        marker:{size:isTop2?6:isQuadTop?4:2, color:isTop2?'#fbbf24':color, opacity:isTop2?0.75:isQuadTop?0.55:0.20},
        showlegend:false, hoverinfo:'skip',
      });
    }

    // 현재 위치 마커
    traces.push({
      x:[sxf(last.x)], y:[syf(last.y)],
      mode:'markers+text', type:'scatter',
      name:ticker,
      hovertext:[hoverTxt],
      hovertemplate:'%{hovertext}<extra></extra>',
      text:[isTop2?`★ ${ticker}`:ticker],
      textposition:'top center',
      textfont:{
        size:  isTop2?15:isQuadTop?13:11,
        color: isTop2?'#fde68a':isQuadTop?'#f1f5f9':'#94a3b8',
        family:'Segoe UI, monospace',
      },
      marker:{
        size:   isTop2?22:isQuadTop?14:9,
        symbol: isTop2?'star':'circle',
        color:  isTop2?'#fbbf24':color,
        opacity:isTop2?1.0:isQuadTop?0.95:0.75,
        line:{
          color: isTop2?'rgba(253,230,138,.8)':isQuadTop?'rgba(255,255,255,.5)':'rgba(255,255,255,.2)',
          width: isTop2?2.5:isQuadTop?1.8:1,
        },
      },
      showlegend:false,
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
    const isT2  = top2Set.has(ticker);
    const isQT  = quadTopSet.has(ticker);

    const tipScale = Math.min(speed * 3.0, 2.5);
    const tip_raw_x = last.x + raw_dx * tipScale;
    const tip_raw_y = last.y + raw_dy * tipScale;

    arrowAnnotations.push({
      x: sxf(tip_raw_x), y: syf(tip_raw_y),
      ax: sxf(last.x),   ay: syf(last.y),
      axref:'x', ayref:'y', xref:'x', yref:'y',
      text:'', showarrow:true,
      arrowhead:3,
      arrowsize:  isT2?1.4:isQT?1.1:0.8,
      arrowwidth: isT2?3.0:isQT?2.0:1.0,
      arrowcolor: isT2?'#fbbf24':color,
      opacity:    isT2?1.0:isQT?0.85:0.45,
    });
  });

  const C = 100;
  const layout = {
    paper_bgcolor:'transparent', plot_bgcolor:'#070b12',
    font:{family:'Segoe UI, monospace', size:11, color:'#64748b'},
    margin:{l:12, r:12, t:28, b:28},
    xaxis:{
      title:{text:'RS-Ratio  →', font:{size:11,color:'#334155'}},
      range:[xlo,xhi], showticklabels:false,
      gridcolor:'rgba(255,255,255,0.03)', zerolinecolor:'transparent',
    },
    yaxis:{
      title:{text:'RS-Momentum  ↑', font:{size:11,color:'#334155'}},
      range:[ylo,yhi], showticklabels:false,
      gridcolor:'rgba(255,255,255,0.03)', zerolinecolor:'transparent',
    },
    shapes:[
      // 4분면 배경 — IBEX 동일 색상
      {type:'rect',x0:C,x1:xhi,y0:C,y1:yhi, xref:'x',yref:'y',layer:'below',fillcolor:'rgba(34,197,94,0.13)',  line:{width:0}},
      {type:'rect',x0:xlo,x1:C, y0:C,y1:yhi, xref:'x',yref:'y',layer:'below',fillcolor:'rgba(250,204,21,0.10)', line:{width:0}},
      {type:'rect',x0:C,x1:xhi,y0:ylo,y1:C,  xref:'x',yref:'y',layer:'below',fillcolor:'rgba(59,130,246,0.10)', line:{width:0}},
      {type:'rect',x0:xlo,x1:C, y0:ylo,y1:C,  xref:'x',yref:'y',layer:'below',fillcolor:'rgba(239,68,68,0.15)',  line:{width:0}},
      // 중심 십자선
      {type:'line',x0:C,x1:C,y0:ylo,y1:yhi, xref:'x',yref:'y',line:{color:'#2d3f52',width:1.5,dash:'dot'}},
      {type:'line',x0:xlo,x1:xhi,y0:C,y1:C, xref:'x',yref:'y',line:{color:'#2d3f52',width:1.5,dash:'dot'}},
    ],
    annotations:[
      // 분면 라벨 — 크고 선명하게
      {x:xhi,y:yhi, xref:'x',yref:'y',text:'LEADING',   showarrow:false,font:{size:14,color:'rgba(74,222,128,0.55)',family:'Segoe UI'},xanchor:'right',yanchor:'top'},
      {x:xlo,y:yhi, xref:'x',yref:'y',text:'IMPROVING', showarrow:false,font:{size:14,color:'rgba(250,204,21,0.55)',family:'Segoe UI'},xanchor:'left', yanchor:'top'},
      {x:xhi,y:ylo, xref:'x',yref:'y',text:'WEAKENING', showarrow:false,font:{size:14,color:'rgba(59,130,246,0.55)',family:'Segoe UI'},xanchor:'right',yanchor:'bottom'},
      {x:xlo,y:ylo, xref:'x',yref:'y',text:'LAGGING',   showarrow:false,font:{size:14,color:'rgba(239,68,68,0.55)', family:'Segoe UI'},xanchor:'left', yanchor:'bottom'},
      // 범례 (우상단 paper 좌표)
      {xref:'paper',yref:'paper',x:1,y:1.04,xanchor:'right',yanchor:'bottom',showarrow:false,
       text:'● <span style="color:#4ade80">Lead</span>  ● <span style="color:#facc15">Impv</span>  ● <span style="color:#3b82f6">Weak</span>  ● <span style="color:#ef4444">Lag</span>  ★ TOP2',
       font:{size:11,color:'#64748b'},align:'right'},
      // 부제목
      {xref:'paper',yref:'paper',x:0,y:1.04,xanchor:'left',yanchor:'bottom',showarrow:false,
       text:'bubble=RS-score · arrow=momentum',font:{size:10,color:'#334155'}},
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

// IBEX 패널 렌더
if(d.regime){renderFngPanel(d.regime);renderRegimePanel(d.regime);}

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

    # Regime 국면 (10분 캐시)
    try:
        regime_data = _fetch_regime()
    except Exception:
        regime_data = dict(_regime_cache)

    return jsonify({
        "news_events": news_events[-100:],
        "stock_signals": stock_signals[-50:],
        "option_signals": option_signals[-50:],
        "all_signals": all_signals,           # 선분이력 기반 전체 시그널 (중복 제거)
        "axis_counts": axis_counts,
        "calendar_events": cal_events,
        "constraint": constraint,
        "rrg_trails": rrg_trails,
        "regime": regime_data,
    })


if __name__ == "__main__":
    print("=" * 60)
    print(f"  News Bot US — Dashboard")
    print(f"  http://127.0.0.1:{DASHBOARD_PORT}")
    print("=" * 60)
    app.run(host="127.0.0.1", port=DASHBOARD_PORT, debug=False)
