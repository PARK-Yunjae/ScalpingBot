#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v3.0 - Pre-Market Analyzer (장 시작 전 분석)
============================================================================
장 시작 전에 필요한 모든 데이터를 수집하고 AI 분석을 수행합니다.

타임라인:
- 08:00 - 프로그램 시작, 전일 데이터 로드
- 08:10 - 거래량 상위 종목 추출
- 08:15 - 뉴스/공시 수집
- 08:20 - 섹터별 수급 분석
- 08:30 - AI 유니버스 선정
- 08:50 - 갭 분석 (시초가 형성 후)
- 09:05 - 스캘핑 시작

사용법:
    analyzer = PreMarketAnalyzer(config, broker)
    result = await analyzer.run_full_analysis()
============================================================================
"""

import os
import re
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, time
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
import asyncio

logger = logging.getLogger('ScalpingBot.PreMarket')


# =============================================================================
# 설정
# =============================================================================

# 네이버 API 키 (환경변수 또는 secrets.yaml에서 로드)
# 여러 환경변수명 지원
NAVER_CLIENT_ID = (
    os.environ.get("NAVER_CLIENT_ID") or 
    os.environ.get("NaverAPI_Client_ID") or 
    ""
)
NAVER_CLIENT_SECRET = (
    os.environ.get("NAVER_CLIENT_SECRET") or 
    os.environ.get("NaverAPI_Client_Secret") or 
    ""
)

# 분석 설정
VOLUME_TOP_COUNT = 50          # 거래량 상위 N개
NEWS_PER_STOCK = 5             # 종목당 뉴스 N개
MIN_MARKET_CAP = 50_000_000_000   # 최소 시총 500억
MAX_MARKET_CAP = 3_000_000_000_000  # 최대 시총 3조


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class StockInfo:
    """종목 정보"""
    code: str
    name: str
    price: float = 0.0
    prev_close: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    volume_ratio: float = 1.0   # 20일 평균 대비
    market_cap: int = 0
    sector: str = ""
    
    # 차트 정보
    ma5: float = 0.0
    ma20: float = 0.0
    ma5_distance: float = 0.0   # 5일선 이격도
    ma20_distance: float = 0.0  # 20일선 이격도
    from_52w_high: float = 0.0  # 52주 고가 대비
    
    # 뉴스
    news: List[Dict] = field(default_factory=list)
    news_summary: str = ""
    
    # AI 분석 결과
    ai_score: float = 0.0
    ai_analysis: Dict = field(default_factory=dict)


@dataclass
class SectorFlow:
    """섹터별 수급"""
    sector: str
    foreign_net: int = 0        # 외국인 순매수
    institution_net: int = 0    # 기관 순매수
    change_pct: float = 0.0     # 섹터 등락률
    is_hot: bool = False        # 주목 섹터 여부


@dataclass
class PreMarketResult:
    """장 시작 전 분석 결과"""
    timestamp: datetime = None
    
    # 선정된 종목
    selected_stocks: List[StockInfo] = field(default_factory=list)
    avoid_stocks: List[Tuple[str, str]] = field(default_factory=list)  # (종목명, 이유)
    
    # 시장 요약
    market_summary: str = ""
    hot_sectors: List[str] = field(default_factory=list)
    risk_sectors: List[str] = field(default_factory=list)
    
    # 시나리오
    scenarios: Dict[str, Dict] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'selected_stocks': [s.__dict__ for s in self.selected_stocks],
            'avoid_stocks': self.avoid_stocks,
            'market_summary': self.market_summary,
            'hot_sectors': self.hot_sectors,
            'risk_sectors': self.risk_sectors,
            'scenarios': self.scenarios,
        }


# =============================================================================
# 뉴스 수집기
# =============================================================================

class NewsCollector:
    """뉴스 수집기"""
    
    def __init__(self, client_id: str = "", client_secret: str = ""):
        self.client_id = client_id or NAVER_CLIENT_ID
        self.client_secret = client_secret or NAVER_CLIENT_SECRET
    
    def search_naver_news(
        self,
        query: str,
        display: int = 10,
        sort: str = "date",
    ) -> List[Dict]:
        """
        네이버 뉴스 검색
        
        Args:
            query: 검색어
            display: 결과 개수 (최대 100)
            sort: 정렬 (date: 최신순, sim: 정확도순)
        
        Returns:
            뉴스 리스트
        """
        if not self.client_id or not self.client_secret:
            logger.warning("네이버 API 키가 설정되지 않음")
            return []
        
        try:
            enc_query = urllib.parse.quote(query)
            url = f"https://openapi.naver.com/v1/search/news.json?query={enc_query}&display={display}&sort={sort}"
            
            request = urllib.request.Request(url)
            request.add_header("X-Naver-Client-Id", self.client_id)
            request.add_header("X-Naver-Client-Secret", self.client_secret)
            
            response = urllib.request.urlopen(request, timeout=10)
            
            if response.getcode() == 200:
                data = json.loads(response.read().decode('utf-8'))
                
                news_list = []
                for item in data.get('items', []):
                    news = {
                        'title': self._clean_html(item.get('title', '')),
                        'description': self._clean_html(item.get('description', '')),
                        'link': item.get('link', ''),
                        'pub_date': item.get('pubDate', ''),
                    }
                    news_list.append(news)
                
                return news_list
            
        except Exception as e:
            logger.error(f"뉴스 검색 실패 ({query}): {e}")
        
        return []
    
    def collect_stock_news(
        self,
        stock_name: str,
        count: int = NEWS_PER_STOCK,
    ) -> List[Dict]:
        """종목 관련 뉴스 수집"""
        # 검색어 조합
        queries = [
            stock_name,
            f"{stock_name} 주가",
            f"{stock_name} 실적",
        ]
        
        all_news = []
        seen_titles = set()
        
        for q in queries:
            news = self.search_naver_news(q, display=count)
            for n in news:
                # 중복 제거
                if n['title'] not in seen_titles:
                    seen_titles.add(n['title'])
                    all_news.append(n)
            
            if len(all_news) >= count:
                break
        
        return all_news[:count]
    
    def _clean_html(self, text: str) -> str:
        """HTML 태그 제거"""
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace('&quot;', '"')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        return text.strip()


# =============================================================================
# 장 시작 전 분석기
# =============================================================================

class PreMarketAnalyzer:
    """
    장 시작 전 분석기
    
    거래량 상위 종목을 추출하고, 뉴스/수급/차트를 분석하여
    AI에게 유니버스 선정을 요청합니다.
    """
    
    def __init__(
        self,
        config: Dict[str, Any],
        broker = None,
        ai_engine = None,
    ):
        self.config = config
        self.broker = broker
        self.ai_engine = ai_engine
        self.news_collector = NewsCollector()
        
        # 결과 저장
        self._result: Optional[PreMarketResult] = None
        
        logger.info("PreMarketAnalyzer 초기화")
    
    async def run_full_analysis(self) -> PreMarketResult:
        """
        전체 분석 실행
        
        Returns:
            PreMarketResult
        """
        logger.info("=" * 60)
        logger.info("📊 장 시작 전 분석 시작")
        logger.info("=" * 60)
        
        result = PreMarketResult(timestamp=datetime.now())
        
        try:
            # 1. 거래량 상위 종목 추출
            logger.info("\n[1/5] 거래량 상위 종목 추출...")
            volume_top = await self._get_volume_top_stocks()
            logger.info(f"   → {len(volume_top)}개 종목 추출")
            
            # 2. 뉴스 수집
            logger.info("\n[2/5] 뉴스/공시 수집...")
            for stock in volume_top:
                stock.news = self.news_collector.collect_stock_news(stock.name)
                logger.debug(f"   - {stock.name}: 뉴스 {len(stock.news)}건")
            
            # 3. 차트 분석 (이격도, 지지/저항)
            logger.info("\n[3/5] 차트 분석...")
            await self._analyze_charts(volume_top)
            
            # 4. 섹터 수급 분석
            logger.info("\n[4/5] 섹터 수급 분석...")
            sector_flows = await self._analyze_sector_flows()
            result.hot_sectors = [s.sector for s in sector_flows if s.is_hot]
            
            # 5. AI 유니버스 선정
            logger.info("\n[5/5] AI 유니버스 선정...")
            ai_result = await self._run_ai_analysis(volume_top, sector_flows)
            
            result.selected_stocks = ai_result.get('selected', [])
            result.avoid_stocks = ai_result.get('avoid', [])
            result.market_summary = ai_result.get('market_summary', '')
            result.scenarios = ai_result.get('scenarios', {})
            
            logger.info("\n" + "=" * 60)
            logger.info(f"✅ 분석 완료: {len(result.selected_stocks)}개 종목 선정")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error(f"분석 실패: {e}")
            import traceback
            traceback.print_exc()
        
        self._result = result
        return result
    
    async def _get_volume_top_stocks(self) -> List[StockInfo]:
        """거래량 상위 종목 조회"""
        stocks = []
        
        if not self.broker:
            logger.warning("브로커 없음 - 더미 데이터 반환")
            return stocks
        
        try:
            # 조건검색 또는 거래량 상위 조회
            # 실제 구현 시 broker.get_volume_ranking() 호출
            
            # TODO: 실제 구현
            pass
            
        except Exception as e:
            logger.error(f"거래량 상위 조회 실패: {e}")
        
        return stocks
    
    async def _analyze_charts(self, stocks: List[StockInfo]):
        """차트 분석 (이격도 등)"""
        for stock in stocks:
            try:
                if not self.broker:
                    continue
                
                # 일봉 데이터 조회
                ohlcv = self.broker.get_daily_ohlcv(stock.code, period=60)
                
                if not ohlcv:
                    continue
                
                # 이동평균 계산
                closes = [d['close'] for d in ohlcv]
                if len(closes) >= 5:
                    stock.ma5 = sum(closes[:5]) / 5
                    stock.ma5_distance = (stock.price - stock.ma5) / stock.ma5 * 100
                
                if len(closes) >= 20:
                    stock.ma20 = sum(closes[:20]) / 20
                    stock.ma20_distance = (stock.price - stock.ma20) / stock.ma20 * 100
                
                # 52주 고가 대비
                if len(closes) >= 250:
                    high_52w = max(d['high'] for d in ohlcv[:250])
                    stock.from_52w_high = (stock.price - high_52w) / high_52w * 100
                
            except Exception as e:
                logger.debug(f"차트 분석 실패 ({stock.code}): {e}")
    
    async def _analyze_sector_flows(self) -> List[SectorFlow]:
        """섹터별 수급 분석"""
        # TODO: 섹터별 외국인/기관 순매수 조회
        # 한투 API 또는 네이버 금융에서 크롤링
        return []
    
    async def _run_ai_analysis(
        self,
        stocks: List[StockInfo],
        sector_flows: List[SectorFlow],
    ) -> Dict[str, Any]:
        """
        AI 유니버스 선정
        
        프롬프트 기반으로 종목 분석 및 선정
        """
        if not self.ai_engine:
            logger.warning("AI 엔진 없음 - 규칙 기반 선정")
            return self._rule_based_selection(stocks)
        
        # 프롬프트 생성
        prompt = self._build_ai_prompt(stocks, sector_flows)
        
        try:
            # AI 호출
            response = await self.ai_engine.generate(prompt)
            
            # JSON 파싱
            result = self._parse_ai_response(response)
            return result
            
        except Exception as e:
            logger.error(f"AI 분석 실패: {e}")
            return self._rule_based_selection(stocks)
    
    def _build_ai_prompt(
        self,
        stocks: List[StockInfo],
        sector_flows: List[SectorFlow],
    ) -> str:
        """AI 프롬프트 생성"""
        
        # 종목 데이터 포맷
        stock_data = []
        for s in stocks[:30]:  # 상위 30개만
            stock_data.append({
                'code': s.code,
                'name': s.name,
                'price': s.price,
                'change_pct': f"{s.change_pct:+.2f}%",
                'volume_ratio': f"{s.volume_ratio:.1f}x",
                'ma5_distance': f"{s.ma5_distance:+.1f}%",
                'ma20_distance': f"{s.ma20_distance:+.1f}%",
                'news_count': len(s.news),
                'top_news': s.news[0]['title'] if s.news else "뉴스 없음",
            })
        
        # 섹터 데이터 포맷
        sector_data = []
        for sf in sector_flows:
            sector_data.append({
                'sector': sf.sector,
                'foreign': f"{sf.foreign_net:+,}억",
                'institution': f"{sf.institution_net:+,}억",
                'change': f"{sf.change_pct:+.2f}%",
            })
        
        prompt = f"""# Role: 단타 전문 트레이더 (스캘핑)

