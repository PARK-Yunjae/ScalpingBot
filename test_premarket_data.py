#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v3.0 - 프리마켓 데이터 수집 테스트
============================================================================
장 시작 전 각 데이터 수집 기능이 정상 작동하는지 테스트합니다.

테스트 항목:
1. 거래량 상위 조회 (한투 API)
2. 뉴스 수집 (네이버 API)
3. 분봉 데이터 조회 (한투 API)
4. 일봉 데이터 조회 (한투 API)
5. 지수 데이터 조회 (한투 API)

실행:
    python test_premarket_data.py
============================================================================
"""

import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path

# 상위 디렉토리 import
sys.path.insert(0, str(Path(__file__).parent))

import yaml


# =============================================================================
# 설정 로드
# =============================================================================

def load_secrets():
    """시크릿 파일 로드"""
    path = Path('config/secrets.yaml')
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}


# =============================================================================
# 테스트 함수들
# =============================================================================

def test_broker_connection():
    """[TEST 1] 브로커 연결 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 1] 브로커 연결 테스트")
    print("=" * 60)
    
    try:
        from scalping.execution.broker import KISBroker
        
        secrets = load_secrets()
        kis = secrets.get('kis', {})
        
        if not kis.get('app_key'):
            print("❌ secrets.yaml에 KIS API 키가 없습니다")
            return False
        
        # config 딕셔너리로 전달
        broker = KISBroker(config=kis, dry_run=False)
        
        # 연결 테스트: 잔고 조회 시도
        try:
            balance = broker.get_balance()
            if balance:
                print("✅ 브로커 연결 성공!")
                print(f"   계좌번호: {kis.get('account_number', '')[:4]}****")
                print(f"   환경: {kis.get('environment', 'P')}")
                print(f"   예수금: {balance.get('available_cash', 0):,.0f}원")
                return broker
            else:
                print("⚠️ 잔고 조회 실패 (API 응답 없음)")
                return broker  # 브로커 객체는 반환
        except Exception as e:
            print(f"⚠️ 잔고 조회 실패: {e}")
            print("   (장 외 시간이면 일부 API가 제한될 수 있음)")
            return broker  # 브로커 객체는 반환
            
    except Exception as e:
        print(f"❌ 에러: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_current_price(broker):
    """[TEST 2] 현재가 조회 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 2] 현재가 조회 테스트")
    print("=" * 60)
    
    test_codes = ["005930", "000660", "035720"]  # 삼성전자, SK하이닉스, 카카오
    
    for code in test_codes:
        try:
            price = broker.get_current_price(code)
            if price > 0:
                print(f"✅ {code}: {price:,.0f}원")
            else:
                print(f"⚠️ {code}: 가격 조회 실패 (장 중 아닐 수 있음)")
        except Exception as e:
            print(f"❌ {code}: 에러 - {e}")
    
    return True


def test_minute_ohlcv(broker):
    """[TEST 3] 분봉 데이터 조회 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 3] 분봉 데이터 조회 테스트")
    print("=" * 60)
    
    try:
        code = "005930"  # 삼성전자
        minute_data = broker.get_minute_ohlcv(code, interval=1, count=5)
        
        if minute_data:
            print(f"✅ 분봉 데이터 조회 성공! ({len(minute_data)}개 봉)")
            print("\n   최근 분봉:")
            for d in minute_data[:3]:
                print(f"   {d['timestamp']} | "
                      f"O:{d['open']:,.0f} H:{d['high']:,.0f} "
                      f"L:{d['low']:,.0f} C:{d['close']:,.0f} "
                      f"V:{d['volume']:,}")
            return True
        else:
            print("⚠️ 분봉 데이터 없음 (장 중 아닐 수 있음)")
            return False
            
    except Exception as e:
        print(f"❌ 에러: {e}")
        return False


