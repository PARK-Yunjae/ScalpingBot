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

# scalp_engine은 broker 연결 시도하므로 import만 테스트
try:
    # 모듈 자체만 import (인스턴스 생성 X)
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
    
    # 시뮬레이션 설정 확인
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

try:
    import tempfile
    import os
    
    # 임시 DB로 테스트
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    tracker = SimulationTracker(
        db_path=test_db,
        max_hold_minutes=30,
        max_concurrent=10,
    )
    print("   ✅ SimulationTracker 생성 성공")
    
    # 가상 진입 테스트
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
    
    # 정리
    os.unlink(test_db)
    
except Exception as e:
    print(f"   ❌ SimulationTracker 테스트 실패: {e}")
    import traceback
    traceback.print_exc()
    errors.append(f"SimulationTracker: {e}")


# =============================================================================
# 4. 가격 업데이트 & 패턴 분석 테스트
# =============================================================================
print("\n[4/6] 가격 업데이트 & 패턴 분석 테스트...")

try:
    import tempfile
    import os
    import time
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    tracker = SimulationTracker(db_path=test_db, max_hold_minutes=1)
    
    # 가상 진입
    tracker.enter_virtual(
        stock_code="005930",
        stock_name="삼성전자",
        entry_price=10000,
        signal_score=80,
        signal_type="breakout",
        take_profit_pct=2.5,
        stop_loss_pct=-0.8,
    )
    
    # 가격 업데이트 시뮬레이션 (익절 시나리오)
    prices = {"005930": 10000}
    
    # 점진적 상승
    for i in range(5):
        prices["005930"] += 50  # +0.5%씩
        results = tracker.update_prices(prices)
        
        if results:
            for r in results:
                print(f"   ✅ 청산 감지: {r.stock_name} | {r.result.value} | {r.exit_pct:+.2f}%")
                print(f"      - 패턴: {r.pattern}")
                print(f"      - 히스토리 길이: {len(r.price_history)}")
    
    # 아직 청산 안됐으면 손절 테스트
    if tracker.get_active_positions():
        prices["005930"] = 9900  # -1%로 급락
        results = tracker.update_prices(prices)
        
        if results:
            for r in results:
                print(f"   ✅ 손절 감지: {r.stock_name} | {r.result.value} | {r.exit_pct:+.2f}%")
                print(f"      - 패턴: {r.pattern}")
    
    print("   ✅ 가격 업데이트 테스트 완료")
    
    os.unlink(test_db)
    
except Exception as e:
    print(f"   ❌ 가격 업데이트 테스트 실패: {e}")
    import traceback
    traceback.print_exc()
    errors.append(f"가격 업데이트: {e}")


# =============================================================================
# 5. 통계 & 리포트 테스트
# =============================================================================
print("\n[5/6] 통계 & 리포트 테스트...")

try:
    import tempfile
    import os
    from datetime import datetime
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    tracker = SimulationTracker(db_path=test_db, max_hold_minutes=1)
    
    # 여러 거래 시뮬레이션
    test_cases = [
        ("005930", "삼성전자", 10000, 90, "breakout", 10300),  # 익절
        ("035720", "카카오", 50000, 78, "pullback", 49500),    # 손절
        ("000660", "SK하이닉스", 80000, 82, "breakout", 80500), # 시간초과
    ]
    
    for code, name, entry, score, stype, exit_price in test_cases:
        tracker.enter_virtual(
            stock_code=code,
            stock_name=name,
            entry_price=entry,
            signal_score=score,
            signal_type=stype,
        )
        
        # 바로 청산 (익절 또는 손절)
        tracker.update_prices({code: exit_price})
    
    # 남은 포지션 강제 청산
    tracker.close_all()
    
    # 통계 조회
    stats = tracker.get_daily_stats()
    print(f"   ✅ 통계 조회 성공")
    print(f"      - 총 신호: {stats['total']}회")
    print(f"      - 승률: {stats['win_rate']:.1f}%")
    
    # 리포트 출력 테스트 (에러 없이 실행되는지)
    print("\n   --- 일일 리포트 테스트 ---")
    tracker.print_daily_report()
    
    os.unlink(test_db)
    
except Exception as e:
    print(f"   ❌ 통계 테스트 실패: {e}")
    import traceback
    traceback.print_exc()
    errors.append(f"통계: {e}")


# =============================================================================
# 6. 타임라인 조회 테스트
# =============================================================================
print("\n[6/6] 타임라인 조회 테스트...")

try:
    import tempfile
    import os
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        test_db = f.name
    
    tracker = SimulationTracker(db_path=test_db, max_hold_minutes=5)
    
    # 가상 진입
    tracker.enter_virtual(
        stock_code="005930",
        stock_name="삼성전자",
        entry_price=10000,
        signal_score=85,
        signal_type="breakout",
    )
    
    # 가격 변동 시뮬레이션 (여러 번 업데이트)
    price = 10000
    for i in range(6):
        if i < 3:
            price += 30  # 상승
        else:
            price -= 50  # 하락
        tracker.update_prices({"005930": price})
    
    # 손절 트리거
    tracker.update_prices({"005930": 9900})
    
    # 타임라인 조회
    trade = tracker.get_trade_timeline(stock_code="005930")
    
    if trade:
        print(f"   ✅ 타임라인 조회 성공")
        print(f"      - 종목: {trade['stock_name']}")
        print(f"      - 결과: {trade['result']}")
        print(f"      - 히스토리: {len(trade['price_history'])}개 포인트")
        print(f"      - 패턴: {trade['pattern']}")
        
        # 타임라인 출력 테스트
        print("\n   --- 타임라인 출력 테스트 ---")
        tracker.print_trade_timeline(stock_code="005930")
    else:
        print("   ⚠️ 타임라인 조회 결과 없음")
    
    os.unlink(test_db)
    
except Exception as e:
    print(f"   ❌ 타임라인 테스트 실패: {e}")
    import traceback
    traceback.print_exc()
    errors.append(f"타임라인: {e}")


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
    print("   2. 장 시작 전에 실행")
    print("   3. db/simulation.db 에 데이터 쌓임")
    print("   4. 장 종료 후 일일 리포트 확인")

print("\n" + "=" * 60)
