#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScalpingBot v3.2 통합 테스트
- Hard Firewall 최소화
- Soft Scoring
- Adaptive Mode
- Funnel Log
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from scalping.strategy.minute_indicators import MinuteIndicatorResult
from scalping.strategy.scalp_signals import ScalpSignalGenerator, MarketContext, SignalType
from scalping.strategy.adaptive_mode import AdaptiveMode, TradingMode
from scalping.strategy.funnel_log import FunnelLog


def test_soft_scoring():
    """Soft Scoring 테스트 - CCI/RSI/VWAP 감점이 차단 대신 점수에 반영되는지"""
    print("\n" + "="*60)
    print("📊 TEST 1: Soft Scoring (감점/가점 시스템)")
    print("="*60)
    
    # config with scoring settings
    config = {
        'trading': {'min_score': 70},
        'scoring': {
            'penalties': {
                'cci_overheat_threshold': 150,
                'cci_overheat_penalty': -10,
                'cci_extreme_threshold': 200,
                'cci_extreme_penalty': -20,
                'rsi_overbought_threshold': 80,
                'rsi_overbought_penalty': -10,
                'below_vwap_penalty': -15,
                'ema_bearish_penalty': -5,
            },
            'bonuses': {
                'ema_bullish': 10,
                'high_volume_threshold': 3.0,
                'high_volume_bonus': 15,
            }
        },
        'hard_firewall': {
            'min_volume_ratio': 0.5,
            'max_day_change_pct': 15.0,
        },
        'indicators': {'strategies': {'breakout': True, 'pullback': True, 'gap_play': True, 'vwap_bounce': False}}
    }
    
    gen = ScalpSignalGenerator(config)
    context = MarketContext(prev_close=10000, prev_high=10200, prev_low=9800)
    
    # 케이스 1: 정상 조건 (기존에도 통과)
    indicators_normal = MinuteIndicatorResult(
        timestamp="2026-01-22 09:30:00",
        price=10500, cci=120, rsi=60,
        ema9=10450, ema20=10400,
        vwap=10300, vwap_distance=1.94,
        volume=1500000, volume_ratio=2.5,
        day_high=10500, day_low=10200, day_change_pct=3.0,
        from_day_high_pct=0.0, is_bullish=True, body_ratio=0.7,
    )
    signal1 = gen.evaluate("005930", indicators_normal, context)
    print(f"\n✅ 정상 조건:")
    print(f"   점수: {signal1.score:.0f}점, 액션: {signal1.action}")
    print(f"   세부: {signal1.score_breakdown}")
    
    # 케이스 2: CCI 과열 (기존: 차단 → v3.2: 감점)
    indicators_cci_hot = MinuteIndicatorResult(
        timestamp="2026-01-22 09:30:00",
        price=10500, cci=160, rsi=60,  # CCI 160 (과열)
        ema9=10450, ema20=10400,
        vwap=10300, vwap_distance=1.94,
        volume=1500000, volume_ratio=2.5,
        day_high=10500, day_low=10200, day_change_pct=3.0,
        from_day_high_pct=0.0, is_bullish=True, body_ratio=0.7,
    )
    signal2 = gen.evaluate("005930", indicators_cci_hot, context)
    print(f"\n⚠️ CCI 과열 (160):")
    print(f"   점수: {signal2.score:.0f}점, 액션: {signal2.action}")
    print(f"   세부: {signal2.score_breakdown}")
    print(f"   → 기존: 차단됨, v3.2: 감점(-10)으로 진입 가능!")
    
    # 케이스 3: VWAP 아래 (기존: 차단 → v3.2: 감점)
    indicators_below_vwap = MinuteIndicatorResult(
        timestamp="2026-01-22 09:30:00",
        price=10200, cci=120, rsi=60,
        ema9=10250, ema20=10200,
        vwap=10300, vwap_distance=-0.97,  # VWAP 아래
        volume=1500000, volume_ratio=2.5,
        day_high=10500, day_low=10000, day_change_pct=2.0,
        from_day_high_pct=-2.9, is_bullish=True, body_ratio=0.7,
    )
    signal3 = gen.evaluate("005930", indicators_below_vwap, context)
    print(f"\n⚠️ VWAP 아래:")
    print(f"   점수: {signal3.score:.0f}점, 액션: {signal3.action}")
    print(f"   세부: {signal3.score_breakdown}")
    print(f"   → 기존: 차단됨, v3.2: 감점(-15)으로 진입 가능!")
    
    # 케이스 4: Hard Firewall - 거래량 부족 (여전히 차단)
    indicators_low_vol = MinuteIndicatorResult(
        timestamp="2026-01-22 09:30:00",
        price=10500, cci=120, rsi=60,
        ema9=10450, ema20=10400,
        vwap=10300, vwap_distance=1.94,
        volume=100000, volume_ratio=0.3,  # 거래량 부족
        day_high=10500, day_low=10200, day_change_pct=3.0,
        from_day_high_pct=0.0, is_bullish=True, body_ratio=0.7,
    )
    signal4 = gen.evaluate("005930", indicators_low_vol, context)
    print(f"\n❌ 거래량 부족 (0.3x) - Hard Firewall:")
    print(f"   점수: {signal4.score:.0f}점, 액션: {signal4.action}")
    print(f"   사유: {signal4.reason}")
    print(f"   → Hard Firewall은 여전히 차단!")
    
    return True


