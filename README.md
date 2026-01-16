# ScalpingBot v2.4

한국 주식 자동매매 봇 - AI 기반 단타 매매 시스템

## 🚀 주요 기능

- **하이브리드 판단 시스템**: 규칙 기반 점수 + Qwen3 AI 판단
- **6대 핵심 지표**: CCI, 등락률, 이동평균 이격, 연속 양봉, 거래량, 캔들 품질
- **실시간 시장 모니터링**: 코스피/코스닥 지수 기반 모드 전환
- **자동 리스크 관리**: 손절(-1.5%), 등급별 익절, 트레일링 스탑

## 📋 시스템 요구사항

- Python 3.10+
- 한국투자증권 API 계정
- Ollama + Qwen3:8b (AI 엔진용)
- 4GB+ RAM

## 🔧 설치

```bash
# 1. 클론
git clone https://github.com/yourusername/ScalpingBot.git
cd ScalpingBot

# 2. 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 의존성 설치
pip install -r requirements.txt

# 4. 설정 파일 준비
cp config/secrets.yaml.example config/secrets.yaml
# secrets.yaml에 한투 API 키 입력
```

## ⚙️ 설정

### config/secrets.yaml

```yaml
kis:
  app_key: "YOUR_APP_KEY"
  app_secret: "YOUR_APP_SECRET"
  account_number: "12345678"
  account_code: "01"
  environment: "V"  # V: 모의, P: 실전

discord:
  webhook_url: "https://discord.com/api/webhooks/..."
```

### config/config.yaml

주요 설정 항목:
- `mode`: 운영 모드 (LIVE_DATA_ONLY, LIVE_MICRO, LIVE)
- `ai.model`: AI 모델 (qwen3:8b)
- `strategy.min_score`: 최소 진입 점수

## 🎮 실행

```bash
# 기본 실행 (설정 파일의 mode 사용)
python main.py

# 모드 지정
python main.py --mode LIVE_MICRO

# 드라이런 (실제 주문 없이 시뮬레이션)
python main.py --dry-run

# 디버그 모드
python main.py --debug
```

## 📊 운영 모드

| 모드 | 설명 | 주문 금액 | 포지션 수 |
|------|------|----------|----------|
| `LIVE_DATA_ONLY` | 데이터 수집만, 주문 없음 | 0 | 0 |
| `LIVE_MICRO` | 소액 테스트 | 5만원 | 3개 |
| `LIVE` | 실전 매매 | 50만원 | 5개 |

## 📈 매매 전략

### 진입 조건

```
정상 모드 (코스피 MA20 위):
  1. 규칙 점수 ≥ 65점
  2. AI 판단 = BUY
  3. AI 신뢰도 ≥ 70%
  4. 현재가 ≤ AI분석가 + 1.5%
  5. 쿨타임 10분 경과

보수적 모드 (코스피 MA20 아래):
  1. 규칙 점수 ≥ 75점
  2. AI 판단 = BUY
  3. AI 신뢰도 ≥ 85%
  4. 현재가 ≤ AI분석가 + 1%
```

### 청산 조건

```
1순위: 손절 → -1.5% 도달 시 즉시
2순위: 익절 → 등급별 목표가 도달
3순위: 트레일링 → 고점 대비 -0.5% 하락
4순위: 시간청산 → 14:50 전량 청산
```

### 등급별 익절 목표

| 등급 | 점수 | 익절 목표 | 트레일링 |
|------|------|----------|----------|
| S | 90+ | +1.5% | -0.5% |
| A | 80-89 | +1.2% | -0.4% |
| B | 70-79 | +1.0% | -0.3% |
| C | 60-69 | +0.8% | -0.3% |

## 🛡️ 안전장치

### 자동 비상 정지

- 코스피 당일 -2% 이상 급락
- 일일 손실 한도 초과 (-3%)
- 연속 손절 5회
- API 에러 3회 연속

### 비상 정지 스크립트

```bash
# 전량 청산 + 시스템 종료
python emergency_stop.py

# 미체결만 취소
python emergency_stop.py --cancel

# 확인 없이 즉시 실행
python emergency_stop.py --force
```

## 📁 프로젝트 구조

```
ScalpingBot/
├── main.py                 # 메인 진입점
├── emergency_stop.py       # 비상 정지 스크립트
├── config/
│   ├── config.yaml         # 메인 설정
│   └── secrets.yaml        # API 키 (gitignore)
├── scalping/
│   ├── ai/                 # AI 엔진
│   │   ├── ai_engine.py
│   │   └── learning_store.py
│   ├── strategy/           # 매매 전략
│   │   ├── indicators.py
│   │   └── score_engine.py
│   ├── execution/          # 주문 실행
│   │   ├── broker.py
│   │   ├── position_manager.py
│   │   ├── cooldown_tracker.py
│   │   └── price_validator.py
│   ├── data/               # 데이터 수집
│   │   ├── ohlcv_loader.py
│   │   ├── market_monitor.py
│   │   ├── universe_filter.py
│   │   ├── realtime_feed.py
│   │   └── stock_mapper.py
│   ├── engine/             # 메인 엔진
│   │   ├── trading_engine.py
│   │   ├── state_machine.py
│   │   └── scheduler.py
│   ├── storage/            # 데이터 저장
│   │   ├── database.py
│   │   ├── models.py
│   │   └── repository.py
│   ├── safety/             # 안전장치
│   │   ├── kill_switch.py
│   │   └── circuit_breaker.py
│   └── notification/       # 알림
│       └── discord_bot.py
├── db/                     # SQLite DB
├── logs/                   # 로그 파일
└── tests/                  # 테스트
```

## 📝 로그

로그 파일 위치: `logs/scalping.log`

```bash
# 실시간 로그 모니터링
tail -f logs/scalping.log

# 에러만 필터링
grep ERROR logs/scalping.log
```

## 🔍 모니터링

### Discord 알림

- 매수/매도 체결 알림
- 일일 리포트 (15:30)
- 비상 상황 경고

### 주요 지표

- 일일 승률
- 총 손익
- 평균 수익/손실
- 연속 손절 횟수

## ⚠️ 주의사항

1. **투자 원금 손실 가능**: 자동매매로 인한 손실에 대한 책임은 사용자에게 있습니다.
2. **모의투자 충분히 테스트**: 실전 전 반드시 `LIVE_DATA_ONLY` → `LIVE_MICRO` 단계를 거치세요.
3. **API 키 보안**: `secrets.yaml`은 절대 공개 저장소에 커밋하지 마세요.
4. **시스템 모니터링**: 자동매매 중에도 주기적으로 상태를 확인하세요.

## 📄 라이선스

MIT License

## 🙏 감사의 말

- 한국투자증권 Open API
- Ollama & Qwen3
- 모든 오픈소스 기여자들

---

**ScalpingBot v2.4** - 실전 준비 완료 🤖📈