너는 한국 주식시장에서 스캘핑(초단타)을 전문으로 하는 트레이더다.
오늘 장에서 1~3% 수익을 목표로 빠르게 치고 빠지는 전략을 사용한다.
장 시작 전에 "오늘 집중 감시할 종목"을 선별하는 것이 임무다.

# Input Data

## 전일 거래량 상위 종목 (상위 {len(stock_data)}개)
```json
{json.dumps(stock_data, ensure_ascii=False, indent=2)}
```

## 섹터별 수급 동향
```json
{json.dumps(sector_data, ensure_ascii=False, indent=2)}
```

# Task

아래 4가지 기준으로 각 종목을 분석하고, 오늘 스캘핑 대상으로 적합한 종목을 선별해라.

## 분석 기준

### 1. 재료 (Material) - 30점
- 최근 3일 내 호재성 뉴스/공시가 있는가?
- 뉴스가 "이미 반영된 것"인가, "아직 반영 중"인가?
- 테마성 이슈와 연결되는가? (정책, 계절, 이벤트)
- 루머/찌라시 vs 공식 발표 구분

### 2. 시황 (Market Sentiment) - 25점
- 해당 종목의 섹터가 현재 시장에서 주목받고 있는가?
- 외국인/기관 수급이 들어오는 섹터인가?
- 미국/중국 시장에서 관련 섹터 동향은?
- 오늘 특별한 이벤트(FOMC, 실적발표 등)가 있는가?