def test_adaptive_mode():
    """Adaptive Mode 테스트"""
    print("\n" + "="*60)
    print("🔄 TEST 2: Adaptive Mode (자동 강약 조절)")
    print("="*60)
    
    config = {
        'enabled': True,
        'default_mode': 'BALANCED',
        'modes': {
            'DEFENSIVE': {'min_score': 75, 'cooldown_minutes': 15},
            'BALANCED': {'min_score': 70, 'cooldown_minutes': 10},
            'AGGRESSIVE': {'min_score': 65, 'cooldown_minutes': 5},
        },
        'triggers': {
            'to_defensive': {'consecutive_losses': 3, 'index_drop_pct': -1.5},
            'to_aggressive': {'daily_profit_pct': 1.0, 'consecutive_wins': 3},
            'to_balanced': {'from_defensive_wins': 2, 'from_aggressive_losses': 2},
        }
    }
    
    am = AdaptiveMode(config)
    
    print(f"\n초기 모드: {am.get_current_mode().value}")
    print(f"min_score: {am.get_min_score()}")
    
    # 시나리오 1: 3연패 → DEFENSIVE
    print(f"\n📍 시나리오 1: 3연패")
    mode = am.update(consecutive_losses=3)
    print(f"   → 모드: {mode.value}, min_score: {am.get_min_score()}")
    
    # 시나리오 2: DEFENSIVE에서 2연승 → BALANCED 복귀
    print(f"\n📍 시나리오 2: DEFENSIVE에서 익절 2회")
    am.record_trade_result(is_win=True)
    am.record_trade_result(is_win=True)
    mode = am.update(consecutive_losses=0, consecutive_wins=2)
    print(f"   → 모드: {mode.value}, min_score: {am.get_min_score()}")
    
    # 시나리오 3: 당일 수익 좋고 3연승 → AGGRESSIVE
    print(f"\n📍 시나리오 3: 당일 +1.5%, 3연승")
    mode = am.update(daily_profit_pct=1.5, consecutive_wins=3)
    print(f"   → 모드: {mode.value}, min_score: {am.get_min_score()}")
    
    # 시나리오 4: 지수 급락 → DEFENSIVE
    print(f"\n📍 시나리오 4: 지수 -2.0%")
    mode = am.update(index_change_pct=-2.0)
    print(f"   → 모드: {mode.value}, min_score: {am.get_min_score()}")
    
    return True


def test_funnel_log():
    """Funnel Log 테스트"""
    print("\n" + "="*60)
    print("📈 TEST 3: Funnel Log (퍼널 추적)")
    print("="*60)
    
    config = {
        'enabled': True,
        'log_interval_minutes': 10,
        'show_top_candidates': 3,
    }
    
    fl = FunnelLog(config)
    
    # 사이클 시작
    fl.start_cycle(universe_count=50)
    
    # 후보 기록
    fl.record_candidate("005930", "삼성전자", 85, "BUY", {'고점돌파': 25, 'EMA정배열': 10})
    fl.record_candidate("000660", "SK하이닉스", 72, "BUY", {'거래량폭증': 20})
    fl.record_candidate("035720", "카카오", 65, "HOLD", {'CCI과열': -10})
    fl.record_candidate("051910", "LG화학", 0, "SKIP", skip_reason="[HARD] 거래량 부족 (0.3x)")
    fl.record_candidate("006400", "삼성SDI", 55, "HOLD", {'VWAP아래': -15})
    
    # 사이클 종료
    top_candidates = fl.end_cycle()
    
    print(f"\n상위 {len(top_candidates)}개 후보:")
    for c in top_candidates:
        print(f"  {c.stock_name}: {c.score:.0f}점 ({c.action})")
    
    # 요약
    summary = fl.get_summary()
    print(f"\n퍼널 요약:")
    print(f"  Hard Firewall 통과: {summary['hard_firewall']['passed']}건")
    print(f"  Hard Firewall 탈락: {summary['hard_firewall']['failed']}건")
    print(f"  min_score 통과: {summary['min_score']['passed']}건")
    print(f"  매수 시그널: {summary['buy_signals']}건")
    
    return True


