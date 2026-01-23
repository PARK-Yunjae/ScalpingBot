# ScalpingBot v3.2.3 패치

## 📋 핵심 변경: AdaptiveMode → SignalGenerator 연동 + 계좌 상세 정보

**기존 문제**: 
1. config.yaml의 `adaptive_mode.modes.BALANCED.min_score`가 실제 매매에 적용되지 않음
2. 시작 시 예수금만 표시, 보유종목/포지션 상태 미확인
3. 예수금 부족해도 매수 시도 → 주문 실패 다발

**해결**: 
1. AdaptiveMode의 min_score가 SignalGenerator에 동적으로 전달
2. 시작 시 예수금 + 실제 보유종목 + DB 포지션 비교 출력
3. 매수 전 예수금 확인 로직 추가

---

## 🔧 패치 내용

### 1. config.yaml - 점수 기준 상향 (실전 데이터 기준)

```yaml
adaptive_mode:
  modes:
    DEFENSIVE:  min_score: 85  # 기존 75
    BALANCED:   min_score: 80  # 기존 70 ⭐
    AGGRESSIVE: min_score: 75  # 기존 65
```

**근거 (오늘 실전 데이터)**:
- 80점 이상: 50% 승률, +6.06%
- 80점 미만: 25% 승률, -1.91%

### 2. scalp_signals.py - set_min_score() 메서드 추가

```python
def set_min_score(self, min_score: int):
    """AdaptiveMode에서 min_score 동적 업데이트"""
    self.min_score = min_score
    self.min_score_conservative = min_score + 10
```

### 3. scalp_engine.py - 주요 개선사항

#### 🆕 시작 시 상세 계좌 정보 출력
```
   ✅ 브로커 연결 성공
   ┌─────────────────────────────────────
   │ 💰 예수금(주문가능): 457,812원
   │ 📊 총 평가금액: 850,000원
   │ 📦 실제 보유종목: 2개
   │   - 삼성전자(005930): 10주 @ 71,000원 → 72,500원 (+2.11%)
   │   - 카카오(035720): 5주 @ 50,000원 → 48,500원 (-3.00%)
   └─────────────────────────────────────
```

#### 🆕 Safety 설정 출력
```
   ┌─────────────────────────────────────
   │ 📊 거래 설정 (config.yaml)
   │   - 최대 포지션: 3개
   │   - 종목당 금액: 130,000원
   │   - 연속손절 휴식: 3회 → 10분
   │   - 연속손절 중단: 7회
   │   - 일일손실 한도: -3.0%
   └─────────────────────────────────────
```

#### 🆕 포지션 동기화
- DB 포지션과 실제 보유종목 비교
- 유령 포지션 자동 제거 (DB에만 있고 실제로 없는 것)
- 미등록 보유종목 경고 (HTS에만 있고 DB에 없는 것)

#### 🆕 매수 전 예수금 확인
```python
# 예수금 부족 시 매수 스킵
if available_cash < required_amount:
    logger.warning(f"⚠️ 예수금 부족 - 매수 스킵: {tracker.name}")
    return
```

#### 🆕 AdaptiveMode 연동
- 초기화 시: AdaptiveMode → SignalGenerator로 min_score 전달
- 모드 전환 시: 자동으로 min_score 동기화
- 재시작 시: 오늘 거래 기록/연속손절/모드 복원

#### 프리마켓 시간 수정
```
08:00 → 08:50 (동시호가 거래량 반영)
```

---

## 📁 패치 파일

```
patch_v323/
├── config/config.yaml              ← adaptive_mode 점수 상향 (80점)
├── scalping/engine/scalp_engine.py ← 계좌상세 + 연동 + 예수금체크
├── scalping/strategy/scalp_signals.py ← set_min_score() 추가
└── README_v323.md
```

---

## 🔧 적용 방법

```powershell
# 1. 프로그램 종료

# 2. 파일 덮어쓰기
Copy-Item patch_v323\config\config.yaml -Destination config\config.yaml -Force
Copy-Item patch_v323\scalping\engine\scalp_engine.py -Destination scalping\engine\scalp_engine.py -Force
Copy-Item patch_v323\scalping\strategy\scalp_signals.py -Destination scalping\strategy\scalp_signals.py -Force

# 3. __pycache__ 삭제 (중요!)
Remove-Item -Recurse -Force scalping\engine\__pycache__
Remove-Item -Recurse -Force scalping\strategy\__pycache__

# 4. 재시작
python run_scalpingbot.py
```

---

## ✅ 확인 방법

재시작 후 로그:

```
[1/7] 브로커 초기화...
   ✅ 브로커 연결 성공
   ┌─────────────────────────────────────
   │ 💰 예수금(주문가능): 457,812원
   │ 📊 총 평가금액: 850,000원
   │ 📦 실제 보유종목: 0개
   └─────────────────────────────────────

[4/7] 안전장치 초기화...
   ┌─────────────────────────────────────
   │ 📊 거래 설정 (config.yaml)
   │   - 최대 포지션: 3개
   │   - 종목당 금액: 130,000원
   └─────────────────────────────────────

[9/10] Adaptive Mode 초기화...
   ✅ Adaptive Mode 초기화 완료 (모드: BALANCED, min_score: 80)

ScalpSignalGenerator 초기화 (최소점수:80)  ← 80점 적용 확인!
```

예수금 부족 시:
```
⚠️ 예수금 부족 - 매수 스킵: 삼성전자
   필요: 130,000원, 가용: 50,000원
```

---

## 📊 이제 config.yaml만 수정하면 됩니다!

```yaml
# 방어 모드로 시작 (85점):
adaptive_mode:
  default_mode: "DEFENSIVE"

# 점수 기준 직접 수정:
  modes:
    BALANCED:
      min_score: 85  # 원하는 값으로

# 금액 설정 변경:
safety:
  max_positions: 2         # 동시 보유 수
  max_position_size: 100000  # 종목당 금액
```