### 3. 거래량 (Volume) - 25점
- 전일 거래량이 평소 대비 몇 배인가?
- 거래량 증가가 "세력 매집"인가 "개미 추격"인가?
- 시간외 거래량은 어떤가?

### 4. 차트 (Chart) - 20점
- 현재 위치가 바닥권/중간/고점 중 어디인가?
- 주요 지지선/저항선은?
- 5일선, 20일선 대비 위치
- 최근 급등 후 눌림목인가, 하락 후 반등 시도인가?

# Output Format

반드시 아래 JSON 형식으로만 응답해라:

```json
{{
  "selected": [
    {{
      "rank": 1,
      "code": "종목코드",
      "name": "종목명",
      "total_score": 85,
      "scores": {{
        "material": 25,
        "sentiment": 22,
        "volume": 23,
        "chart": 15
      }},
      "material_summary": "재료 한줄 요약",
      "sentiment_summary": "시황 한줄 요약",
      "volume_summary": "거래량 한줄 요약",
      "chart_summary": "차트 한줄 요약",
      "scenarios": {{
        "gap_up": "갭상승 시 대응 방법",
        "flat": "보합 시작 시 대응 방법",
        "gap_down": "갭하락 시 대응 방법"
      }},
      "risk": "주의사항"
    }}
  ],
  "avoid": [
    {{"name": "종목명", "reason": "피해야 할 이유"}}
  ],
  "market_summary": "오늘의 시황 요약 (주도 섹터, 주의 섹터, 특이사항)"
}}
```

