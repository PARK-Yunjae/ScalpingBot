#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - OHLCV Loader (데이터 로더)
============================================================================
일봉/분봉 OHLCV 데이터를 수집하고 캐싱

데이터 소스:
- FinanceDataReader (기본)
- 네이버 금융 크롤링 (백업)
- 한투 API (실시간)

핵심 기능:
- 일봉 데이터 로드 (60일)
- 분봉 데이터 로드 (당일)
- 메모리 캐시 (TTL 기반)
- 데이터 검증

사용법:
    loader = OHLCVLoader()
    
    # 일봉 데이터
    df = loader.get_daily_ohlcv("005930", days=60)
    
    # 분봉 데이터
    df = loader.get_minute_ohlcv("005930", minutes=30)
============================================================================
"""

import time
import logging
import threading
import requests
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date, timedelta
from dataclasses import dataclass
import pandas as pd
import numpy as np
from io import StringIO

# 로거 설정
logger = logging.getLogger('ScalpingBot.Data')


# =============================================================================
# 상수 설정
# =============================================================================

# 캐시 TTL (초)
DAILY_CACHE_TTL = 3600      # 일봉: 1시간
MINUTE_CACHE_TTL = 60       # 분봉: 1분

# 기본 일수
DEFAULT_DAILY_DAYS = 60
DEFAULT_MINUTE_BARS = 60

# 네이버 금융 URL
NAVER_DAILY_URL = "https://fchart.stock.naver.com/sise.nhn"
NAVER_SISE_URL = "https://finance.naver.com/item/sise_day.naver"

# User-Agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# =============================================================================
# 캐시 엔트리
# =============================================================================

@dataclass
class CacheEntry:
    """캐시 엔트리"""
    data: pd.DataFrame
    timestamp: float
    ttl: float
    
    def is_valid(self) -> bool:
        """캐시 유효성 확인"""
        return time.time() - self.timestamp < self.ttl


# =============================================================================
# OHLCV 로더 클래스
# =============================================================================

class OHLCVLoader:
    """
    OHLCV 데이터 로더
    
    다양한 데이터 소스에서 OHLCV 데이터를 수집하고
    메모리에 캐싱합니다.
    """
    
    def __init__(
        self,
        broker=None,
        use_fdr: bool = True,
        use_naver: bool = True,
        daily_cache_ttl: int = DAILY_CACHE_TTL,
        minute_cache_ttl: int = MINUTE_CACHE_TTL,
    ):
        """
        초기화
        
        Args:
            broker: KISBroker 인스턴스 (분봉용)
            use_fdr: FinanceDataReader 사용 여부
            use_naver: 네이버 금융 사용 여부
            daily_cache_ttl: 일봉 캐시 TTL (초)
            minute_cache_ttl: 분봉 캐시 TTL (초)
        """
        self.broker = broker
        self.use_fdr = use_fdr
        self.use_naver = use_naver
        self.daily_cache_ttl = daily_cache_ttl
        self.minute_cache_ttl = minute_cache_ttl
        
        # 캐시 (stock_code -> CacheEntry)
        self._daily_cache: Dict[str, CacheEntry] = {}
        self._minute_cache: Dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        
        # FinanceDataReader 로드
        self._fdr = None
        if use_fdr:
            try:
                import FinanceDataReader as fdr
                self._fdr = fdr
                logger.info("FinanceDataReader 로드 완료")
            except ImportError:
                logger.warning("FinanceDataReader를 찾을 수 없습니다. pip install finance-datareader")
                self.use_fdr = False
        
        # 통계
        self._stats = {
            'daily_requests': 0,
            'daily_cache_hits': 0,
            'minute_requests': 0,
            'minute_cache_hits': 0,
            'errors': 0,
        }
        
        logger.info(f"OHLCVLoader 초기화 (FDR: {self.use_fdr}, Naver: {self.use_naver})")
    
    # =========================================================================
    # 일봉 데이터
    # =========================================================================
    
    def get_daily_ohlcv(
        self,
        stock_code: str,
        days: int = DEFAULT_DAILY_DAYS,
        use_cache: bool = True,
    ) -> Optional[pd.DataFrame]:
        """
        일봉 OHLCV 데이터 조회
        
        Args:
            stock_code: 종목 코드 (6자리)
            days: 조회 일수
            use_cache: 캐시 사용 여부
        
        Returns:
            DataFrame (Date, Open, High, Low, Close, Volume) 또는 None
        """
        self._stats['daily_requests'] += 1
        
        # 캐시 확인
        if use_cache:
            with self._lock:
                if stock_code in self._daily_cache:
                    entry = self._daily_cache[stock_code]
                    if entry.is_valid():
                        self._stats['daily_cache_hits'] += 1
                        return entry.data.copy()
        
        # 데이터 로드
        df = None
        
        # 1. FinanceDataReader 시도
        if self.use_fdr and self._fdr:
            df = self._load_daily_fdr(stock_code, days)
        
        # 2. 네이버 금융 시도
        if df is None and self.use_naver:
            df = self._load_daily_naver(stock_code, days)
        
        # 3. 한투 API 시도
        if df is None and self.broker:
            df = self._load_daily_broker(stock_code, days)
        
        if df is not None and not df.empty:
            # 캐시 저장
            with self._lock:
                self._daily_cache[stock_code] = CacheEntry(
                    data=df.copy(),
                    timestamp=time.time(),
                    ttl=self.daily_cache_ttl,
                )
            
            return df
        
        self._stats['errors'] += 1
        logger.warning(f"일봉 데이터 로드 실패: {stock_code}")
        return None
    
    def _load_daily_fdr(self, stock_code: str, days: int) -> Optional[pd.DataFrame]:
        """FinanceDataReader로 일봉 로드"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days + 30)  # 여유 있게
            
            df = self._fdr.DataReader(
                stock_code,
                start_date.strftime('%Y-%m-%d'),
                end_date.strftime('%Y-%m-%d')
            )
            
            if df is not None and not df.empty:
                # 컬럼 정규화
                df = df.reset_index()
                df.columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'Change']
                df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
                df = df.tail(days)
                
                logger.debug(f"FDR 일봉 로드: {stock_code} ({len(df)}일)")
                return df
        
        except Exception as e:
            logger.debug(f"FDR 일봉 로드 실패 ({stock_code}): {e}")
        
        return None
    
    def _load_daily_naver(self, stock_code: str, days: int) -> Optional[pd.DataFrame]:
        """네이버 금융에서 일봉 로드 (차트 데이터)"""
        try:
            # 네이버 차트 API
            params = {
                'symbol': stock_code,
                'timeframe': 'day',
                'count': days + 10,
                'requestType': 0,
            }
            
            headers = {'User-Agent': USER_AGENT}
            response = requests.get(
                NAVER_DAILY_URL,
                params=params,
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                # XML 파싱
                from xml.etree import ElementTree as ET
                root = ET.fromstring(response.text)
                
                items = root.findall('.//item')
                if not items:
                    return None
                
                data = []
                for item in items:
                    data_str = item.get('data', '')
                    if data_str:
                        parts = data_str.split('|')
                        if len(parts) >= 6:
                            data.append({
                                'Date': pd.to_datetime(parts[0]),
                                'Open': float(parts[1]),
                                'High': float(parts[2]),
                                'Low': float(parts[3]),
                                'Close': float(parts[4]),
                                'Volume': float(parts[5]),
                            })
                
                if data:
                    df = pd.DataFrame(data)
                    df = df.sort_values('Date').tail(days)
                    
                    logger.debug(f"Naver 일봉 로드: {stock_code} ({len(df)}일)")
                    return df
        
        except Exception as e:
            logger.debug(f"Naver 일봉 로드 실패 ({stock_code}): {e}")
        
        return None
    
    def _load_daily_broker(self, stock_code: str, days: int) -> Optional[pd.DataFrame]:
        """한투 API로 일봉 로드"""
        try:
            if not self.broker:
                return None
            
            df = self.broker.get_daily_ohlcv(stock_code, period=days)
            
            if df is not None and not df.empty:
                logger.debug(f"브로커 일봉 로드: {stock_code} ({len(df)}일)")
                return df
        
        except Exception as e:
            logger.debug(f"브로커 일봉 로드 실패 ({stock_code}): {e}")
        
        return None
    
    # =========================================================================
    # 분봉 데이터
    # =========================================================================
    
    def get_minute_ohlcv(
        self,
        stock_code: str,
        minutes: int = DEFAULT_MINUTE_BARS,
        interval: int = 1,
        use_cache: bool = True,
    ) -> Optional[pd.DataFrame]:
        """
        분봉 OHLCV 데이터 조회
        
        Args:
            stock_code: 종목 코드
            minutes: 조회 분수
            interval: 봉 간격 (1, 3, 5, 10, 15, 30, 60)
            use_cache: 캐시 사용 여부
        
        Returns:
            DataFrame (DateTime, Open, High, Low, Close, Volume) 또는 None
        """
        self._stats['minute_requests'] += 1
        cache_key = f"{stock_code}_{interval}"
        
        # 캐시 확인
        if use_cache:
            with self._lock:
                if cache_key in self._minute_cache:
                    entry = self._minute_cache[cache_key]
                    if entry.is_valid():
                        self._stats['minute_cache_hits'] += 1
                        return entry.data.copy()
        
        # 데이터 로드
        df = None
        
        # 한투 API로 로드
        if self.broker:
            df = self._load_minute_broker(stock_code, minutes, interval)
        
        if df is not None and not df.empty:
            # 캐시 저장
            with self._lock:
                self._minute_cache[cache_key] = CacheEntry(
                    data=df.copy(),
                    timestamp=time.time(),
                    ttl=self.minute_cache_ttl,
                )
            
            return df
        
        self._stats['errors'] += 1
        logger.warning(f"분봉 데이터 로드 실패: {stock_code}")
        return None
    
    def _load_minute_broker(
        self,
        stock_code: str,
        minutes: int,
        interval: int,
    ) -> Optional[pd.DataFrame]:
        """한투 API로 분봉 로드"""
        try:
            if not self.broker:
                return None
            
            # 한투 API 분봉 조회 (구현 필요)
            # 현재는 브로커에 get_minute_ohlcv가 없으므로 None 반환
            # TODO: 브로커에 분봉 조회 메서드 추가
            
            return None
        
        except Exception as e:
            logger.debug(f"브로커 분봉 로드 실패 ({stock_code}): {e}")
        
        return None
    
    # =========================================================================
    # 현재가 조회
    # =========================================================================
    
    def get_current_price(self, stock_code: str) -> float:
        """
        현재가 조회
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            현재가 (조회 실패 시 0)
        """
        # 1. 브로커로 조회
        if self.broker:
            price = self.broker.get_current_price(stock_code)
            if price > 0:
                return price
        
        # 2. 네이버로 조회
        try:
            url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
            headers = {'User-Agent': USER_AGENT}
            response = requests.get(url, headers=headers, timeout=5)
            
            if response.status_code == 200:
                import re
                match = re.search(r'<dd>현재가 <span>([0-9,]+)</span>', response.text)
                if match:
                    return float(match.group(1).replace(',', ''))
        except:
            pass
        
        return 0.0
    
    # =========================================================================
    # 복수 종목 조회
    # =========================================================================
    
    def get_multiple_daily(
        self,
        stock_codes: List[str],
        days: int = DEFAULT_DAILY_DAYS,
    ) -> Dict[str, pd.DataFrame]:
        """
        복수 종목 일봉 조회
        
        Args:
            stock_codes: 종목 코드 리스트
            days: 조회 일수
        
        Returns:
            {종목코드: DataFrame} 딕셔너리
        """
        result = {}
        
        for code in stock_codes:
            df = self.get_daily_ohlcv(code, days)
            if df is not None:
                result[code] = df
            time.sleep(0.1)  # 레이트 리밋 방지
        
        logger.info(f"복수 일봉 로드: {len(result)}/{len(stock_codes)}개 성공")
        return result
    
    def get_multiple_prices(
        self,
        stock_codes: List[str],
    ) -> Dict[str, float]:
        """
        복수 종목 현재가 조회
        
        Args:
            stock_codes: 종목 코드 리스트
        
        Returns:
            {종목코드: 현재가} 딕셔너리
        """
        result = {}
        
        for code in stock_codes:
            price = self.get_current_price(code)
            if price > 0:
                result[code] = price
            time.sleep(0.05)  # 레이트 리밋 방지
        
        return result
    
    # =========================================================================
    # 캐시 관리
    # =========================================================================
    
    def clear_cache(self, stock_code: str = None):
        """
        캐시 삭제
        
        Args:
            stock_code: 특정 종목만 삭제 (None이면 전체)
        """
        with self._lock:
            if stock_code:
                self._daily_cache.pop(stock_code, None)
                # 분봉은 키가 다르므로 패턴 매칭
                keys_to_remove = [k for k in self._minute_cache if k.startswith(stock_code)]
                for k in keys_to_remove:
                    del self._minute_cache[k]
            else:
                self._daily_cache.clear()
                self._minute_cache.clear()
        
        logger.info(f"캐시 삭제: {stock_code or '전체'}")
    
    def cleanup_expired_cache(self):
        """만료된 캐시 정리"""
        with self._lock:
            # 일봉 캐시
            expired = [k for k, v in self._daily_cache.items() if not v.is_valid()]
            for k in expired:
                del self._daily_cache[k]
            
            # 분봉 캐시
            expired = [k for k, v in self._minute_cache.items() if not v.is_valid()]
            for k in expired:
                del self._minute_cache[k]
        
        if expired:
            logger.debug(f"만료 캐시 정리: {len(expired)}개")
    
    # =========================================================================
    # 통계
    # =========================================================================
    
    def get_stats(self) -> Dict:
        """통계 조회"""
        daily_hit_rate = (
            self._stats['daily_cache_hits'] / self._stats['daily_requests'] * 100
            if self._stats['daily_requests'] > 0 else 0
        )
        minute_hit_rate = (
            self._stats['minute_cache_hits'] / self._stats['minute_requests'] * 100
            if self._stats['minute_requests'] > 0 else 0
        )
        
        return {
            **self._stats,
            'daily_hit_rate': daily_hit_rate,
            'minute_hit_rate': minute_hit_rate,
            'daily_cache_size': len(self._daily_cache),
            'minute_cache_size': len(self._minute_cache),
        }


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    # 로깅 설정
    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("OHLCVLoader 테스트")
    print("=" * 60)
    
    loader = OHLCVLoader()
    
    # 1. 일봉 데이터 로드
    print("\n1. 일봉 데이터 로드 테스트:")
    df = loader.get_daily_ohlcv("005930", days=30)
    
    if df is not None:
        print(f"   로드 성공: {len(df)}일")
        print(f"   컬럼: {list(df.columns)}")
        print(f"   최근 데이터:\n{df.tail(3)}")
    else:
        print("   로드 실패 (네트워크 또는 라이브러리 없음)")
    
    # 2. 캐시 테스트
    print("\n2. 캐시 테스트:")
    df2 = loader.get_daily_ohlcv("005930", days=30)
    stats = loader.get_stats()
    print(f"   캐시 히트율: {stats['daily_hit_rate']:.1f}%")
    
    # 3. 복수 종목 테스트
    print("\n3. 복수 종목 테스트:")
    codes = ["005930", "000660", "035720"]
    results = loader.get_multiple_daily(codes, days=10)
    print(f"   성공: {len(results)}/{len(codes)}개")
    
    # 4. 통계
    print("\n4. 통계:")
    stats = loader.get_stats()
    for key, value in stats.items():
        if isinstance(value, float):
            print(f"   {key}: {value:.2f}")
        else:
            print(f"   {key}: {value}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
