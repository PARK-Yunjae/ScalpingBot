#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
구조 기반 손절 + EMA 정배열 테스트
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from scalping.strategy.minute_indicators import MinuteIndicators, OHLCV, MinuteIndicatorResult
from scalping.strategy.scalp_signals import ScalpSignalGenerator, MarketContext, SignalType
from scalping.execution.position_manager import PositionManager, PositionInfo

def test_ema_calculation():
    """EMA 9/20 계산 테스트"""
    print("\n" + "="*60)
    print("📊 EMA 9/20 계산 테스트")
    print("="*60)
    
    calc = MinuteIndicators(prev_close=10000)
    
    # 상승 추세 데이터 생성
    prices = [10000, 10050, 10100, 10080, 10150, 10200, 10180, 10250, 10300, 10280,
              10350, 10400, 10380, 10450, 10500, 10480, 10550, 10600, 10580, 10650,
              10700, 10680, 10750, 10800, 10780]
    
    result = None
    for i, price in enumerate(prices):
        candle = OHLCV(
            timestamp=f"2026-01-22 09:{i:02d}:00",
            open=price - 20,
            high=price + 30,
            low=price - 50,
            close=price,
            volume=1000000 + i * 10000
        )
        result = calc.update(candle)
    
    print(f"\n마지막 봉:")
    print(f"  가격: {result.price:,.0f}원")
    print(f"  EMA5: {result.ema5:,.2f}")
    print(f"  EMA9: {result.ema9:,.2f}")
    print(f"  EMA10: {result.ema10:,.2f}")
    print(f"  EMA20: {result.ema20:,.2f}")
    
    ema_status = "정배열 ✅" if result.ema9 >= result.ema20 else "역배열 ⚠️"
    print(f"  EMA9/20: {ema_status}")
    
    return result

def test_signal_with_ema():
    """EMA 정배열 점수 포함 시그널 테스트"""
    print("\n" + "="*60)
    print("📈 EMA 정배열 점수 테스트")
    print("="*60)
    
    gen = ScalpSignalGenerator({
        'trading': {
            'stop_loss': -0.8,
            'take_profit_1': 2.5,
            'min_score': 70,
        },
        'indicators': {
            'strategies': {
                'breakout': True,
                'pullback': True,
                'gap_play': True,
                'vwap_bounce': False,
            }
        }
    })
    
    # 정배열 상태의 지표
    indicators_bullish = MinuteIndicatorResult(
        timestamp="2026-01-22 09:30:00",
        price=10500,
        cci=120,
        rsi=60,
        ema5=10480,
        ema9=10450,    # EMA9 > EMA20
        ema10=10420,
        ema20=10380,
        vwap=10300,
        vwap_distance=1.94,
        volume=1500000,
        volume_ratio=2.5,
        day_high=10500,
        day_low=10200,
        day_change_pct=3.0,
        from_day_high_pct=0.0,
        is_bullish=True,
        body_ratio=0.7,
    )
    
    context = MarketContext(
        prev_close=10200,
        prev_high=10400,
        prev_low=10000,
    )
    
    signal = gen.evaluate("005930", indicators_bullish, context, "삼성전자")
    
    print(f"\n🎯 정배열 시그널 (EMA9 > EMA20):")
    print(f"  종목: {signal.stock_code}")
    print(f"  액션: {signal.action}")
    print(f"  점수: {signal.score:.0f}점")
    print(f"  전략: {signal.signal_type.value}")
    print(f"  구조 손절 레벨: {signal.breakout_level:,.0f}원")
    print(f"  VWAP: {signal.vwap_at_entry:,.0f}원")
    print(f"\n  점수 세부:")
    for key, val in signal.score_breakdown.items():
        print(f"    - {key}: {val:+.0f}")
    
    # 역배열 상태 테스트
    indicators_bearish = MinuteIndicatorResult(
        timestamp="2026-01-22 09:30:00",
        price=10500,
        cci=120,
        rsi=60,
        ema5=10480,
        ema9=10350,    # EMA9 < EMA20 (역배열!)
        ema10=10420,
        ema20=10400,
        vwap=10300,
        vwap_distance=1.94,
        volume=1500000,
        volume_ratio=2.5,
        day_high=10500,
        day_low=10200,
        day_change_pct=3.0,
        from_day_high_pct=0.0,
        is_bullish=True,
        body_ratio=0.7,
    )
    
    signal2 = gen.evaluate("005930", indicators_bearish, context, "삼성전자")
    
    print(f"\n⚠️ 역배열 시그널 (EMA9 < EMA20):")
    print(f"  점수: {signal2.score:.0f}점")
    print(f"  점수 세부:")
    for key, val in signal2.score_breakdown.items():
        print(f"    - {key}: {val:+.0f}")
    
    return signal, signal2

def test_structure_stop():
    """구조 기반 손절 테스트"""
    print("\n" + "="*60)
    print("🛡️ 구조 기반 손절 테스트")
    print("="*60)
    
    pm = PositionManager(
        stop_loss=-0.8,
        use_structure_stop=True,
        use_breakout_stop=True,
        use_vwap_stop=True,
    )
    
    # 테스트용 포지션 추가
    position = pm.add_position(
        stock_code="005930",
        stock_name="삼성전자",
        entry_price=10500,
        quantity=10,
        score=75,
        breakout_level=10450,    # 돌파 기준가
        vwap_at_entry=10400,     # VWAP
    )
    
    print(f"\n📍 포지션 등록:")
    print(f"  종목: {position.stock_code} {position.stock_name}")
    print(f"  진입가: {position.entry_price:,.0f}원")
    print(f"  돌파선: {position.breakout_level:,.0f}원")
    print(f"  VWAP: {position.vwap_at_entry:,.0f}원")
    
    # 시나리오 1: 돌파선 위 → HOLD
    print(f"\n시나리오 1: 현재가 10,500원 (돌파선 위)")
    signal1 = pm.update_price("005930", 10500)
    print(f"  → {signal1.action}: {signal1.message}")
    
    # 시나리오 2: 돌파선 아래 복귀 → SELL
    print(f"\n시나리오 2: 현재가 10,420원 (돌파선 아래)")
    signal2 = pm.update_price("005930", 10420)
    print(f"  → {signal2.action}: {signal2.message}")
    
    # 리셋 후 VWAP 이탈 테스트
    pm._positions.clear()
    position2 = pm.add_position(
        stock_code="000660",
        stock_name="SK하이닉스",
        entry_price=10500,
        quantity=10,
        score=75,
        breakout_level=10300,    # 돌파선은 멀리
        vwap_at_entry=10450,     # VWAP은 가까이
    )
    
    # 시나리오 3: VWAP 이탈
    print(f"\n시나리오 3: 현재가 10,420원 (VWAP 아래)")
    signal3 = pm.update_price("000660", 10420)
    print(f"  → {signal3.action}: {signal3.message}")
    
    # 포지션 정리
    pm.remove_position("000660")
    
    return pm

def main():
    print("\n" + "="*60)
    print("🧪 ScalpingBot v3.1 - 구조 손절 + EMA 테스트")
    print("="*60)
    print(f"테스트 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 1. EMA 계산 테스트
    test_ema_calculation()
    
    # 2. EMA 포함 시그널 테스트
    test_signal_with_ema()
    
    # 3. 구조 손절 테스트
    test_structure_stop()
    
    print("\n" + "="*60)
    print("✅ 모든 테스트 완료!")
    print("="*60)

if __name__ == "__main__":
    main()
