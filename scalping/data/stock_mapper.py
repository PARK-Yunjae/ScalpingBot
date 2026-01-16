#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Stock Mapper (종목 매퍼)
============================================================================
종목코드와 종목명 간의 매핑 관리

핵심 기능:
- 종목코드 → 종목명 변환
- 종목명 → 종목코드 변환
- 시장 구분 (KOSPI/KOSDAQ)
- 종목 정보 캐싱

데이터 소스:
- KRX 상장종목 목록
- FinanceDataReader

사용법:
    mapper = StockMapper()
    
    name = mapper.code_to_name("005930")  # "삼성전자"
    code = mapper.name_to_code("삼성전자")  # "005930"
============================================================================
"""

import logging
import requests
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, date
from dataclasses import dataclass
from pathlib import Path
import json

# 로거 설정
logger = logging.getLogger('ScalpingBot.StockMapper')


# =============================================================================
# 상수 설정
# =============================================================================

# 캐시 설정
CACHE_FILE = "stock_list.json"
CACHE_TTL_DAYS = 1

# KRX 상장종목 URL
KRX_STOCK_URL = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

# 네이버 금융 URL
NAVER_STOCK_LIST_URL = "https://finance.naver.com/sise/sise_market_sum.naver"


# =============================================================================
# 종목 정보 데이터 클래스
# =============================================================================

@dataclass
class StockMeta:
    """종목 메타 정보"""
    stock_code: str
    stock_name: str
    market: str              # KOSPI / KOSDAQ
    sector: str = ""         # 업종
    market_cap: float = 0    # 시가총액 (억원)
    
    def to_dict(self) -> Dict:
        return {
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'market': self.market,
            'sector': self.sector,
            'market_cap': self.market_cap,
        }


# =============================================================================
# 종목 매퍼 클래스
# =============================================================================

class StockMapper:
    """
    종목 코드/이름 매퍼
    
    종목코드와 종목명 간의 양방향 변환을 제공합니다.
    """
    
    def __init__(
        self,
        cache_dir: Path = None,
        auto_load: bool = True,
    ):
        """
        초기화
        
        Args:
            cache_dir: 캐시 디렉토리
            auto_load: 자동 로드 여부
        """
        self.cache_dir = cache_dir or Path("db")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # 매핑 딕셔너리
        self._code_to_meta: Dict[str, StockMeta] = {}
        self._name_to_code: Dict[str, str] = {}
        
        # 캐시 파일 경로
        self._cache_file = self.cache_dir / CACHE_FILE
        
        # 로드 상태
        self._loaded = False
        self._load_time: Optional[datetime] = None
        
        if auto_load:
            self.load()
        
        logger.info(f"StockMapper 초기화 (캐시: {self.cache_dir})")
    
    # =========================================================================
    # 로드 및 저장
    # =========================================================================
    
    def load(self, force_refresh: bool = False) -> bool:
        """
        종목 목록 로드
        
        Args:
            force_refresh: 강제 새로고침
        
        Returns:
            성공 여부
        """
        # 캐시 확인
        if not force_refresh and self._load_from_cache():
            return True
        
        # 온라인에서 로드
        if self._load_from_online():
            self._save_to_cache()
            return True
        
        # 실패 시 빈 캐시라도 사용
        logger.warning("종목 목록 로드 실패")
        return False
    
    def _load_from_cache(self) -> bool:
        """캐시에서 로드"""
        try:
            if not self._cache_file.exists():
                return False
            
            # 캐시 유효성 체크
            cache_mtime = datetime.fromtimestamp(self._cache_file.stat().st_mtime)
            cache_age = (datetime.now() - cache_mtime).days
            
            if cache_age >= CACHE_TTL_DAYS:
                logger.info("캐시 만료됨")
                return False
            
            # 캐시 로드
            with open(self._cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for item in data.get('stocks', []):
                meta = StockMeta(
                    stock_code=item['stock_code'],
                    stock_name=item['stock_name'],
                    market=item.get('market', ''),
                    sector=item.get('sector', ''),
                    market_cap=item.get('market_cap', 0),
                )
                self._code_to_meta[meta.stock_code] = meta
                self._name_to_code[meta.stock_name] = meta.stock_code
            
            self._loaded = True
            self._load_time = cache_mtime
            
            logger.info(f"캐시에서 로드: {len(self._code_to_meta)}개 종목")
            return True
        
        except Exception as e:
            logger.error(f"캐시 로드 에러: {e}")
            return False
    
    def _load_from_online(self) -> bool:
        """온라인에서 로드"""
        stocks = []
        
        # 1. FinanceDataReader 시도
        try:
            import FinanceDataReader as fdr
            
            # 코스피
            kospi = fdr.StockListing('KOSPI')
            for _, row in kospi.iterrows():
                stocks.append(StockMeta(
                    stock_code=str(row.get('Code', '')).zfill(6),
                    stock_name=row.get('Name', ''),
                    market='KOSPI',
                    sector=row.get('Sector', ''),
                ))
            
            # 코스닥
            kosdaq = fdr.StockListing('KOSDAQ')
            for _, row in kosdaq.iterrows():
                stocks.append(StockMeta(
                    stock_code=str(row.get('Code', '')).zfill(6),
                    stock_name=row.get('Name', ''),
                    market='KOSDAQ',
                    sector=row.get('Sector', ''),
                ))
            
            logger.info(f"FDR에서 로드: {len(stocks)}개 종목")
        
        except ImportError:
            logger.info("FDR 없음, 네이버에서 로드 시도")
            stocks = self._load_from_naver()
        except Exception as e:
            logger.warning(f"FDR 로드 실패: {e}")
            stocks = self._load_from_naver()
        
        if stocks:
            for meta in stocks:
                if meta.stock_code and meta.stock_name:
                    self._code_to_meta[meta.stock_code] = meta
                    self._name_to_code[meta.stock_name] = meta.stock_code
            
            self._loaded = True
            self._load_time = datetime.now()
            
            logger.info(f"온라인에서 로드: {len(self._code_to_meta)}개 종목")
            return True
        
        return False
    
    def _load_from_naver(self) -> List[StockMeta]:
        """네이버 금융에서 로드"""
        stocks = []
        
        try:
            from bs4 import BeautifulSoup
            
            for market, sosok in [('KOSPI', '0'), ('KOSDAQ', '1')]:
                for page in range(1, 40):  # 약 800개
                    try:
                        response = requests.get(
                            NAVER_STOCK_LIST_URL,
                            params={'sosok': sosok, 'page': page},
                            headers={'User-Agent': 'Mozilla/5.0'},
                            timeout=10
                        )
                        
                        if response.status_code != 200:
                            break
                        
                        soup = BeautifulSoup(response.text, 'html.parser')
                        rows = soup.select('table.type_2 tr')
                        
                        found = False
                        for row in rows:
                            try:
                                link = row.select_one('a[href*="main.naver?code="]')
                                if not link:
                                    continue
                                
                                href = link.get('href', '')
                                code = href.split('code=')[1][:6]
                                name = link.text.strip()
                                
                                if code and name:
                                    stocks.append(StockMeta(
                                        stock_code=code,
                                        stock_name=name,
                                        market=market,
                                    ))
                                    found = True
                            except:
                                continue
                        
                        if not found:
                            break
                        
                        time.sleep(0.1)
                    except:
                        break
            
            logger.info(f"네이버에서 로드: {len(stocks)}개 종목")
        
        except Exception as e:
            logger.error(f"네이버 로드 에러: {e}")
        
        return stocks
    
    def _save_to_cache(self):
        """캐시에 저장"""
        try:
            data = {
                'updated': datetime.now().isoformat(),
                'stocks': [meta.to_dict() for meta in self._code_to_meta.values()],
            }
            
            with open(self._cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"캐시 저장: {len(self._code_to_meta)}개 종목")
        
        except Exception as e:
            logger.error(f"캐시 저장 에러: {e}")
    
    # =========================================================================
    # 변환 함수
    # =========================================================================
    
    def code_to_name(self, stock_code: str) -> str:
        """
        종목코드 → 종목명
        
        Args:
            stock_code: 종목코드 (6자리)
        
        Returns:
            종목명 (없으면 빈 문자열)
        """
        stock_code = str(stock_code).zfill(6)
        meta = self._code_to_meta.get(stock_code)
        return meta.stock_name if meta else ""
    
    def name_to_code(self, stock_name: str) -> str:
        """
        종목명 → 종목코드
        
        Args:
            stock_name: 종목명
        
        Returns:
            종목코드 (없으면 빈 문자열)
        """
        return self._name_to_code.get(stock_name, "")
    
    def get_meta(self, stock_code: str) -> Optional[StockMeta]:
        """
        종목 메타 정보 조회
        
        Args:
            stock_code: 종목코드
        
        Returns:
            StockMeta 또는 None
        """
        stock_code = str(stock_code).zfill(6)
        return self._code_to_meta.get(stock_code)
    
    def get_market(self, stock_code: str) -> str:
        """
        시장 구분 조회
        
        Args:
            stock_code: 종목코드
        
        Returns:
            "KOSPI" 또는 "KOSDAQ" 또는 ""
        """
        meta = self.get_meta(stock_code)
        return meta.market if meta else ""
    
    # =========================================================================
    # 검색 및 필터
    # =========================================================================
    
    def search(self, keyword: str, limit: int = 10) -> List[StockMeta]:
        """
        종목 검색
        
        Args:
            keyword: 검색어 (코드 또는 이름)
            limit: 최대 결과 수
        
        Returns:
            StockMeta 리스트
        """
        results = []
        keyword = keyword.upper()
        
        for meta in self._code_to_meta.values():
            if keyword in meta.stock_code or keyword in meta.stock_name.upper():
                results.append(meta)
                if len(results) >= limit:
                    break
        
        return results
    
    def get_by_market(self, market: str) -> List[str]:
        """
        시장별 종목코드 조회
        
        Args:
            market: "KOSPI" 또는 "KOSDAQ"
        
        Returns:
            종목코드 리스트
        """
        return [
            meta.stock_code 
            for meta in self._code_to_meta.values() 
            if meta.market.upper() == market.upper()
        ]
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def is_loaded(self) -> bool:
        """로드 상태"""
        return self._loaded
    
    def count(self) -> int:
        """종목 수"""
        return len(self._code_to_meta)
    
    def refresh(self):
        """강제 새로고침"""
        self._code_to_meta.clear()
        self._name_to_code.clear()
        self.load(force_refresh=True)
    
    def get_all_codes(self) -> List[str]:
        """전체 종목코드 목록"""
        return list(self._code_to_meta.keys())
    
    def get_all_names(self) -> List[str]:
        """전체 종목명 목록"""
        return list(self._name_to_code.keys())


# =============================================================================
# 전역 인스턴스 (싱글톤)
# =============================================================================

_mapper_instance: Optional[StockMapper] = None


def get_mapper() -> StockMapper:
    """전역 StockMapper 인스턴스"""
    global _mapper_instance
    if _mapper_instance is None:
        _mapper_instance = StockMapper()
    return _mapper_instance


# =============================================================================
# 편의 함수
# =============================================================================

def code_to_name(stock_code: str) -> str:
    """종목코드 → 종목명"""
    return get_mapper().code_to_name(stock_code)


def name_to_code(stock_name: str) -> str:
    """종목명 → 종목코드"""
    return get_mapper().name_to_code(stock_name)


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("StockMapper 테스트")
    print("=" * 60)
    
    import tempfile
    cache_dir = Path(tempfile.gettempdir()) / "stock_mapper_test"
    
    mapper = StockMapper(cache_dir=cache_dir)
    
    print(f"\n1. 로드 상태: {mapper.is_loaded()}")
    print(f"   종목 수: {mapper.count()}")
    
    if mapper.count() > 0:
        print("\n2. 변환 테스트:")
        
        # 삼성전자 테스트
        name = mapper.code_to_name("005930")
        print(f"   005930 → {name}")
        
        code = mapper.name_to_code("삼성전자")
        print(f"   삼성전자 → {code}")
        
        # 메타 정보
        meta = mapper.get_meta("005930")
        if meta:
            print(f"   시장: {meta.market}")
        
        print("\n3. 검색 테스트:")
        results = mapper.search("삼성", limit=5)
        for r in results:
            print(f"   {r.stock_code} {r.stock_name} ({r.market})")
        
        print("\n4. 시장별 종목:")
        kospi = mapper.get_by_market("KOSPI")
        kosdaq = mapper.get_by_market("KOSDAQ")
        print(f"   KOSPI: {len(kospi)}개")
        print(f"   KOSDAQ: {len(kosdaq)}개")
    else:
        print("\n종목 목록 로드 실패 (네트워크 확인)")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
