#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Price Validator (가격 검증기)
============================================================================
AI 분석 시점과 주문 시점 사이의 가격 변동을 검증

핵심 기능:
- AI 분석 시점 가격 vs 현재 가격 비교
- 슬리피지 허용 범위 체크
- 급등 종목 매수 방지
- 호가 스프레드 검증

검증 규칙:
- 현재가 > 분석가 + 1.5%: SKIP (이미 급등)
- 분석 후 30초 이상 경과: SKIP (시효 만료)
- 호가 스프레드 > 1%: SKIP (유동성 부족)

사용법:
    validator = PriceValidator()
    
    # AI 분석 완료 후
    result = validator.validate(
        stock_code="005930",
        analysis_price=70000,
        current_price=70500,
        analysis_time=datetime.now() - timedelta(seconds=5)
    )
    
    if result.is_valid:
        # 매수 진행
    else:
        print(f"검증 실패: {result.reason}")
============================================================================
"""

import logging
from typing import Dict, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

# 로거 설정
logger = logging.getLogger('ScalpingBot.Validator')


# =============================================================================
# 상수 설정
# =============================================================================

# 최대 허용 슬리피지 (%)
DEFAULT_MAX_SLIPPAGE = 1.5

# 분석 시효 (초)
DEFAULT_ANALYSIS_TTL = 30

# 최대 허용 스프레드 (%)
DEFAULT_MAX_SPREAD = 1.0

# 최소 거래량 비율
DEFAULT_MIN_VOLUME_RATIO = 0.5


# =============================================================================
# 열거형 및 데이터 클래스
# =============================================================================

class ValidationResult(Enum):
    """검증 결과"""
    VALID = "유효"
    PRICE_UP = "가격 상승"
    PRICE_DOWN = "가격 하락"
    EXPIRED = "시효 만료"
    SPREAD_TOO_WIDE = "스프레드 과다"
    LOW_VOLUME = "거래량 부족"
    INVALID = "검증 실패"


@dataclass
class PriceValidation:
    """가격 검증 결과"""
    stock_code: str
    is_valid: bool
    result: ValidationResult
    reason: str
    
    # 가격 정보
    analysis_price: float = 0.0
    current_price: float = 0.0
    slippage_pct: float = 0.0
    
    # 시간 정보
    elapsed_seconds: float = 0.0
    
    # 추가 정보
    spread_pct: float = 0.0
    volume_ratio: float = 0.0
    
    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            'stock_code': self.stock_code,
            'is_valid': self.is_valid,
            'result': self.result.value,
            'reason': self.reason,
            'analysis_price': self.analysis_price,
            'current_price': self.current_price,
            'slippage_pct': self.slippage_pct,
            'elapsed_seconds': self.elapsed_seconds,
            'spread_pct': self.spread_pct,
            'volume_ratio': self.volume_ratio,
        }


# =============================================================================
# 가격 검증기 클래스
# =============================================================================

class PriceValidator:
    """
    가격 검증기
    
    AI 분석 후 실제 매수 전에 가격 유효성을 검증합니다.
    급등 종목 추격 매수를 방지합니다.
    """
    
    def __init__(
        self,
        max_slippage: float = DEFAULT_MAX_SLIPPAGE,
        analysis_ttl: float = DEFAULT_ANALYSIS_TTL,
        max_spread: float = DEFAULT_MAX_SPREAD,
        min_volume_ratio: float = DEFAULT_MIN_VOLUME_RATIO,
    ):
        """
        초기화
        
        Args:
            max_slippage: 최대 허용 슬리피지 (%)
            analysis_ttl: 분석 시효 (초)
            max_spread: 최대 허용 스프레드 (%)
            min_volume_ratio: 최소 거래량 비율
        """
        self.max_slippage = max_slippage
        self.analysis_ttl = analysis_ttl
        self.max_spread = max_spread
        self.min_volume_ratio = min_volume_ratio
        
        # 통계
        self._stats = {
            'total_validations': 0,
            'passed': 0,
            'failed_slippage': 0,
            'failed_expired': 0,
            'failed_spread': 0,
            'failed_volume': 0,
        }
        
        logger.info(
            f"PriceValidator 초기화 "
            f"(슬리피지: {max_slippage}%, 시효: {analysis_ttl}초)"
        )
    
    # =========================================================================
    # 검증
    # =========================================================================
    
    def validate(
        self,
        stock_code: str,
        analysis_price: float,
        current_price: float,
        analysis_time: datetime = None,
        bid_price: float = None,
        ask_price: float = None,
        volume_ratio: float = None,
    ) -> PriceValidation:
        """
        가격 유효성 검증
        
        Args:
            stock_code: 종목 코드
            analysis_price: AI 분석 시점 가격
            current_price: 현재 가격
            analysis_time: 분석 시점 (기본: 현재)
            bid_price: 매수호가 (선택)
            ask_price: 매도호가 (선택)
            volume_ratio: 거래량 비율 (선택)
        
        Returns:
            PriceValidation 객체
        """
        self._stats['total_validations'] += 1
        
        # 시간 계산
        if analysis_time is None:
            analysis_time = datetime.now()
        
        elapsed = (datetime.now() - analysis_time).total_seconds()
        
        # 슬리피지 계산
        if analysis_price > 0:
            slippage = (current_price - analysis_price) / analysis_price * 100
        else:
            slippage = 0
        
        # 스프레드 계산
        spread_pct = 0.0
        if bid_price and ask_price and bid_price > 0:
            spread_pct = (ask_price - bid_price) / bid_price * 100
        
        # 1. 시효 체크
        if elapsed > self.analysis_ttl:
            self._stats['failed_expired'] += 1
            return PriceValidation(
                stock_code=stock_code,
                is_valid=False,
                result=ValidationResult.EXPIRED,
                reason=f"분석 시효 만료 ({elapsed:.1f}초 > {self.analysis_ttl}초)",
                analysis_price=analysis_price,
                current_price=current_price,
                slippage_pct=slippage,
                elapsed_seconds=elapsed,
            )
        
        # 2. 슬리피지 체크 (급등)
        if slippage > self.max_slippage:
            self._stats['failed_slippage'] += 1
            return PriceValidation(
                stock_code=stock_code,
                is_valid=False,
                result=ValidationResult.PRICE_UP,
                reason=f"가격 급등 ({slippage:+.2f}% > {self.max_slippage}%)",
                analysis_price=analysis_price,
                current_price=current_price,
                slippage_pct=slippage,
                elapsed_seconds=elapsed,
            )
        
        # 3. 슬리피지 체크 (급락) - 선택적
        if slippage < -self.max_slippage * 2:  # 급락은 더 넓게 허용
            self._stats['failed_slippage'] += 1
            return PriceValidation(
                stock_code=stock_code,
                is_valid=False,
                result=ValidationResult.PRICE_DOWN,
                reason=f"가격 급락 ({slippage:+.2f}%)",
                analysis_price=analysis_price,
                current_price=current_price,
                slippage_pct=slippage,
                elapsed_seconds=elapsed,
            )
        
        # 4. 스프레드 체크
        if spread_pct > self.max_spread:
            self._stats['failed_spread'] += 1
            return PriceValidation(
                stock_code=stock_code,
                is_valid=False,
                result=ValidationResult.SPREAD_TOO_WIDE,
                reason=f"스프레드 과다 ({spread_pct:.2f}% > {self.max_spread}%)",
                analysis_price=analysis_price,
                current_price=current_price,
                slippage_pct=slippage,
                elapsed_seconds=elapsed,
                spread_pct=spread_pct,
            )
        
        # 5. 거래량 체크
        if volume_ratio is not None and volume_ratio < self.min_volume_ratio:
            self._stats['failed_volume'] += 1
            return PriceValidation(
                stock_code=stock_code,
                is_valid=False,
                result=ValidationResult.LOW_VOLUME,
                reason=f"거래량 부족 ({volume_ratio:.2f}x < {self.min_volume_ratio}x)",
                analysis_price=analysis_price,
                current_price=current_price,
                slippage_pct=slippage,
                elapsed_seconds=elapsed,
                volume_ratio=volume_ratio,
            )
        
        # 검증 통과
        self._stats['passed'] += 1
        
        return PriceValidation(
            stock_code=stock_code,
            is_valid=True,
            result=ValidationResult.VALID,
            reason=f"검증 통과 (슬리피지: {slippage:+.2f}%, {elapsed:.1f}초)",
            analysis_price=analysis_price,
            current_price=current_price,
            slippage_pct=slippage,
            elapsed_seconds=elapsed,
            spread_pct=spread_pct,
            volume_ratio=volume_ratio or 0,
        )
    
    def quick_validate(
        self,
        analysis_price: float,
        current_price: float,
    ) -> bool:
        """
        빠른 가격 검증 (슬리피지만)
        
        Args:
            analysis_price: AI 분석 시점 가격
            current_price: 현재 가격
        
        Returns:
            True: 유효, False: 무효
        """
        if analysis_price <= 0:
            return False
        
        slippage = (current_price - analysis_price) / analysis_price * 100
        
        return slippage <= self.max_slippage
    
    # =========================================================================
    # AI 결과와 함께 검증
    # =========================================================================
    
    def validate_ai_result(
        self,
        ai_result: Dict[str, Any],
        current_price: float,
        bid_price: float = None,
        ask_price: float = None,
    ) -> PriceValidation:
        """
        AI 분석 결과와 함께 검증
        
        Args:
            ai_result: AI 분석 결과 딕셔너리
                - stock_code: 종목 코드
                - original_price: 분석 시점 가격
                - timestamp: 분석 시점
            current_price: 현재 가격
            bid_price: 매수호가 (선택)
            ask_price: 매도호가 (선택)
        
        Returns:
            PriceValidation 객체
        """
        stock_code = ai_result.get('stock_code', '')
        analysis_price = ai_result.get('original_price', 0)
        timestamp = ai_result.get('timestamp', 0)
        
        # 타임스탬프 변환
        if isinstance(timestamp, (int, float)):
            analysis_time = datetime.fromtimestamp(timestamp)
        elif isinstance(timestamp, datetime):
            analysis_time = timestamp
        else:
            analysis_time = datetime.now()
        
        return self.validate(
            stock_code=stock_code,
            analysis_price=analysis_price,
            current_price=current_price,
            analysis_time=analysis_time,
            bid_price=bid_price,
            ask_price=ask_price,
        )
    
    # =========================================================================
    # 추천 매수가 계산
    # =========================================================================
    
    def get_recommended_buy_price(
        self,
        analysis_price: float,
        current_price: float,
    ) -> float:
        """
        추천 매수가 계산
        
        분석가 + 슬리피지 허용 범위 내에서 매수가를 추천합니다.
        
        Args:
            analysis_price: AI 분석 시점 가격
            current_price: 현재 가격
        
        Returns:
            추천 매수가 (지정가 주문용)
        """
        # 분석가 기준 최대 허용 가격
        max_price = analysis_price * (1 + self.max_slippage / 100)
        
        # 현재가와 최대가 중 작은 값
        recommended = min(current_price, max_price)
        
        return recommended
    
    # =========================================================================
    # 통계
    # =========================================================================
    
    def get_stats(self) -> Dict:
        """통계 조회"""
        total = self._stats['total_validations']
        passed = self._stats['passed']
        
        return {
            **self._stats,
            'pass_rate': (passed / total * 100) if total > 0 else 0,
        }
    
    def reset_stats(self):
        """통계 리셋"""
        for key in self._stats:
            self._stats[key] = 0


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
    print("PriceValidator 테스트")
    print("=" * 60)
    
    validator = PriceValidator(
        max_slippage=1.5,
        analysis_ttl=30,
        max_spread=1.0,
    )
    
    # 1. 정상 케이스
    print("\n1. 정상 케이스 (슬리피지 0.5%):")
    result = validator.validate(
        stock_code="005930",
        analysis_price=70000,
        current_price=70350,  # +0.5%
        analysis_time=datetime.now() - timedelta(seconds=5),
    )
    print(f"   유효: {result.is_valid}")
    print(f"   결과: {result.result.value}")
    print(f"   이유: {result.reason}")
    
    # 2. 급등 케이스
    print("\n2. 급등 케이스 (슬리피지 2.5%):")
    result = validator.validate(
        stock_code="005930",
        analysis_price=70000,
        current_price=71750,  # +2.5%
        analysis_time=datetime.now() - timedelta(seconds=3),
    )
    print(f"   유효: {result.is_valid}")
    print(f"   결과: {result.result.value}")
    print(f"   이유: {result.reason}")
    
    # 3. 시효 만료 케이스
    print("\n3. 시효 만료 케이스 (35초 경과):")
    result = validator.validate(
        stock_code="005930",
        analysis_price=70000,
        current_price=70100,
        analysis_time=datetime.now() - timedelta(seconds=35),
    )
    print(f"   유효: {result.is_valid}")
    print(f"   결과: {result.result.value}")
    print(f"   이유: {result.reason}")
    
    # 4. 스프레드 과다
    print("\n4. 스프레드 과다 케이스:")
    result = validator.validate(
        stock_code="005930",
        analysis_price=70000,
        current_price=70100,
        analysis_time=datetime.now(),
        bid_price=69800,
        ask_price=70800,  # 스프레드 1.4%
    )
    print(f"   유효: {result.is_valid}")
    print(f"   결과: {result.result.value}")
    print(f"   이유: {result.reason}")
    
    # 5. 빠른 검증
    print("\n5. 빠른 검증 (quick_validate):")
    print(f"   70000 → 70500: {validator.quick_validate(70000, 70500)}")  # +0.7%
    print(f"   70000 → 72000: {validator.quick_validate(70000, 72000)}")  # +2.9%
    
    # 6. 추천 매수가
    print("\n6. 추천 매수가:")
    recommended = validator.get_recommended_buy_price(70000, 71000)
    print(f"   분석가 70,000원, 현재가 71,000원 → 추천: {recommended:,.0f}원")
    
    # 7. 통계
    print("\n7. 검증 통계:")
    stats = validator.get_stats()
    for key, value in stats.items():
        if isinstance(value, float):
            print(f"   {key}: {value:.2f}")
        else:
            print(f"   {key}: {value}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
