#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScalpingBot v3.0 - 프리마켓 분석 테스트
장 외 시간에도 뉴스 수집 + AI 분석 테스트 가능
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

# 상위 디렉토리를 path에 추가
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

import yaml


def load_secrets():
    """secrets.yaml 로드"""
    path = ROOT_DIR / 'config' / 'secrets.yaml'
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}


def test_news_collection():
    """뉴스 수집 테스트"""
    print("\n" + "=" * 60)
    print("[1] 뉴스 수집 테스트")
    print("=" * 60)
    
    from scalping.data.premarket_analyzer import NewsCollector
    
    secrets = load_secrets()
    naver = secrets.get('naver', {})
    
    client_id = (
        naver.get('client_id') or
        os.environ.get('NaverAPI_Client_ID') or
        ''
    )
    client_secret = (
        naver.get('client_secret') or
        os.environ.get('NaverAPI_Client_Secret') or
        ''
    )
    
    if not client_id:
        print("❌ 네이버 API 키 없음")
        return None
    
    collector = NewsCollector(client_id=client_id, client_secret=client_secret)
    
    # 테스트 종목
    test_stocks = ["삼성전자", "SK하이닉스", "현대차", "NAVER", "카카오"]
    
    all_news = {}
    
    for stock in test_stocks:
        news = collector.collect_stock_news(stock, count=3)
        all_news[stock] = news
        
        print(f"\n📰 [{stock}] 뉴스 {len(news)}건:")
        for n in news[:2]:
            title = n['title'][:45] + "..." if len(n['title']) > 45 else n['title']
            print(f"   - {title}")
    
    return all_news


def test_gemini_analysis(news_data: dict):
    """Gemini AI 분석 테스트"""
    print("\n" + "=" * 60)
    print("[2] Gemini AI 분석 테스트")
    print("=" * 60)
    
    secrets = load_secrets()
    gemini_key = secrets.get('gemini', {}).get('api_key', '')
    
    if not gemini_key:
        print("❌ Gemini API 키 없음")
        return None
    
    try:
        import requests
        
        # 프롬프트 생성
        prompt = """당신은 한국 주식 스캘핑 전문 트레이더입니다.

아래 종목별 뉴스를 분석하고, 오늘 스캘핑 대상으로 적합한 종목 순위를 매겨주세요.

평가 기준:
1. 재료 (호재성 뉴스 여부)
2. 시장 관심도 (테마성)
3. 리스크 (악재 가능성)

"""
        
        for stock, news_list in news_data.items():
            prompt += f"\n### {stock}\n"
            for n in news_list:
                prompt += f"- {n['title']}\n"
        
        prompt += """

다음 JSON 형식으로 응답해주세요:
```json
{
  "ranking": [
    {"rank": 1, "name": "종목명", "score": 85, "reason": "선정 이유"},
    {"rank": 2, "name": "종목명", "score": 75, "reason": "선정 이유"}
  ],
  "avoid": [
    {"name": "종목명", "reason": "피해야 할 이유"}
  ],
  "summary": "전체 시황 요약 한 문장"
}
```
"""
        
        print("\n🤖 Gemini 분석 중...")
        
        # REST API 직접 호출 (라이브러리 의존성 없음)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}"
        
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 1000,
            }
        }
        
        response = requests.post(url, json=payload, timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            result = data['candidates'][0]['content']['parts'][0]['text']
            
            print("\n📊 AI 분석 결과:")
            print("-" * 40)
            print(result)
            
            return result
        else:
            print(f"❌ API 에러: {response.status_code}")
            print(response.text[:200])
            return None
        
    except Exception as e:
        print(f"❌ Gemini 에러: {e}")
        return None


def test_full_premarket():
    """전체 프리마켓 분석 시뮬레이션"""
    print("\n" + "=" * 60)
    print("[3] 프리마켓 분석 시뮬레이션")
    print("=" * 60)
    
    print(f"\n📅 현재 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n실제 장 시작 전 (08:00~08:50)에는:")
    print("  1. 거래량 상위 종목 자동 추출")
    print("  2. 종목별 뉴스 수집")
    print("  3. AI 유니버스 선정")
    print("  4. 시나리오별 대응 전략 생성")
    print("\n지금은 장 외 시간이므로 테스트 데이터로 시뮬레이션합니다.")
    
    # 테스트 종목 (실제로는 거래량 상위에서 가져옴)
    test_universe = [
        {"code": "005930", "name": "삼성전자", "change": "+2.96%", "volume_ratio": "2.1x"},
        {"code": "000660", "name": "SK하이닉스", "change": "+1.52%", "volume_ratio": "1.8x"},
        {"code": "035720", "name": "카카오", "change": "+3.21%", "volume_ratio": "2.5x"},
        {"code": "035420", "name": "NAVER", "change": "+1.87%", "volume_ratio": "1.6x"},
        {"code": "005380", "name": "현대차", "change": "+0.95%", "volume_ratio": "1.4x"},
    ]
    
    print("\n📋 테스트 유니버스 (거래량 상위 시뮬레이션):")
    print("-" * 50)
    for s in test_universe:
        print(f"  {s['name']:12} | {s['change']:>7} | 거래량 {s['volume_ratio']}")
    
    return test_universe


def main():
    print("=" * 60)
    print("ScalpingBot v3.0 - 프리마켓 분석 테스트")
    print("=" * 60)
    print(f"테스트 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 1. 뉴스 수집 테스트
    news_data = test_news_collection()
    
    if not news_data:
        print("\n⚠️ 뉴스 수집 실패 - 테스트 중단")
        return
    
    # 2. Gemini 분석 테스트
    ai_result = test_gemini_analysis(news_data)
    
    # 3. 전체 시뮬레이션
    test_full_premarket()
    
    # 결과 요약
    print("\n" + "=" * 60)
    print("📋 테스트 결과 요약")
    print("=" * 60)
    print(f"  뉴스 수집: {'✅ PASS' if news_data else '❌ FAIL'}")
    print(f"  AI 분석:   {'✅ PASS' if ai_result else '❌ FAIL'}")
    print("\n💡 내일 08:00에 run_scalp_v3.bat 실행하면")
    print("   실제 거래량 상위 종목으로 분석이 진행됩니다!")


if __name__ == '__main__':
    main()