상위 5개 종목만 선정하고, 피해야 할 종목은 2~3개만 명시해라.
"""
        return prompt
    
    def _parse_ai_response(self, response: str) -> Dict[str, Any]:
        """AI 응답 파싱"""
        try:
            # JSON 블록 추출
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # JSON 블록 없으면 전체에서 시도
                json_str = response
            
            result = json.loads(json_str)
            return result
            
        except Exception as e:
            logger.error(f"AI 응답 파싱 실패: {e}")
            return {'selected': [], 'avoid': [], 'market_summary': ''}
    
    def _rule_based_selection(self, stocks: List[StockInfo]) -> Dict[str, Any]:
        """규칙 기반 종목 선정 (AI 실패 시 백업)"""
        selected = []
        
        for stock in stocks[:10]:
            # 기본 점수
            score = 50
            
            # 거래량 비율 가산
            if stock.volume_ratio >= 3.0:
                score += 15
            elif stock.volume_ratio >= 2.0:
                score += 10
            
            # 이격도 (5일선 근접이 좋음)
            if -2 <= stock.ma5_distance <= 5:
                score += 10
            
            # 뉴스 있으면 가산
            if stock.news:
                score += 10
            
            stock.ai_score = score
            selected.append(stock)
        
        # 점수순 정렬
        selected.sort(key=lambda x: x.ai_score, reverse=True)
        
        return {
            'selected': selected[:5],
            'avoid': [],
            'market_summary': '규칙 기반 선정 (AI 미사용)',
        }
    
    def get_result(self) -> Optional[PreMarketResult]:
        """최근 분석 결과 반환"""
        return self._result


# =============================================================================
# 타임라인 스케줄러
# =============================================================================

class PreMarketScheduler:
    """
    장 시작 전 스케줄러
    
    타임라인:
    - 08:00 - 시작
    - 08:10 - 거래량 상위 추출
    - 08:15 - 뉴스 수집
    - 08:20 - 섹터 분석
    - 08:30 - AI 분석
    - 08:50 - 갭 분석
    - 09:05 - 스캘핑 시작
    """
    
    SCHEDULE = {
        time(8, 0): "start",
        time(8, 10): "volume_top",
        time(8, 15): "news",
        time(8, 20): "sector",
        time(8, 30): "ai_analysis",
        time(8, 50): "gap_analysis",
        time(9, 5): "scalping_start",
    }
    
    def __init__(self, analyzer: PreMarketAnalyzer):
        self.analyzer = analyzer
        self._running = False
    
    async def run(self):
        """스케줄 실행"""
        self._running = True
        logger.info("📅 프리마켓 스케줄러 시작")
        
        while self._running:
            now = datetime.now().time()
            
            for scheduled_time, task in self.SCHEDULE.items():
                if now.hour == scheduled_time.hour and now.minute == scheduled_time.minute:
                    await self._execute_task(task)
            
            await asyncio.sleep(30)  # 30초마다 체크
    
    async def _execute_task(self, task: str):
        """태스크 실행"""
        logger.info(f"⏰ [{task}] 실행")
        
        if task == "start":
            logger.info("프리마켓 분석 준비")
        
        elif task == "ai_analysis":
            await self.analyzer.run_full_analysis()
        
        elif task == "scalping_start":
            logger.info("🚀 스캘핑 시작!")
            self._running = False
    
    def stop(self):
        """스케줄러 중지"""
        self._running = False


# =============================================================================
# 테스트
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("PreMarketAnalyzer 테스트")
    print("=" * 60)
    
    # 뉴스 수집 테스트
    print("\n1. 뉴스 수집 테스트")
    collector = NewsCollector()
    
    test_stocks = ["삼성전자", "SK하이닉스", "현대차"]
    
    for stock in test_stocks:
        news = collector.collect_stock_news(stock, count=3)
        print(f"\n   [{stock}] 뉴스 {len(news)}건")
        for n in news[:2]:
            print(f"      - {n['title'][:40]}...")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
