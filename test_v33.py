#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScalpingBot v3.3 테스트 스크립트
장중 아닐 때 버그 체크용
"""

import sys
from pathlib import Path

# 경로 설정
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

print("=" * 60)
print("ScalpingBot v3.3 테스트")
print("=" * 60)

errors = []

# =============================================================================
# 1. Import 테스트
# =============================================================================
print("\n[1/6] Import 테스트...")

try:
    from scalping.strategy.simulation_tracker import (
        SimulationTracker, 
        VirtualPosition, 
        SimulationResult
    )
    print("   ✅ simulation_tracker.py import 성공")
except Exception as e:
    print(f"   ❌ simulation_tracker.py import 실패: {e}")
    errors.append(f"import simulation_tracker: {e}")

try:
    from scalping.config.config_loader import ConfigLoader
    print("   ✅ config_loader.py import 성공")
except Exception as e:
    print(f"   ❌ config_loader.py import 실패: {e}")
    errors.append(f"import config_loader: {e}")

try:
    from scalping.strategy.scalp_signals import ScalpSignalGenerator, SignalType
    print("   ✅ scalp_signals.py import 성공")
except Exception as e:
    print(f"   ❌ scalp_signals.py import 실패: {e}")
    errors.append(f"import scalp_signals: {e}")

try:
    import scalping.engine.scalp_engine as scalp_engine_module
    print("   ✅ scalp_engine.py import 성공")
except Exception as e:
    print(f"   ❌ scalp_engine.py import 실패: {e}")
    errors.append(f"import scalp_engine: {e}")


# =============================================================================
# 2. Config 테스트
# =============================================================================
print("\n[2/6] Config 로드 테스트...")

try:
    config_loader = ConfigLoader('config/config.yaml')
    config = config_loader.load()
    
    mode = config.get('mode')
    sim_config = config.get('simulation', {})
    
    print(f"   ✅ Config 로드 성공")
    print(f"      - mode: {mode}")
    print(f"      - simulation.take_profit_pct: {sim_config.get('take_profit_pct')}")
    print(f"      - simulation.stop_loss_pct: {sim_config.get('stop_loss_pct')}")
    print(f"      - simulation.min_score_to_track: {sim_config.get('min_score_to_track')}")
    
    if mode != 'SIMULATION':
        print(f"   ⚠️ 주의: mode가 SIMULATION이 아님 ({mode})")
        
except Exception as e:
    print(f"   ❌ Config 로드 실패: {e}")
    errors.append(f"config load: {e}")


# =============================================================================
# 3. SimulationTracker 기본 기능 테스트
# =============================================================================
print("\n[3/6] SimulationTracker 기본 기능 테스트...")

tracker = None
try:
    # 실제 경로 사용 (임시 파일 대신)
    test_db = BASE_DIR / 'db' / 'simulation_test.db'
    test_db.parent.mkdir(parents=True, exist_ok=True)
    
    tracker = SimulationTracker(
        db_path=str(test_db),
        max_hold_minutes=30,
        max_concurrent=10,
    )
    print("   ✅ SimulationTracker 생성 성공")
    
    pos = tracker.enter_virtual(
        stock_code="005930",
        stock_name="삼성전자",
        entry_price=72000,
        signal_score=85,
        signal_type="breakout",
        take_profit_pct=2.5,
        stop_loss_pct=-0.8,
    )
    
    if pos:
        print(f"   ✅ 가상 진입 성공: {pos.stock_name} @ {pos.entry_price:,}원")
        print(f"      - 익절가: {pos.take_profit_price:,}원")
        print(f"      - 손절가: {pos.stop_loss_price:,}원")
    else:
        print("   ❌ 가상 진입 실패")
        errors.append("가상 진입 실패")
    
except Exception as e:
    print(f"   ❌ SimulationTracker 테스트 실패: {e}")
    import traceback
    traceback.print_exc()
    errors.append(f"SimulationTracker: {e}")
finally:
    tracker = None  # 연결 해제


# =============================================================================
# 4. 가격 업데이트 & 패턴 분석 테스트
# =============================================================================
print("\n[4/6] 가격 업데이트 & 패턴 분석 테스트...")

tracker = None
try:
    test_db = BASE_DIR / 'db' / 'simulation_test2.db'
    
    tracker = SimulationTracker(db_path=str(test_db), max_hold_minutes=1)
    
    tracker.enter_virtual(
        stock_code="005930",
        stock_name="삼성전자",
        entry_price=10000,
        signal_score=80,
        signal_type="breakout",
        take_profit_pct=2.5,
        stop_loss_pct=-0.8,
    )
    
    prices = {"005930": 10000}
    
    for i in range(5):
        prices["005930"] += 50
        results = tracker.update_prices(prices)
        
        if results:
            for r in results:
                print(f"   ✅ 청산 감지: {r.stock_name} | {r.result.value} | {r.exit_pct:+.2f}%")
                print(f"      - 패턴: {r.pattern}")
                print(f"      - 히스토리 길이: {len(r.price_history)}")
    
    if tracker.get_active_positions():
        prices["005930"] = 9900
        results = tracker.update_prices(prices)
        
        if results:
            for r in results:
                print(f"   ✅ 손절 감지: {r.stock_name} | {r.result.value} | {r.exit_pct:+.2f}%")
                print(f"      - 패턴: {r.pattern}")
    
    print("   ✅ 가격 업데이트 테스트 완료")
    
except Exception as e:
    print(f"   ❌ 가격 업데이트 테스트 실패: {e}")
    import traceback
    traceback.print_exc()
    errors.append(f"가격 업데이트: {e}")
finally:
    tracker = None


# =============================================================================
# 5. 통계 & 리포트 테스트
# =============================================================================
print("\n[5/6] 통계 & 리포트 테스트...")

tracker = None
try:
    test_db = BASE_DIR / 'db' / 'simulation_test3.db'
    
    tracker = SimulationTracker(db_path=str(test_db), max_hold_minutes=1)
    
    test_cases = [
        ("005930", "삼성전자", 10000, 90, "breakout", 10300),
        ("035720", "카카오", 50000, 78, "pullback", 49500),
        ("000660", "SK하이닉스", 80000, 82, "breakout", 80500),
    ]
    
    for code, name, entry, score, stype, exit_price in test_cases:
        tracker.enter_virtual(
            stock_code=code,
            stock_name=name,
            entry_price=entry,
            signal_score=score,
            signal_type=stype,
        )
        tracker.update_prices({code: exit_price})
    
    tracker.close_all()
    
    stats = tracker.get_daily_stats()
    print(f"   ✅ 통계 조회 성공")
    print(f"      - 총 신호: {stats['total']}회")
    print(f"      - 승률: {stats['win_rate']:.1f}%")
    
    print("\n   --- 일일 리포트 테스트 ---")
    tracker.print_daily_report()
    
except Exception as e:
    print(f"   ❌ 통계 테스트 실패: {e}")
    import traceback
    traceback.print_exc()
    errors.append(f"통계: {e}")
finally:
    tracker = None


# =============================================================================
# 6. 타임라인 조회 테스트
# =============================================================================
print("\n[6/6] 타임라인 조회 테스트...")

tracker = None
try:
    test_db = BASE_DIR / 'db' / 'simulation_test4.db'
    
    tracker = SimulationTracker(db_path=str(test_db), max_hold_minutes=5)
    
    tracker.enter_virtual(
        stock_code="005930",
        stock_name="삼성전자",
        entry_price=10000,
        signal_score=85,
        signal_type="breakout",
    )
    
    price = 10000
    for i in range(6):
        if i < 3:
            price += 30
        else:
            price -= 50
        tracker.update_prices({"005930": price})
    
    tracker.update_prices({"005930": 9900})
    
    trade = tracker.get_trade_timeline(stock_code="005930")
    
    if trade:
        print(f"   ✅ 타임라인 조회 성공")
        print(f"      - 종목: {trade['stock_name']}")
        print(f"      - 결과: {trade['result']}")
        print(f"      - 히스토리: {len(trade['price_history'])}개 포인트")
        print(f"      - 패턴: {trade['pattern']}")
        
        print("\n   --- 타임라인 출력 테스트 ---")
        tracker.print_trade_timeline(stock_code="005930")
    else:
        print("   ⚠️ 타임라인 조회 결과 없음")
    
except Exception as e:
    print(f"   ❌ 타임라인 테스트 실패: {e}")
    import traceback
    traceback.print_exc()
    errors.append(f"타임라인: {e}")
finally:
    tracker = None


# =============================================================================
# 테스트 DB 정리
# =============================================================================
print("\n[정리] 테스트 DB 파일 삭제...")
import time
import gc

gc.collect()  # 가비지 컬렉션 강제 실행
time.sleep(0.5)  # Windows에서 파일 핸들 해제 대기

test_files = [
    'simulation_test.db',
    'simulation_test2.db', 
    'simulation_test3.db',
    'simulation_test4.db',
]

for fname in test_files:
    test_file = BASE_DIR / 'db' / fname
    try:
        if test_file.exists():
            test_file.unlink()
            print(f"   ✅ {fname} 삭제")
    except Exception as e:
        print(f"   ⚠️ {fname} 삭제 실패 (무시해도 됨)")


# =============================================================================
# 결과 요약
# =============================================================================
print("\n" + "=" * 60)
print("테스트 결과 요약")
print("=" * 60)

if errors:
    print(f"\n❌ 에러 {len(errors)}개 발견:")
    for err in errors:
        print(f"   - {err}")
else:
    print("\n✅ 모든 테스트 통과!")
    print("\n다음 단계:")
    print("   1. config.yaml에서 mode: SIMULATION 확인")
    print("   2. 장 시작 전에 실행: python scalping/engine/scalp_engine.py")
    print("   3. db/simulation.db 에 데이터 쌓임")
    print("   4. 장 종료 후 일일 리포트 확인")

print("\n" + "=" * 60)