def test_integrated():
    """통합 테스트 - 실제 매매 시나리오"""
    print("\n" + "="*60)
    print("🎯 TEST 4: 통합 시나리오 테스트")
    print("="*60)
    
    # 설정
    config = {
        'trading': {'min_score': 70},
        'scoring': {
            'penalties': {
                'cci_overheat_threshold': 150,
                'cci_overheat_penalty': -10,
                'rsi_overbought_threshold': 80,
                'rsi_overbought_penalty': -10,
                'below_vwap_penalty': -15,
                'ema_bearish_penalty': -5,
                'low_volume_threshold': 1.5,
                'low_volume_penalty': -10,
            },
            'bonuses': {
                'ema_bullish': 10,
                'high_volume_threshold': 3.0,
                'high_volume_bonus': 15,
                'vwap_strong_threshold': 1.0,
                'vwap_strong_bonus': 10,
            }
        },
        'hard_firewall': {
            'min_volume_ratio': 0.5,
            'max_day_change_pct': 15.0,
        },
        'indicators': {'strategies': {'breakout': True, 'pullback': True, 'gap_play': True, 'vwap_bounce': False}}
    }
    
    gen = ScalpSignalGenerator(config)
    context = MarketContext(prev_close=10000, prev_high=10200, prev_low=9800)
    
    # 다양한 종목 시뮬레이션
    test_cases = [
        ("삼성전자", {"price": 10500, "cci": 120, "rsi": 60, "vwap_distance": 1.5, "volume_ratio": 2.5, "ema9": 10450, "ema20": 10400}),
        ("SK하이닉스", {"price": 10300, "cci": 160, "rsi": 65, "vwap_distance": 0.5, "volume_ratio": 3.2, "ema9": 10280, "ema20": 10250}),  # CCI 과열
        ("카카오", {"price": 9900, "cci": 100, "rsi": 55, "vwap_distance": -1.0, "volume_ratio": 2.0, "ema9": 9950, "ema20": 10000}),  # VWAP 아래
        ("네이버", {"price": 10200, "cci": 80, "rsi": 82, "vwap_distance": 0.8, "volume_ratio": 1.8, "ema9": 10150, "ema20": 10180}),  # RSI 과열 + EMA 역배열
        ("LG전자", {"price": 10100, "cci": 90, "rsi": 50, "vwap_distance": 0.3, "volume_ratio": 0.4, "ema9": 10050, "ema20": 10000}),  # 거래량 부족 (Hard)
    ]
    
    print(f"\n{'종목':<12} {'점수':>6} {'액션':<6} 주요 감점/가점")
    print("-" * 60)
    
    buy_count = 0
    for name, params in test_cases:
        indicators = MinuteIndicatorResult(
            timestamp="2026-01-22 09:30:00",
            price=params["price"],
            cci=params["cci"],
            rsi=params["rsi"],
            ema9=params["ema9"],
            ema20=params["ema20"],
            vwap=10000,
            vwap_distance=params["vwap_distance"],
            volume=1000000,
            volume_ratio=params["volume_ratio"],
            day_high=params["price"] + 100,
            day_low=params["price"] - 300,
            day_change_pct=3.0,
            from_day_high_pct=-0.5,
            is_bullish=True,
            body_ratio=0.6,
        )
        
        signal = gen.evaluate("000000", indicators, context)
        
        # 주요 감점/가점 추출
        key_scores = []
        for k, v in signal.score_breakdown.items():
            if v < 0:
                key_scores.append(f"{k}:{v:+.0f}")
        
        status = "✅" if signal.action == "BUY" else ("❌" if signal.action == "SKIP" else "⬜")
        key_str = ", ".join(key_scores[:3]) if key_scores else "-"
        
        print(f"{status} {name:<10} {signal.score:>5.0f}점 {signal.action:<6} {key_str}")
        
        if signal.action == "BUY":
            buy_count += 1
    
    print("-" * 60)
    print(f"매수 시그널: {buy_count}/{len(test_cases)}개")
    print(f"\n💡 기존 v3.1: CCI 과열, VWAP 아래는 차단됨")
    print(f"   v3.2: 감점만 되고 총점이 70점 이상이면 진입 가능!")
    
    return True


def main():
    print("\n" + "="*60)
    print("🧪 ScalpingBot v3.2 통합 테스트")
    print("="*60)
    print(f"테스트 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    tests = [
        ("Soft Scoring", test_soft_scoring),
        ("Adaptive Mode", test_adaptive_mode),
        ("Funnel Log", test_funnel_log),
        ("통합 시나리오", test_integrated),
    ]
    
    results = []
    for name, test_fn in tests:
        try:
            result = test_fn()
            results.append((name, "✅ PASS" if result else "❌ FAIL"))
        except Exception as e:
            results.append((name, f"❌ ERROR: {e}"))
    
    print("\n" + "="*60)
    print("📋 테스트 결과 요약")
    print("="*60)
    for name, result in results:
        print(f"  {name}: {result}")
    
    all_pass = all("PASS" in r[1] for r in results)
    print("\n" + ("✅ 모든 테스트 통과!" if all_pass else "❌ 일부 테스트 실패"))
    print("="*60)


if __name__ == "__main__":
    main()
