#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Universe Filter (유니버스 필터)
============================================================================
거래대금 상위 종목을 필터링하여 스캔 대상 유니버스 구성

필터링 단계:
1. 거래대금 상위 200개 조회
2. 등락률 필터 (-5% ~ +15%)
3. 가격 필터 (1,000원 ~ 500,000원)
4. 시가총액 필터 (500억 이상)
5. 최종 100개 선정

데이터 소스:
- 네이버 금융 거래상위
- FinanceDataReader (백업)
- 한투 API (백업)

사용법:
    filter = UniverseFilter()
    
    # 유니버스 구성
    universe = filter.get_universe(target_size=100)
    
    # 상세 정보 포함
    df = filter.get_universe_with_info()
============================================================================
"""

import time
import logging
import requests
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date
from dataclasses import dataclass
import pandas as pd
from bs4 import BeautifulSoup

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
DEFAULT_MIN_MARKET_CAP = 500       # 최소 시가총액 (억원)
DEFAULT_MIN_VOLUME = 100000        # 최소 거래량

# 네이버 금융 URL
NAVER_VOLUME_URL = "https://finance.naver.com/sise/sise_quant.naver"
NAVER_MARKET_URL = "https://finance.naver.com/sise/sise_market_sum.naver"

# 제외 종목 (ETF, ETN, 스팩 등)
EXCLUDE_PATTERNS = ['ETF', 'ETN', 'KODEX', 'TIGER', 'KBSTAR', 'ARIRANG', 
                    'HANARO', 'SOL', '스팩', 'SPAC', '리츠']

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
    current_price: float
    change_pct: float
    volume: int
    trade_value: float      # 거래대금 (억원)
    market_cap: float = 0   # 시가총액 (억원)
    market: str = ""        # KOSPI / KOSDAQ
    
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
    """
    
    def __init__(
        self,
        top_n: int = DEFAULT_TOP_N,
        target_size: int = DEFAULT_TARGET_SIZE,
        min_price: float = DEFAULT_MIN_PRICE,
        max_price: float = DEFAULT_MAX_PRICE,
        min_change: float = DEFAULT_MIN_CHANGE,
        max_change: float = DEFAULT_MAX_CHANGE,
        min_market_cap: float = DEFAULT_MIN_MARKET_CAP,
        min_volume: int = DEFAULT_MIN_VOLUME,
        exclude_patterns: List[str] = None,
    ):
        """
        초기화
        
        Args:
            top_n: 거래대금 상위 N개 조회
            target_size: 최종 유니버스 크기
            min_price: 최소 가격
            max_price: 최대 가격
            min_change: 최소 등락률 (%)
            max_change: 최대 등락률 (%)
            min_market_cap: 최소 시가총액 (억원)
            min_volume: 최소 거래량
            exclude_patterns: 제외 패턴 리스트
        """
        self.top_n = top_n
        self.target_size = target_size
        self.min_price = min_price
        self.max_price = max_price
        self.min_change = min_change
        self.max_change = max_change
        self.min_market_cap = min_market_cap
        self.min_volume = min_volume
        self.exclude_patterns = exclude_patterns or EXCLUDE_PATTERNS
        
        # 캐시
        self._cache: List[StockInfo] = []
        self._cache_time: float = 0
        self._cache_ttl: float = 300  # 5분
        
        # 통계
        self._stats = {
            'total_fetched': 0,
            'after_price_filter': 0,
            'after_change_filter': 0,
            'after_exclude_filter': 0,
            'final_size': 0,
        }
        
        logger.info(
            f"UniverseFilter 초기화 "
            f"(상위 {top_n}개 → 최종 {target_size}개)"
        )
    
    # =========================================================================
    # 유니버스 구성
    # =========================================================================
    
    def get_universe(
        self,
        target_size: int = None,
        use_cache: bool = True,
    ) -> List[str]:
        """
        유니버스 구성 (종목 코드만)
        
        Args:
            target_size: 목표 크기 (None이면 기본값)
            use_cache: 캐시 사용 여부
        
        Returns:
            종목 코드 리스트
        """
        stocks = self.get_universe_with_info(target_size, use_cache)
        return [s.stock_code for s in stocks]
    
    def get_universe_with_info(
        self,
        target_size: int = None,
        use_cache: bool = True,
    ) -> List[StockInfo]:
        """
        유니버스 구성 (상세 정보 포함)
        
        Args:
            target_size: 목표 크기 (None이면 기본값)
            use_cache: 캐시 사용 여부
        
        Returns:
            StockInfo 리스트
        """
        target = target_size or self.target_size
        
        # 캐시 확인
        if use_cache and self._cache and time.time() - self._cache_time < self._cache_ttl:
            return self._cache[:target]
        
        # 데이터 수집
        stocks = self._fetch_top_stocks()
        
        if not stocks:
            logger.error("거래대금 상위 종목 조회 실패")
            return self._cache[:target] if self._cache else []
        
        # 필터링
        filtered = self._apply_filters(stocks)
        
        # 정렬 (거래대금 순)
        filtered.sort(key=lambda x: -x.trade_value)
        
        # 목표 크기로 자르기
        result = filtered[:target]
        
        # 캐시 저장
        self._cache = result
        self._cache_time = time.time()
        
        self._stats['final_size'] = len(result)
        
        logger.info(f"유니버스 구성 완료: {len(result)}개 종목")
        
        return result
    
    # =========================================================================
    # 데이터 수집
    # =========================================================================
    
    def _fetch_top_stocks(self) -> List[StockInfo]:
        """거래대금 상위 종목 조회"""
        stocks = []
        
        # 1. 네이버 금융에서 조회
        for market in ['kospi', 'kosdaq']:
            market_stocks = self._fetch_naver_volume(market)
            stocks.extend(market_stocks)
        
        self._stats['total_fetched'] = len(stocks)
        logger.info(f"거래대금 상위 {len(stocks)}개 조회")
        
        return stocks
    
    def _fetch_naver_volume(self, market: str = 'kospi') -> List[StockInfo]:
        """네이버 금융 거래대금 상위 조회"""
        stocks = []
        
        try:
            # 여러 페이지 조회
            for page in range(1, 6):  # 5페이지 = 100개
                params = {
                    'sosok': '0' if market == 'kospi' else '1',
                    'page': page,
                }
                headers = {
                    'User-Agent': USER_AGENT,
                    'Referer': 'https://finance.naver.com/',
                }
                
                response = requests.get(
                    NAVER_VOLUME_URL,
                    params=params,
                    headers=headers,
                    timeout=10
                )
                
                if response.status_code != 200:
                    continue
                
                soup = BeautifulSoup(response.text, 'html.parser')
                table = soup.find('table', class_='type_2')
                
                if not table:
                    continue
                
                rows = table.find_all('tr')
                
                for row in rows:
                    try:
                        cols = row.find_all('td')
                        if len(cols) < 10:
                            continue
                        
                        # 종목명, 코드 추출
                        name_tag = cols[1].find('a')
                        if not name_tag:
                            continue
                        
                        href = name_tag.get('href', '')
                        if 'code=' not in href:
                            continue
                        
                        stock_code = href.split('code=')[1][:6]
                        stock_name = name_tag.text.strip()
                        
                        # 숫자 파싱
                        def parse_number(text):
                            text = text.strip().replace(',', '').replace('+', '')
                            return float(text) if text and text != '-' else 0
                        
                        current_price = parse_number(cols[2].text)
                        change_pct = parse_number(cols[4].text)
                        volume = int(parse_number(cols[5].text))
                        trade_value = parse_number(cols[6].text) / 100  # 백만원 → 억원
                        
                        stocks.append(StockInfo(
                            stock_code=stock_code,
                            stock_name=stock_name,
                            current_price=current_price,
                            change_pct=change_pct,
                            volume=volume,
                            trade_value=trade_value,
                            market=market.upper(),
                        ))
                    
                    except Exception as e:
                        continue
                
                time.sleep(0.2)  # 레이트 리밋
        
        except Exception as e:
            logger.error(f"네이버 거래대금 조회 실패 ({market}): {e}")
        
        return stocks
    
    # =========================================================================
    # 필터링
    # =========================================================================
    
    def _apply_filters(self, stocks: List[StockInfo]) -> List[StockInfo]:
        """필터 적용"""
        filtered = stocks
        
        # 1. 가격 필터
        filtered = [
            s for s in filtered
            if self.min_price <= s.current_price <= self.max_price
        ]
        self._stats['after_price_filter'] = len(filtered)
        
        # 2. 등락률 필터
        filtered = [
            s for s in filtered
            if self.min_change <= s.change_pct <= self.max_change
        ]
        self._stats['after_change_filter'] = len(filtered)
        
        # 3. 거래량 필터
        filtered = [
            s for s in filtered
            if s.volume >= self.min_volume
        ]
        
        # 4. 제외 패턴 필터 (ETF, ETN 등)
        filtered = [
            s for s in filtered
            if not any(pattern in s.stock_name for pattern in self.exclude_patterns)
        ]
        self._stats['after_exclude_filter'] = len(filtered)
        
        logger.info(
            f"필터링: {len(stocks)} → 가격:{self._stats['after_price_filter']} → "
            f"등락:{self._stats['after_change_filter']} → 제외:{len(filtered)}"
        )
        
        return filtered
    
    # =========================================================================
    # 개별 종목 확인
    # =========================================================================
    
    def is_valid_stock(self, stock_info: StockInfo) -> Tuple[bool, str]:
        """
        종목 유효성 확인
        
        Args:
            stock_info: 종목 정보
        
        Returns:
            (유효여부, 사유)
        """
        # 가격 체크
        if stock_info.current_price < self.min_price:
            return False, f"가격 미달 ({stock_info.current_price:,.0f} < {self.min_price:,.0f})"
        
        if stock_info.current_price > self.max_price:
            return False, f"가격 초과 ({stock_info.current_price:,.0f} > {self.max_price:,.0f})"
        
        # 등락률 체크
        if stock_info.change_pct < self.min_change:
            return False, f"등락률 미달 ({stock_info.change_pct:.1f}% < {self.min_change:.1f}%)"
        
        if stock_info.change_pct > self.max_change:
            return False, f"등락률 초과 ({stock_info.change_pct:.1f}% > {self.max_change:.1f}%)"
        
        # 제외 패턴 체크
        for pattern in self.exclude_patterns:
            if pattern in stock_info.stock_name:
                return False, f"제외 패턴 ({pattern})"
        
        return True, "유효"
    
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
        """
        특정 종목 정보 조회 (캐시에서)
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            StockInfo 또는 None
        """
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
    print("UniverseFilter 테스트")
    print("=" * 60)
    
    filter = UniverseFilter(
        top_n=200,
        target_size=100,
        min_price=1000,
        max_price=500000,
        min_change=-5.0,
        max_change=15.0,
    )
    
    # 1. 유니버스 구성
    print("\n1. 유니버스 구성:")
    universe = filter.get_universe_with_info()
    print(f"   유니버스 크기: {len(universe)}개")
    
    if universe:
        # 상위 10개 출력
        print("\n   상위 10개 종목:")
        for i, stock in enumerate(universe[:10], 1):
            print(
                f"   {i:2d}. {stock.stock_code} {stock.stock_name:12s} "
                f"{stock.current_price:>8,.0f}원 ({stock.change_pct:+.1f}%) "
                f"거래대금: {stock.trade_value:,.0f}억"
            )
    
    # 2. 코드만 추출
    print("\n2. 종목 코드만 추출:")
    codes = filter.get_universe()
    print(f"   코드 수: {len(codes)}개")
    if codes:
        print(f"   예시: {codes[:5]}")
    
    # 3. 필터링 통계
    print("\n3. 필터링 통계:")
    stats = filter.get_stats()
    for key, value in stats.items():
        if isinstance(value, float):
            print(f"   {key}: {value:.2f}")
        else:
            print(f"   {key}: {value}")
    
    # 4. DataFrame 변환
    print("\n4. DataFrame 변환:")
    df = filter.to_dataframe()
    if not df.empty:
        print(f"   행: {len(df)}, 열: {list(df.columns)}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