def test_daily_ohlcv(broker):
    """[TEST 4] 일봉 데이터 조회 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 4] 일봉 데이터 조회 테스트")
    print("=" * 60)
    
    try:
        code = "005930"  # 삼성전자
        daily_data = broker.get_daily_ohlcv(code, period=5)
        
        if daily_data:
            print(f"✅ 일봉 데이터 조회 성공! ({len(daily_data)}일)")
            print("\n   최근 일봉:")
            for d in daily_data[:3]:
                print(f"   {d['date']} | "
                      f"O:{d['open']:,.0f} H:{d['high']:,.0f} "
                      f"L:{d['low']:,.0f} C:{d['close']:,.0f} "
                      f"등락률:{d['change_pct']:+.2f}%")
            return True
        else:
            print("❌ 일봉 데이터 없음")
            return False
            
    except Exception as e:
        print(f"❌ 에러: {e}")
        return False


def test_index_price(broker):
    """[TEST 5] 지수 조회 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 5] 지수 조회 테스트")
    print("=" * 60)
    
    try:
        # 코스피
        kospi = broker.get_index_price('0001')
        if kospi:
            print(f"✅ KOSPI: {kospi.get('price', 0):,.2f} ({kospi.get('change_pct', 0):+.2f}%)")
        
        # 코스닥
        kosdaq = broker.get_index_price('1001')
        if kosdaq:
            print(f"✅ KOSDAQ: {kosdaq.get('price', 0):,.2f} ({kosdaq.get('change_pct', 0):+.2f}%)")
        
        return True
        
    except Exception as e:
        print(f"❌ 에러: {e}")
        return False


def test_naver_news():
    """[TEST 6] 네이버 뉴스 API 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 6] 네이버 뉴스 API 테스트")
    print("=" * 60)
    
    try:
        from scalping.data.premarket_analyzer import NewsCollector
        
        secrets = load_secrets()
        naver = secrets.get('naver', {})
        
        # secrets.yaml 또는 환경변수에서 로드
        client_id = (
            naver.get('client_id') or
            os.environ.get('NAVER_CLIENT_ID') or
            os.environ.get('NaverAPI_Client_ID') or
            ''
        )
        client_secret = (
            naver.get('client_secret') or
            os.environ.get('NAVER_CLIENT_SECRET') or
            os.environ.get('NaverAPI_Client_Secret') or
            ''
        )
        
        if not client_id:
            print("❌ 네이버 API 키가 없습니다")
            print("\n   방법 1: secrets.yaml에 추가")
            print("   naver:")
            print("     client_id: 'YOUR_CLIENT_ID'")
            print("     client_secret: 'YOUR_CLIENT_SECRET'")
            print("\n   방법 2: 환경변수 설정")
            print("   set NaverAPI_Client_ID=YOUR_ID")
            print("   set NaverAPI_Client_Secret=YOUR_SECRET")
            return False
        
        collector = NewsCollector(
            client_id=client_id,
            client_secret=client_secret,
        )
        
        # 테스트 검색
        test_stocks = ["삼성전자", "SK하이닉스"]
        
        for stock in test_stocks:
            news = collector.search_naver_news(stock, display=3)
            
            if news:
                print(f"\n✅ '{stock}' 뉴스 {len(news)}건:")
                for n in news[:2]:
                    print(f"   - {n['title'][:50]}...")
            else:
                print(f"⚠️ '{stock}' 뉴스 없음")
        
        return True
        
    except Exception as e:
        print(f"❌ 에러: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_condition_search(broker):
    """[TEST 7] 조건검색 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 7] 조건검색 테스트")
    print("=" * 60)
    
    try:
        secrets = load_secrets()
        hts_id = secrets.get('kis', {}).get('hts_id', '')
        
        if not hts_id:
            print("⚠️ HTS ID가 설정되지 않음 (조건검색 불가)")
            print("   secrets.yaml의 kis.hts_id를 설정하세요")
            return False
        
        # 조건식 목록 조회
        conditions = broker.get_condition_list(hts_id)
        
        if conditions:
            print(f"✅ 조건식 {len(conditions)}개 발견:")
            for c in conditions[:5]:
                print(f"   - [{c['seq']}] {c['name']}")
            return True
        else:
            print("⚠️ 조건식 없음 (HTS에서 먼저 설정 필요)")
            return False
            
    except Exception as e:
        print(f"❌ 에러: {e}")
        return False


