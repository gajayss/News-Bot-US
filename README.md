# News_Bot_US — 뉴스 기반 미국주식/옵션 자동매매 봇

뉴스 이벤트를 실시간 감시하여 감성 분석 후 미국주식(KIS REST)과 미국옵션(Kiwoom Bridge)으로 자동 주문을 내는 시스템입니다.

## 아키텍처

```
Finnhub / Sample News
    ↓ classify_news() — 감성분석 + 이벤트 분류
    ↓ SignalOrchestrator — 임계값 기반 신호 생성
    ├── stock_signals.json → KIS REST API → 미국주식 매매
    └── option_signals.json → Kiwoom Bridge → 미국옵션 매매
```

3개 독립 프로세스가 JSON 파일 버스(`runtime/interface/`)로 통신합니다:

| 프로세스 | 역할 |
|----------|------|
| `run_news_radar.py` | 뉴스 수집 → 분류 → 신호 생성 |
| `run_stock_consumer.py` | 주식 신호 소비 → KIS 주문 |
| `run_option_consumer.py` | 옵션 신호 소비 → Kiwoom 주문 |

## 안전장치 (Option B 보강)

- **파일 기반 중복 제거** — 프로세스 재시작 시에도 뉴스 중복 처리 방지
- **주문 속도 제한** — 분당 최대 주문 수 + 종목별 쿨다운
- **KIS 토큰 재시도** — 지수 백오프로 최대 3회 재시도
- **Telegram 알림** (선택) — 주문 실행/실패/속도제한 시 즉시 알림

## 빠른 시작

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 환경 설정
cp .env.example .env
# .env 파일에서 API 키 등 설정

# 3. 실행 (3개 프로세스 동시)
START_NEWS_BRIDGE.bat

# 또는 개별 실행
python run_news_radar.py
python run_stock_consumer.py
python run_option_consumer.py
```

## 설정

### 뉴스 소스

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `NEWS_SOURCE_MODE` | `sample` | `sample` 또는 `finnhub` |
| `FINNHUB_KEY` | | Finnhub API 키 |
| `NEWS_POLL_SEC` | `20` | 뉴스 폴링 간격(초) |
| `WATCHLIST` | `NVDA,TSLA,AAPL,QQQ` | 감시 종목 |

### 임계값

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `CONFIDENCE_THRESHOLD` | `0.55` | 최소 신뢰도 |
| `NEGATIVE_STOCK_THRESHOLD` | `-0.70` | 주식 SELL 감성 기준 |
| `POSITIVE_STOCK_THRESHOLD` | `0.70` | 주식 BUY 감성 기준 |
| `NEGATIVE_OPTION_THRESHOLD` | `-0.75` | 옵션 PUT 감성 기준 |
| `POSITIVE_OPTION_THRESHOLD` | `0.75` | 옵션 CALL 감성 기준 |

### 속도 제한

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MAX_ORDERS_PER_MINUTE` | `5` | 분당 최대 주문 수 |
| `SYMBOL_COOLDOWN_SEC` | `60` | 동일 종목 재주문 대기(초) |

### Telegram 알림 (선택)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `TELEGRAM_BOT_TOKEN` | | BotFather에서 발급받은 토큰 |
| `TELEGRAM_CHAT_ID` | | 알림 받을 채팅방 ID |

설정하지 않으면 알림 기능이 자동으로 비활성화됩니다.

### KIS (미국주식)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `KIS_APP_KEY` | | KIS API 앱키 |
| `KIS_APP_SECRET` | | KIS API 시크릿 |
| `KIS_CANO` | | 계좌번호 앞자리 |
| `KIS_SIMULATE` | `true` | `true`면 실제 주문 안 냄 |

### Kiwoom (미국옵션)

3가지 연동 방식 지원:

#### 1) payload-file 방식
```env
KIWOOM_COMMAND=python C:/trade/live_order_entry.py {payload_file}
```

#### 2) 직접 placeholder 방식
```env
KIWOOM_COMMAND=python C:/trade/live_order_entry.py --symbol {underlying} --side {side} --qty {qty} --right {option_right} --expiry {expiry_type} --signal-id {signal_id}
```

#### 3) adapter 방식 (권장)
```env
KIWOOM_COMMAND=python news_bridge/adapters/kiwoom_command_adapter.py --payload {payload_file} --target C:/trade/us_option_bot/live_order_entry.py --mode named-args --arg-map-file kiwoom_arg_map.sample.json
```

사용 가능한 placeholder:
`{payload_file}`, `{payload_json}`, `{payload_json_base64}`, `{underlying}`, `{symbol}`, `{side}`, `{qty}`, `{reason}`, `{signal_id}`, `{expiry_type}`, `{reference_price}`, `{option_right}`

실행 시 자동 주입되는 환경변수:
`KIWOOM_PAYLOAD_FILE`, `KIWOOM_PAYLOAD_JSON`, `KIWOOM_SIGNAL_ID`, `KIWOOM_UNDERLYING`, `KIWOOM_SYMBOL`, `KIWOOM_SIDE`, `KIWOOM_QTY`, `KIWOOM_REASON`, `KIWOOM_EXPIRY_TYPE`, `KIWOOM_REFERENCE_PRICE`, `KIWOOM_OPTION_RIGHT`

## 빠른 테스트

```env
KIWOOM_COMMAND=python kiwoom_entry_stub.py --symbol {underlying} --side {side} --qty {qty} --right {option_right} --expiry-type {expiry_type} --signal-id {signal_id}
```

```bash
python run_option_consumer.py
```

`runtime/interface/execution_reports.json`에 결과 확인.

## 프로젝트 구조

```
News_Bot_US/
├── config.py                          # 환경변수 기반 설정
├── run_news_radar.py                  # 뉴스 감시 + 신호 생성
├── run_stock_consumer.py              # 주식 신호 소비 + KIS 주문
├── run_option_consumer.py             # 옵션 신호 소비 + Kiwoom 주문
├── START_NEWS_BRIDGE.bat              # 3프로세스 동시 실행
├── kiwoom_entry_stub.py               # 옵션 테스트 스텁
├── kiwoom_arg_map.sample.json         # 인자 매핑 템플릿
├── news_bridge/
│   ├── models.py                      # NewsEvent, TradeSignal, ExecutionReport
│   ├── classifier.py                  # 뉴스 감성분석 + 이벤트 분류
│   ├── orchestrator.py                # 임계값 기반 신호 생성
│   ├── file_bus.py                    # JSON 파일 메시지 버스
│   ├── consumers.py                   # Offset 기반 신호 소비
│   ├── rate_limiter.py                # 주문 속도 제한
│   ├── alerter.py                     # Telegram 알림
│   ├── utils.py                       # 로깅 유틸
│   ├── sources/
│   │   ├── finnhub_source.py          # Finnhub API
│   │   └── sample_source.py           # 테스트 데이터
│   ├── brokers/
│   │   ├── kis_rest_stock.py          # KIS REST 주식 주문
│   │   ├── kiwoom_option_bridge.py    # Kiwoom command/webhook 브릿지
│   │   └── kiwoom_rest_template.py    # Kiwoom REST 템플릿
│   └── adapters/
│       └── kiwoom_command_adapter.py  # 기존 스크립트 어댑터
└── runtime/
    ├── interface/                     # JSON 메시지 파일
    └── logs/                          # 일별 로그
```
