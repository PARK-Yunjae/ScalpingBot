#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Universe Filter (유니버스 필터)
============================================================================
거래대금 상위 종목을 필터링하여 스캔 대상 유니버스 구성

데이터 소스 우선순위:
1. 한투 API 조건검색 (TV100) - 가장 안정적
2. KRX 거래대금 상위 조회 - 백업
3. 네이버 금융 크롤링 - 최후 수단

필터링 단계:
1. 조건검색/크롤링으로 거래대금 상위 조회
2. 등락률 필터 (-5% ~ +15%)
3. 가격 필터 (1,000원 ~ 500,000원)
4. 제외 패턴 필터 (ETF, ETN 등)
5. 최종 유니버스 구성

사용법:
    # 브로커 없이 (KRX fallback)
    filter = UniverseFilter()
    universe = filter.get_universe(target_size=100)
    
    # 브로커와 함께 (조건검색 사용)
    filter = UniverseFilter(broker=broker, hts_id="사용자ID")
    universe = filter.get_universe(condition_name="TV100")
============================================================================
"""

import time
import logging
import requests
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING
from datetime import datetime, date
from dataclasses import dataclass
import pandas as pd

# 타입 힌트용 (순환 import 방지)
if TYPE_CHECKING:
    from scalping.execution.broker import KISBroker

# 로거 설정
logger = logging.getLogger('ScalpingBot.Universe')


# =============================================================================
# 상수 설정
# =============================================================================

# 필터 기본값
DEFAULT_TOP_N = 200                # 거래대금 상위 N개
DEFAULT_TARGET_SIZE = 100          # 최종 유니버스 크기
DEFAULT_MIN_PRICE = 1000           # 최소 가격
DEFAULT_MAX_PRICE = 500000         # 최대 가격
DEFAULT_MIN_CHANGE = -5.0          # 최소 등락률 (%)
DEFAULT_MAX_CHANGE = 15.0          # 최대 등락률 (%)
DEFAULT_MIN_VOLUME = 100000        # 최소 거래량

# KRX API URL
KRX_DATA_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

# 제외 종목 패턴 (ETF, ETN, 스팩, 우선주 등)
EXCLUDE_PATTERNS = ['ETF', 'ETN', 'KODEX', 'TIGER', 'KBSTAR', 'ARIRANG', 
                    'HANARO', 'SOL', '스팩', 'SPAC', '리츠', 'RISE', 'ACE',
                    '우', '우B', '1우', '2우', '3우', '우선주']

# User-Agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# =============================================================================
# 종목 정보 데이터 클래스
# =============================================================================

@dataclass
class StockInfo:
    """종목 정보"""
    stock_code: str
    stock_name: str
    current_price: float = 0
    change_pct: float = 0
    volume: int = 0
    trade_value: float = 0      # 거래대금 (억원)
    market_cap: float = 0       # 시가총액 (억원)
    market: str = ""            # KOSPI / KOSDAQ
    
    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'current_price': self.current_price,
            'change_pct': self.change_pct,
            'volume': self.volume,
            'trade_value': self.trade_value,
            'market_cap': self.market_cap,
            'market': self.market,
        }


# =============================================================================
# 유니버스 필터 클래스
# =============================================================================

class UniverseFilter:
    """
    유니버스 필터
    
    거래대금 상위 종목을 필터링하여
    스캔 대상 종목 리스트를 구성합니다.
    
    데이터 소스 우선순위:
    1. 한투 API 조건검색 (TV100)
    2. KRX 거래대금 상위
    3. 네이버 금융 (fallback)
    """
    
    def __init__(
        self,
        broker: Optional["KISBroker"] = None,
        hts_id: Optional[str] = None,
        target_size: int = DEFAULT_TARGET_SIZE,
        min_price: float = DEFAULT_MIN_PRICE,
        max_price: float = DEFAULT_MAX_PRICE,
        min_change: float = DEFAULT_MIN_CHANGE,
        max_change: float = DEFAULT_MAX_CHANGE,
        min_volume: int = DEFAULT_MIN_VOLUME,
        exclude_patterns: List[str] = None,
    ):
        """
        초기화
        
        Args:
            broker: KISBroker 인스턴스 (조건검색용)
            hts_id: HTS 사용자 ID (조건검색용)
            target_size: 최종 유니버스 크기
            min_price: 최소 가격
            max_price: 최대 가격
            min_change: 최소 등락률 (%)
            max_change: 최대 등락률 (%)
            min_volume: 최소 거래량
            exclude_patterns: 제외 패턴 리스트
        """
        self.broker = broker
        self.hts_id = hts_id
        self.target_size = target_size
        self.min_price = min_price
        self.max_price = max_price
        self.min_change = min_change
        self.max_change = max_change
        self.min_volume = min_volume
        self.exclude_patterns = exclude_patterns or EXCLUDE_PATTERNS
        
        # 캐시
        self._cache: List[StockInfo] = []
        self._cache_time: float = 0
        self._cache_ttl: float = 300  # 5분
        
        # 통계
        self._stats = {
            'source': '',           # 데이터 소스
            'total_fetched': 0,
            'after_filter': 0,
            'final_size': 0,
        }
        
        source_info = "조건검색" if (broker and hts_id) else "KRX/네이버"
        logger.info(
            f"UniverseFilter 초기화 "
            f"(데이터소스: {source_info}, 목표: {target_size}개)"
        )
    
    # =========================================================================
    # 유니버스 구성 (메인 API)
    # =========================================================================
    
    def get_universe(
        self,
        condition_name: str = "TV100",
        target_size: int = None,
        use_cache: bool = True,
    ) -> List[str]:
        """
        유니버스 구성 (종목 코드만)
        
        Args:
            condition_name: 조건검색 조건식 이름 (기본: TV100)
            target_size: 목표 크기 (None이면 기본값)
            use_cache: 캐시 사용 여부
        
        Returns:
            종목 코드 리스트
        """
        stocks = self.get_universe_with_info(condition_name, target_size, use_cache)
        return [s.stock_code for s in stocks]
    
    def get_universe_with_info(
        self,
        condition_name: str = "TV100",
        target_size: int = None,
        use_cache: bool = True,
    ) -> List[StockInfo]:
        """
        유니버스 구성 (상세 정보 포함)
        
        Args:
            condition_name: 조건검색 조건식 이름 (기본: TV100)
            target_size: 목표 크기 (None이면 기본값)
            use_cache: 캐시 사용 여부
        
        Returns:
            StockInfo 리스트
        """
        target = target_size or self.target_size
        
        # 캐시 확인
        if use_cache and self._cache and time.time() - self._cache_time < self._cache_ttl:
            logger.debug(f"캐시 사용: {len(self._cache)}개")
            return self._cache[:target]
        
        stocks: List[StockInfo] = []
        
        # 1순위: 한투 API 조건검색
        if self.broker and self.hts_id:
            stocks = self._fetch_from_condition(condition_name)
            if stocks:
                self._stats['source'] = f'조건검색({condition_name})'
        
        # 2순위: KRX
        if not stocks:
            stocks = self._fetch_from_krx()
            if stocks:
                self._stats['source'] = 'KRX'
        
        # 3순위: 네이버 (최후 수단)
        if not stocks:
            stocks = self._fetch_from_naver()
            if stocks:
                self._stats['source'] = '네이버'
        
        if not stocks:
            logger.error("모든 데이터 소스에서 유니버스 조회 실패")
            return self._cache[:target] if self._cache else []
        
        self._stats['total_fetched'] = len(stocks)
        
        # 필터링 (조건검색 결과도 추가 필터링)
        filtered = self._apply_filters(stocks)
        self._stats['after_filter'] = len(filtered)
        
        # 거래대금 순 정렬
        filtered.sort(key=lambda x: -x.trade_value if x.trade_value > 0 else 0)
        
        # 목표 크기로 자르기
        result = filtered[:target]
        
        # 캐시 저장
        self._cache = result
        self._cache_time = time.time()
        
        self._stats['final_size'] = len(result)
        
        logger.info(
            f"유니버스 구성 완료: {len(result)}개 종목 "
            f"(소스: {self._stats['source']})"
        )
        
        return result
    
    # =========================================================================
    # 데이터 소스별 조회
    # =========================================================================
    
    def _fetch_from_condition(self, condition_name: str) -> List[StockInfo]:
        """한투 API 조건검색으로 조회"""
        try:
            logger.info(f"조건검색 조회 시작: {condition_name}")
            
            results = self.broker.get_condition_universe(
                hts_id=self.hts_id,
                condition_name=condition_name,
                limit=500
            )
            
            if not results:
                logger.warning(f"조건검색 결과 없음: {condition_name}")
                return []
            
            stocks = []
            for item in results:
                stocks.append(StockInfo(
                    stock_code=item.get('code', ''),
                    stock_name=item.get('name', ''),
                    market=item.get('market', 'KOSPI'),
                    current_price=0,  # 나중에 필요시 조회
                    change_pct=0,
                    volume=0,
                    trade_value=0,
                ))
            
            logger.info(f"조건검색 조회 완료: {len(stocks)}개")
            return stocks
        
        except Exception as e:
            logger.error(f"조건검색 조회 실패: {e}")
            return []
    
    def _fetch_from_krx(self) -> List[StockInfo]:
        """KRX에서 거래대금 상위 조회"""
        stocks = []
        
        try:
            logger.info("KRX 거래대금 상위 조회 시작")
            
            for market_id, market_name in [("STK", "KOSPI"), ("KSQ", "KOSDAQ")]:
                market_stocks = self._fetch_krx_market(market_id, market_name)
                stocks.extend(market_stocks)
                time.sleep(0.3)  # 레이트 리밋
            
            logger.info(f"KRX 조회 완료: {len(stocks)}개")
            return stocks
        
        except Exception as e:
            logger.error(f"KRX 조회 실패: {e}")
            return []
    
    def _fetch_krx_market(self, market_id: str, market_name: str) -> List[StockInfo]:
        """KRX 특정 시장 거래대금 상위 조회"""
        try:
            # 오늘 날짜
            today = datetime.now().strftime("%Y%m%d")
            
            params = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
                "locale": "ko_KR",
                "mktId": market_id,
                "trdDd": today,
                "share": "1",
                "money": "1",
                "csvxls_is498No": "0",
            }
            
            headers = {
                "User-Agent": USER_AGENT,
                "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
            }
            
            response = requests.post(
                KRX_DATA_URL,
                data=params,
                headers=headers,
                timeout=15
            )
            
            if response.status_code != 200:
                logger.warning(f"KRX {market_name} 조회 실패: HTTP {response.status_code}")
                return []
            
            data = response.json()
            items = data.get("OutBlock_1", [])
            
            stocks = []
            for item in items[:150]:  # 상위 150개
                try:
                    code = item.get("ISU_SRT_CD", "")
                    if not code or len(code) != 6:
                        continue
                    
                    name = item.get("ISU_ABBRV", "")
                    
                    # 숫자 파싱 (쉼표 제거)
                    def parse_num(val):
                        if not val:
                            return 0
                        return float(str(val).replace(",", "").replace("-", "0"))
                    
                    price = parse_num(item.get("TDD_CLSPRC", 0))
                    change_pct = parse_num(item.get("FLUC_RT", 0))
                    volume = int(parse_num(item.get("ACC_TRDVOL", 0)))
                    trade_value = parse_num(item.get("ACC_TRDVAL", 0)) / 100000000  # 원 → 억원
                    market_cap = parse_num(item.get("MKTCAP", 0)) / 100000000  # 원 → 억원
                    
                    stocks.append(StockInfo(
                        stock_code=code,
                        stock_name=name,
                        current_price=price,
                        change_pct=change_pct,
                        volume=volume,
                        trade_value=trade_value,
                        market_cap=market_cap,
                        market=market_name,
                    ))
                except Exception:
                    continue
            
            logger.debug(f"KRX {market_name}: {len(stocks)}개")
            return stocks
        
        except Exception as e:
            logger.error(f"KRX {market_name} 조회 에러: {e}")
            return []
    
    def _fetch_from_naver(self) -> List[StockInfo]:
        """네이버 금융에서 거래대금 상위 조회 (fallback)"""
        stocks = []
        
        try:
            logger.info("네이버 금융 조회 시작 (fallback)")
            
            from bs4 import BeautifulSoup
            
            for market, sosok in [("KOSPI", "0"), ("KOSDAQ", "1")]:
                for page in range(1, 4):  # 3페이지 = 약 60개
                    try:
                        url = "https://finance.naver.com/sise/sise_quant.naver"
                        params = {"sosok": sosok, "page": page}
                        headers = {"User-Agent": USER_AGENT}
                        
                        response = requests.get(url, params=params, headers=headers, timeout=10)
                        if response.status_code != 200:
                            continue
                        
                        soup = BeautifulSoup(response.text, 'html.parser')
                        table = soup.find('table', class_='type_2')
                        if not table:
                            continue
                        
                        for row in table.find_all('tr'):
                            try:
                                cols = row.find_all('td')
                                if len(cols) < 10:
                                    continue
                                
                                name_tag = cols[1].find('a')
                                if not name_tag:
                                    continue
                                
                                href = name_tag.get('href', '')
                                if 'code=' not in href:
                                    continue
                                
                                code = href.split('code=')[1][:6]
                                name = name_tag.text.strip()
                                
                                def parse_num(text):
                                    text = text.strip().replace(',', '').replace('+', '')
                                    return float(text) if text and text != '-' else 0
                                
                                price = parse_num(cols[2].text)
                                change_pct = parse_num(cols[4].text)
                                volume = int(parse_num(cols[5].text))
                                trade_value = parse_num(cols[6].text) / 100  # 백만원 → 억원
                                
                                stocks.append(StockInfo(
                                    stock_code=code,
                                    stock_name=name,
                                    current_price=price,
                                    change_pct=change_pct,
                                    volume=volume,
                                    trade_value=trade_value,
                                    market=market,
                                ))
                            except Exception:
                                continue
                        
                        time.sleep(0.2)
                    except Exception:
                        continue
            
            logger.info(f"네이버 조회 완료: {len(stocks)}개")
            return stocks
        
        except ImportError:
            logger.error("BeautifulSoup 미설치 - pip install beautifulsoup4")
            return []
        except Exception as e:
            logger.error(f"네이버 조회 실패: {e}")
            return []
    
    # =========================================================================
    # 필터링
    # =========================================================================
    
    def _apply_filters(self, stocks: List[StockInfo]) -> List[StockInfo]:
        """필터 적용"""
        filtered = stocks
        initial_count = len(filtered)
        
        # 1. 가격 필터 (조건검색은 가격 정보 없을 수 있음)
        if any(s.current_price > 0 for s in filtered):
            filtered = [
                s for s in filtered
                if s.current_price == 0 or (self.min_price <= s.current_price <= self.max_price)
            ]
        
        # 2. 등락률 필터
        if any(s.change_pct != 0 for s in filtered):
            filtered = [
                s for s in filtered
                if s.change_pct == 0 or (self.min_change <= s.change_pct <= self.max_change)
            ]
        
        # 3. 거래량 필터
        if any(s.volume > 0 for s in filtered):
            filtered = [
                s for s in filtered
                if s.volume == 0 or s.volume >= self.min_volume
            ]
        
        # 4. 제외 패턴 필터 (ETF, ETN 등)
        filtered = [
            s for s in filtered
            if not any(pattern in s.stock_name for pattern in self.exclude_patterns)
        ]
        
        logger.info(f"필터링: {initial_count} → {len(filtered)}개")
        
        return filtered
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def refresh(self):
        """캐시 갱신"""
        self._cache_time = 0
        self.get_universe_with_info()
    
    def get_stats(self) -> Dict:
        """통계 조회"""
        return {
            **self._stats,
            'cache_size': len(self._cache),
            'cache_age': time.time() - self._cache_time if self._cache_time > 0 else 0,
        }
    
    def get_stock_info(self, stock_code: str) -> Optional[StockInfo]:
        """특정 종목 정보 조회 (캐시에서)"""
        for stock in self._cache:
            if stock.stock_code == stock_code:
                return stock
        return None
    
    def to_dataframe(self) -> pd.DataFrame:
        """DataFrame으로 변환"""
        if not self._cache:
            self.get_universe_with_info()
        
        return pd.DataFrame([s.to_dict() for s in self._cache])


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("UniverseFilter 테스트 (브로커 없이 - KRX/네이버)")
    print("=" * 60)
    
    # 브로커 없이 테스트 (KRX 또는 네이버 사용)
    filter = UniverseFilter(
        target_size=50,
        min_price=5000,
        max_price=500000,
    )
    
    # 유니버스 구성
    print("\n1. 유니버스 구성:")
    universe = filter.get_universe_with_info()
    print(f"   유니버스 크기: {len(universe)}개")
    print(f"   데이터 소스: {filter._stats['source']}")
    
    if universe:
        # 상위 10개 출력
        print("\n   상위 10개 종목:")
        for i, stock in enumerate(universe[:10], 1):
            print(
                f"   {i:2d}. {stock.stock_code} {stock.stock_name:12s} "
                f"{stock.current_price:>8,.0f}원 ({stock.change_pct:+.1f}%) "
                f"거래대금: {stock.trade_value:,.0f}억"
            )
    
    # 통계
    print("\n2. 통계:")
    stats = filter.get_stats()
    for key, value in stats.items():
        if isinstance(value, float):
            print(f"   {key}: {value:.2f}")
        else:
            print(f"   {key}: {value}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