def test_minute_indicators():
    """[TEST 8] 분봉 지표 계산 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 8] 분봉 지표 계산 테스트")
    print("=" * 60)
    
    try:
        from scalping.strategy.minute_indicators import MinuteIndicators, OHLCV
        import random
        
        # 테스트 데이터 생성
        calc = MinuteIndicators(prev_close=10000)
        
        # 20개 봉 시뮬레이션
        price = 10000
        for i in range(20):
            change = random.uniform(-50, 70)
            price += change
            
            candle = OHLCV(
                timestamp=f"09:{i+5:02d}",
                open=price - random.uniform(0, 30),
                high=price + random.uniform(0, 50),
                low=price - random.uniform(0, 50),
                close=price,
                volume=random.randint(10000, 50000),
            )
            
            result = calc.update(candle)
        
        print("✅ 분봉 지표 계산 성공!")
        print(f"   CCI: {result.cci:.1f}")
        print(f"   RSI: {result.rsi:.1f}")
        print(f"   VWAP: {result.vwap:,.0f}")
        print(f"   거래량비: {result.volume_ratio:.2f}x")
        print(f"   당일등락률: {result.day_change_pct:+.2f}%")
        
        return True
        
    except Exception as e:
        print(f"❌ 에러: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_scalp_signals():
    """[TEST 9] 스캘핑 시그널 테스트"""
    print("\n" + "=" * 60)
    print("[TEST 9] 스캘핑 시그널 테스트")
    print("=" * 60)
    
    try:
        from scalping.strategy.scalp_signals import (
            ScalpSignalGenerator, MarketContext
        )
        from scalping.strategy.minute_indicators import MinuteIndicatorResult
        from datetime import time
        
        # 시그널 생성기
        gen = ScalpSignalGenerator({
            'trading': {
                'stop_loss': -0.7,
                'take_profit_1': 1.5,
                'min_score': 55,
            }
        })
        
        # 테스트 케이스: 돌파 시나리오
        indicators = MinuteIndicatorResult(
            timestamp='09:15',
            price=10500,
            cci=150,
            rsi=65,
            vwap=10300,
            vwap_distance=1.94,
            volume_ratio=2.5,
            day_high=10500,
            day_change_pct=3.0,
            from_day_high_pct=0,
            is_bullish=True,
            body_ratio=0.7,
        )
        
        context = MarketContext(
            prev_close=10200,
            prev_high=10400,
            current_time=time(9, 15),
        )
        
        signal = gen.evaluate("005930", indicators, context)
        
        print(f"✅ 시그널 생성 성공!")
        print(f"   판정: {signal.action}")
        print(f"   타입: {signal.signal_type.value}")
        print(f"   점수: {signal.score:.0f}")
        print(f"   이유: {signal.reason}")
        
        if signal.action == "BUY":
            print(f"   손절가: {signal.stop_loss:,.0f}")
            print(f"   익절가: {signal.take_profit_1:,.0f}")
        
        return True
        
    except Exception as e:
        print(f"❌ 에러: {e}")
        import traceback
        traceback.print_exc()
        return False


# =============================================================================
# 메인
# =============================================================================

def main():
    print("=" * 60)
    print("ScalpingBot v3.0 - 프리마켓 데이터 수집 테스트")
    print("=" * 60)
    print(f"테스트 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {}
    
    # 1. 브로커 연결
    broker = test_broker_connection()
    results['broker'] = broker is not None
    
    if broker:
        # 2. 현재가 조회
        results['current_price'] = test_current_price(broker)
        
        # 3. 분봉 데이터
        results['minute_ohlcv'] = test_minute_ohlcv(broker)
        
        # 4. 일봉 데이터
        results['daily_ohlcv'] = test_daily_ohlcv(broker)
        
        # 5. 지수 조회
        results['index'] = test_index_price(broker)
        
        # 7. 조건검색
        results['condition'] = test_condition_search(broker)
    
    # 6. 네이버 뉴스
    results['naver_news'] = test_naver_news()
    
    # 8. 분봉 지표
    results['indicators'] = test_minute_indicators()
    
    # 9. 스캘핑 시그널
    results['signals'] = test_scalp_signals()
    
    # 결과 요약
    print("\n" + "=" * 60)
    print("📋 테스트 결과 요약")
    print("=" * 60)
    
    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"   {name:<20}: {status}")
    
    total_pass = sum(1 for v in results.values() if v)
    total_tests = len(results)
    
    print("=" * 60)
    print(f"결과: {total_pass}/{total_tests} 통과")
    print("=" * 60)
    
    if total_pass == total_tests:
        print("\n🎉 모든 테스트 통과! 스캘핑 준비 완료!")
    else:
        print("\n⚠️ 일부 테스트 실패. 위 로그를 확인하세요.")


if __name__ == '__main__':
    main()
