# ScalpingBot v2.4.1 패치 노트

> **기준**: v2.4 설계서 및 검증 체크리스트 반영
> **날짜**: 2026-01-16
> **상태**: P0 블로커 해결, P1 이슈 수정 완료

---

## 수정 요약

### P0 블로커 해결 (5개)

| # | 문제 | 수정 내용 | 파일 |
|---|------|----------|------|
| 1 | Import 클래스명 불일치 | `TickData`/`OrderbookData` → `PriceTick`/`OrderbookTick` 별칭 추가 | `scalping/data/__init__.py` |
| 2 | main.py ↔ TradingEngine API 불일치 | 생성자 유연화 + `start()`/`is_running()` 메서드 추가 | `scalping/engine/trading_engine.py` |
| 3 | AIEngine 생성자 호출 | `AIEngine(model=..., timeout=...)` → `AIEngine(config=...)` | `scalping/engine/trading_engine.py` |
| 4 | 지수 MA20 데이터 소스 누락 | `get_index_daily()` 메서드 추가 (FinanceDataReader 사용) | `scalping/execution/broker.py` |
| 5 | MarketMonitor 일봉 MA20 오염 | 틱 append 제거, 일봉 히스토리 + 현재가로 MA 계산 | `scalping/data/market_monitor.py` |

### P1 이슈 수정 (2개)

| # | 문제 | 수정 내용 | 파일 |
|---|------|----------|------|
| 1 | 점수 정규화 분모 불일치 | `75` → `self.max_raw_score` (가중치 합 85점) | `scalping/strategy/score_engine.py` |
| 2 | 설정 키 참조 유연화 | `config['risk']['stop_loss_pct']` 등 다양한 구조 지원 | `main.py` |

---

## 상세 수정 내역

### 1. scalping/data/__init__.py

```python
# 변경 전
from scalping.data.realtime_feed import TickData, OrderbookData

# 변경 후  
from scalping.data.realtime_feed import PriceTick, OrderbookTick
TickData = PriceTick        # 하위 호환
OrderbookData = OrderbookTick  # 하위 호환
```

### 2. scalping/engine/trading_engine.py

**생성자 유연화:**
```python
def __init__(
    self,
    config: Dict[str, Any],
    secrets: Dict[str, Any] = None,      # 방식 1
    kis_config: Dict[str, Any] = None,   # 방식 2
    discord_config: Dict[str, Any] = None,
    dry_run: bool = None,
):
```

**start()/is_running() 메서드 추가:**
```python
def start(self):
    """별도 스레드에서 run() 실행"""
    self._engine_thread = threading.Thread(target=self.run, daemon=True)
    self._engine_thread.start()

def is_running(self) -> bool:
    return self._running
```

**AIEngine 호출 수정:**
```python
# 변경 전
self.ai_engine = AIEngine(model=..., timeout=...)

# 변경 후
self.ai_engine = AIEngine(config=ai_config)
```

**지수 일봉 히스토리 초기화 추가:**
```python
kospi_daily = self.broker.get_index_daily('0001', period=60)
self.market_monitor.initialize_history(kospi_daily, kosdaq_daily)
```

### 3. scalping/execution/broker.py

**get_index_daily() 메서드 추가:**
```python
def get_index_daily(self, index_code: str = '0001', period: int = 60) -> List[float]:
    """FinanceDataReader로 지수 일봉 종가 조회"""
    import FinanceDataReader as fdr
    fdr_code = {'0001': 'KS11', '1001': 'KQ11'}.get(index_code, 'KS11')
    df = fdr.DataReader(fdr_code, start_date, end_date)
    return df['Close'].tolist()[-period:]
```

### 4. scalping/data/market_monitor.py

**_update_market_data() 수정:**
- 틱 데이터를 `_kospi_history`에 append하지 않음
- 일봉 히스토리는 `initialize_history()`에서 로드한 값 유지

**_calculate_moving_averages() 수정:**
```python
# 일봉 히스토리 + 현재가로 MA 계산
calc_history = history + [current_price]
self.state.kospi_ma20 = sum(calc_history[-20:]) / 20
```

### 5. scalping/strategy/score_engine.py

**정규화 분모 수정:**
```python
# 변경 전
normalized = (raw_total / 75.0) * 100.0

# 변경 후
normalized = (raw_total / self.max_raw_score) * 100.0  # 85점 기준
```

### 6. main.py

**설정 로그 유연화:**
```python
# 다양한 config 구조 지원
stop_loss = (
    config.get('risk', {}).get('stop_loss_pct') or
    config.get('trading', {}).get('stop_loss') or
    config.get('safety', {}).get('stop_loss_pct', 'N/A')
)
```

---

## 검증 결과

### Import 테스트
```
✅ scalping 모듈
✅ scalping.data (PriceTick, OrderbookTick, TickData, OrderbookData)
✅ scalping.config
✅ scalping.strategy
✅ scalping.ai
✅ scalping.execution
✅ scalping.engine (TradingEngine)
```

### ScoreEngine 정규화 테스트
```
max_raw_score: 85점 (가중치 합)
테스트 1 (최고점): 원점수 85점 → 정규화 100점 ✅
테스트 2 (중간):   원점수 54.8점 → 정규화 64.5점 ✅
```

### TradingEngine API 테스트
```
✅ main.py 스타일 호출 (kis_config, discord_config, dry_run)
✅ 속성: dry_run=True, max_positions=3, position_size=100000
✅ 메서드: start(), stop(), is_running(), run(), initialize()
```

---

## 다음 단계 (권장)

1. **Smoke Test**: `python -c "import scalping; print('OK')"`
2. **Config 로드 테스트**: `python main.py --help`
3. **LIVE_DATA_ONLY 드라이런**: 10~30분 구동하여 로그 확인
4. **pytest 실행**: `pytest -q tests/`
5. **소액 실전 테스트**: LIVE_MICRO 모드로 5만원 단위 테스트

---

**ScalpingBot v2.4.1** - P0 블로커 해결 완료 🔧
