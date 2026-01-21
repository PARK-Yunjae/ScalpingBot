#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScalpingBot v3.1 기능 테스트
- MACD/RSI 기술적 필터
- TV100 조건검색
- 유니버스 갱신
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime


def test_technical_filter():
    """기술적 필터 테스트"""
    print("\n" + "=" * 60)
    print("[1] MACD/RSI 기술적 필터 테스트")
    print("=" * 60)
    
    try:
        from scalping.strategy.minute_indicators import (
            calculate_macd_signal,
            calculate_rsi_crossover,
            check_technical_filter,
        )
        
        # 테스트 데이터 (상승 추세)
        import numpy as np
        np.random.seed(42)
        
        # 상승 추세 데이터
        base = 10000
        trend = np.linspace(0, 500, 30)
        noise = np.random.randn(30) * 50
        closes_up = (base + trend + noise).tolist()
        
        print("\n📈 상승 추세 데이터:")
        macd = calculate_macd_signal(closes_up)
        rsi = calculate_rsi_crossover(closes_up)
        tech = check_technical_filter(closes_up)
        
        print(f"   MACD: {macd['macd_value']:.4f} / Signal: {macd['signal_value']:.4f}")
        print(f"   MACD 골든크로스: {macd['golden_cross']}")
        print(f"   MACD > Signal: {macd['macd_above']}")
        print(f"   RSI: {rsi['rsi_value']:.1f}")
        print(f"   RSI 30 상향돌파: {rsi['upward_cross_30']}")
        print(f"   복합 매수신호: {tech['buy_signal']}")
        print(f"   보너스 점수: +{tech['score_bonus']}")
        print(f"   사유: {tech['reasons']}")
        
        # 하락 추세 데이터
        closes_down = (base - trend + noise).tolist()
        
        print("\n📉 하락 추세 데이터:")
        macd = calculate_macd_signal(closes_down)
        rsi = calculate_rsi_crossover(closes_down)
        tech = check_technical_filter(closes_down)
        
        print(f"   MACD: {macd['macd_value']:.4f} / Signal: {macd['signal_value']:.4f}")
        print(f"   MACD 데드크로스: {macd['dead_cross']}")
        print(f"   RSI: {rsi['rsi_value']:.1f}")
        print(f"   RSI 과매도: {rsi['is_oversold']}")
        print(f"   복합 매수신호: {tech['buy_signal']}")
        print(f"   복합 매도신호: {tech['sell_signal']}")
        
        print("\n✅ 기술적 필터 테스트 PASS")
        return True
        
    except Exception as e:
        print(f"\n❌ 기술적 필터 테스트 FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_condition_search():
    """TV100 조건검색 테스트"""
    print("\n" + "=" * 60)
    print("[2] TV100 조건검색 테스트")
    print("=" * 60)
    
    try:
        import yaml
        
        # secrets 로드
        secrets_path = Path(__file__).parent.parent / 'config' / 'secrets.yaml'
        if not secrets_path.exists():
            print("   ⚠️ secrets.yaml 없음 - 스킵")
            return True
        
        with open(secrets_path, 'r', encoding='utf-8') as f:
            secrets = yaml.safe_load(f)
        
        kis_config = secrets.get('kis', {})
        hts_id = kis_config.get('hts_id', '')
        
        if not hts_id:
            print("   ⚠️ hts_id 없음 - 스킵")
            return True
        
        from scalping.execution.broker import KISBroker
        
        broker = KISBroker(config=kis_config)
        
        print(f"\n   HTS ID: {hts_id}")
        print("   조건검색 목록 조회 중...")
        
        conditions = broker.get_condition_list(hts_id)
        
        if conditions:
            print(f"   ✅ 조건식 {len(conditions)}개 발견:")
            for c in conditions[:5]:
                print(f"      - {c.get('name')} (seq={c.get('seq')})")
        else:
            print("   ⚠️ 조건식 없음 (HTS에서 서버저장 필요)")
        
        # TV100 검색
        print("\n   TV100 검색 중...")
        stocks = broker.get_condition_stocks("TV100", limit=10)
        
        if stocks:
            print(f"   ✅ TV100 결과: {len(stocks)}개")
            for s in stocks[:5]:
                print(f"      - {s.get('name')} ({s.get('code')}) {s.get('price', 0):,}원")
        else:
            print("   ⚠️ TV100 결과 없음")
        
        print("\n✅ 조건검색 테스트 PASS")
        return True
        
    except Exception as e:
        print(f"\n❌ 조건검색 테스트 FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_scalp_engine_init():
    """ScalpEngine 초기화 테스트"""
    print("\n" + "=" * 60)
    print("[3] ScalpEngine 초기화 테스트")
    print("=" * 60)
    
    try:
        from scalping.engine.scalp_engine import ScalpEngine
        
        engine = ScalpEngine()
        result = engine.initialize()
        
        if result:
            print("\n✅ ScalpEngine 초기화 PASS")
            
            # 유니버스 갱신 테스트
            print("\n   유니버스 갱신 테스트...")
            engine._last_universe_refresh = datetime.now()
            engine._check_universe_refresh()
            print(f"   현재 유니버스: {len(engine._trackers)}개")
            
        else:
            print("\n❌ ScalpEngine 초기화 FAIL")
        
        return result
        
    except Exception as e:
        print(f"\n❌ ScalpEngine 테스트 FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("=" * 60)
    print("ScalpingBot v3.1 기능 테스트")
    print(f"테스트 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    results = {
        '기술적 필터 (MACD/RSI)': test_technical_filter(),
        'TV100 조건검색': test_condition_search(),
        'ScalpEngine 초기화': test_scalp_engine_init(),
    }
    
    print("\n" + "=" * 60)
    print("📋 테스트 결과 요약")
    print("=" * 60)
    
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"   {name}: {status}")
    
    all_passed = all(results.values())
    print("\n" + ("🎉 모든 테스트 통과!" if all_passed else "⚠️ 일부 테스트 실패"))
    
    return 0 if all_passed else 1


if __name__ == '__main__':
    sys.exit(main())
