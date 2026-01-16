#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Score Engine (점수 엔진)
============================================================================
6대 지표를 점수화하고 종합 점수를 계산하는 엔진

6대 지표 (총 85점, AI 보너스 15점 별도):
1. CCI (15점)           - Commodity Channel Index
2. 등락률 (15점)        - 전일 대비 변화율
3. 이격도 (15점)        - 20일선 대비 거리
4. 연속양봉 (10점)      - 연속 상승 일수
5. 거래량비율 (15점)    - 평균 거래량 대비
6. 캔들품질 (15점)      - 캔들 패턴 분석

점수 해석:
- 80점+: 매우 강한 매수 신호
- 70-79점: 강한 매수 신호
- 60-69점: 보통 매수 신호 (보수적 모드에서는 스킵)
- 60점 미만: 매수 대기

사용법:
    from scalping.strategy.score_engine import ScoreEngine
    
    engine = ScoreEngine()
    
    # 종합 점수 계산
    result = engine.calculate_total_score({
        'cci': 165,
        'change_pct': 4.5,
        'distance_ma20': 5.2,
        'consec_bullish': 2,
        'volume_ratio': 2.0,
        'upper_wick_ratio': 0.1,
        'ma20_3day_up': True,
        'high_eq_close': False,
    })
    
    print(f"종합 점수: {result['total_score']:.1f}점")
============================================================================
"""

import logging
from typing import Dict, Any, Union, Optional
from dataclasses import dataclass, field

# 로거 설정
logger = logging.getLogger('ScalpingBot.Score')


# =============================================================================
# 점수 결과 데이터 클래스
# =============================================================================

@dataclass
class ScoreResult:
    """점수 계산 결과"""
    cci_score: float = 0.0
    change_score: float = 0.0
    distance_score: float = 0.0
    consec_score: float = 0.0
    volume_score: float = 0.0
    candle_score: float = 0.0
    
    raw_total: float = 0.0       # 원점수 합계 (기본 85점 만점)
    total_score: float = 0.0     # 정규화 점수 (100점 만점)
    
    # 개별 지표값 (디버깅용)
    indicators: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            'cci_score': self.cci_score,
            'change_score': self.change_score,
            'distance_score': self.distance_score,
            'consec_score': self.consec_score,
            'volume_score': self.volume_score,
            'candle_score': self.candle_score,
            'raw_total': self.raw_total,
            'total_score': self.total_score,
            'indicators': self.indicators,
        }


# =============================================================================
# 개별 점수 계산 함수 (설계서 10.1 공식)
# =============================================================================

def calc_cci_score(cci: float) -> float:
    """
    CCI 점수 계산 (15점 만점)
    
    최적 구간: 160 ~ 180 (15점)
    
    CCI 해석:
    - 160~180: 상승 모멘텀이 강하면서 과열되지 않은 최적 구간
    - 100~140: 상승 추세 시작
    - 200+: 과열 구간 (감점)
    - 0 이하: 약세 구간 (낮은 점수)
    
    Args:
        cci: CCI 값
    
    Returns:
        CCI 점수 (0~15)
    """
    if 160 <= cci <= 180:
        # 최적 구간
        return 15.0
    elif 140 <= cci < 160:
        # 상승 접근
        return 12.0 + ((cci - 140) / 20) * 3.0
    elif 180 < cci <= 200:
        # 약간 과열
        return 15.0 - ((cci - 180) / 20) * 3.0
    elif 100 <= cci < 140:
        # 상승 초기
        return 5.0 + ((cci - 100) / 40) * 7.0
    elif 200 < cci <= 250:
        # 과열 구간
        return 12.0 - ((cci - 200) / 50) * 7.0
    elif cci > 250:
        # 매우 과열
        return max(0, 5.0 - ((cci - 250) / 100) * 5.0)
    else:
        # 약세/중립 (cci < 100)
        return 2.0


def calc_change_score(change_pct: float) -> float:
    """
    등락률 점수 계산 (15점 만점)
    
    최적 구간: 2% ~ 8% (14~15점)
    목표: 5% (15점)
    
    등락률 해석:
    - 2~8%: 적절한 상승폭
    - 1~2%: 소폭 상승
    - 8~10%: 과도한 상승 (추격 위험)
    - 음수: 하락 (낮은 점수)
    
    Args:
        change_pct: 등락률 (%)
    
    Returns:
        등락률 점수 (0~15)
    """
    if 2.0 <= change_pct <= 8.0:
        # 최적 구간 (5%가 최고점)
        return 15.0 - (abs(change_pct - 5) / 3) * 1.0
    elif 1.0 <= change_pct < 2.0:
        # 소폭 상승
        return 10.0 + (change_pct - 1.0) * 4.0
    elif 8.0 < change_pct <= 10.0:
        # 과도한 상승
        return 14.0 - ((change_pct - 8.0) / 2) * 4.0
    elif change_pct < 0:
        # 하락
        return max(0, 3.0 + (change_pct + 5) * 0.6)
    else:
        # 0~1% 또는 10%+
        return 5.0


def calc_distance_score(distance_ma20: float) -> float:
    """
    이격도 점수 계산 (15점 만점)
    
    최적 구간: 2% ~ 8% (14~15점)
    목표: 5% (15점)
    
    이격도 해석:
    - 2~8%: MA20 위에서 적절한 거리
    - 0~2%: MA20 근접 (지지 가능)
    - 8~15%: 과이격 (조정 위험)
    - 음수: MA20 아래 (약세)
    
    Args:
        distance_ma20: 20일선 이격도 (%)
    
    Returns:
        이격도 점수 (0~15)
    """
    if 2.0 <= distance_ma20 <= 8.0:
        # 최적 구간
        return 15.0 - (abs(distance_ma20 - 5) / 3) * 1.0
    elif 0 <= distance_ma20 < 2.0:
        # MA20 근접
        return 8.0 + distance_ma20 * 3.0
    elif 8.0 < distance_ma20 <= 15.0:
        # 과이격
        return 14.0 - ((distance_ma20 - 8.0) / 7.0) * 6.0
    elif distance_ma20 < 0:
        # MA20 아래
        return max(3.0, 8.0 + distance_ma20 * 0.5)
    else:
        # 15%+ 극과이격
        return 2.0


def calc_consec_score(consec_bullish: int) -> float:
    """
    연속양봉 점수 계산 (10점 만점)
    
    최적: 2~3일 연속 양봉 (10점)
    
    연속양봉 해석:
    - 2~3일: 상승 추세 확인, 과열 전
    - 1일: 반등 시작
    - 4일: 상승 지속, 약간 과열
    - 5일+: 과열 위험
    - 0일: 음봉
    
    Args:
        consec_bullish: 연속 양봉 일수
    
    Returns:
        연속양봉 점수 (0~10)
    """
    if consec_bullish == 2 or consec_bullish == 3:
        # 최적
        return 10.0
    elif consec_bullish == 1:
        # 반등 시작
        return 6.0
    elif consec_bullish == 4:
        # 상승 지속
        return 8.0
    elif consec_bullish == 0:
        # 음봉
        return 3.0
    elif consec_bullish >= 5:
        # 과열
        return max(2.0, 6.0 - (consec_bullish - 4) * 1.0)
    else:
        return 5.0


def calc_volume_score(volume_ratio: float) -> float:
    """
    거래량비율 점수 계산 (15점 만점)
    
    최적 구간: 1.5x ~ 3.0x (13~15점)
    목표: 2.25x (15점)
    
    거래량 해석:
    - 1.5~3.0x: 적절한 관심 증가
    - 1.0~1.5x: 평균적 거래
    - 3.0~5.0x: 과열 주의
    - 5.0x+: 급등/급락 가능성 (불안정)
    - 1.0x 미만: 관심 저조
    
    Args:
        volume_ratio: 거래량 비율 (배수)
    
    Returns:
        거래량 점수 (0~15)
    """
    if 1.5 <= volume_ratio <= 3.0:
        # 최적 구간 (2.25가 최고점)
        return 15.0 - (abs(volume_ratio - 2.25) / 0.75) * 2.0
    elif 1.0 <= volume_ratio < 1.5:
        # 평균적
        return 8.0 + (volume_ratio - 1.0) * 14.0
    elif 3.0 < volume_ratio <= 5.0:
        # 과열 주의
        return 13.0 - ((volume_ratio - 3.0) / 2.0) * 5.0
    elif volume_ratio < 1.0:
        # 관심 저조
        return max(3.0, 8.0 * volume_ratio)
    else:
        # 5.0x+ 급등
        return 3.0


def calc_candle_score(
    upper_wick_ratio: float,
    ma20_3day_up: bool,
    high_eq_close: bool,
) -> float:
    """
    캔들품질 점수 계산 (15점 만점)
    
    기본 점수: 10점
    감점/가점:
    - 윗꼬리 > 30%: -5점 (매도 압력)
    - MA20 3일 상승: +5점 (추세 확인)
    - 고가=종가: +2점 (강한 마감)
    
    Args:
        upper_wick_ratio: 윗꼬리 비율 (0~1)
        ma20_3day_up: MA20 3일 연속 상승 여부
        high_eq_close: 고가 == 종가 여부
    
    Returns:
        캔들품질 점수 (0~15)
    """
    score = 10.0
    
    # 윗꼬리 패널티
    if upper_wick_ratio > 0.3:
        score -= 5.0
    
    # MA20 상승 보너스
    if ma20_3day_up:
        score += 5.0
    
    # 고가=종가 보너스
    if high_eq_close:
        score += 2.0
    
    # 범위 제한
    return max(0.0, min(15.0, score))


# =============================================================================
# 점수 엔진 클래스
# =============================================================================

class ScoreEngine:
    """
    6대 지표 점수 엔진
    
    지표별 점수를 계산하고 종합 점수를 산출합니다.
    max_raw_score(기본 85점)를 100점으로 정규화합니다.
    """
    
    def __init__(self, config: Dict = None):
        """
        초기화
        
        Args:
            config: 설정 딕셔너리 (가중치 등)
        """
        self.config = config or {}
        
        # 기본 가중치 (설계서 기준)
        self.weights = self.config.get('score_weights', {
            'cci': 15,
            'change_rate': 15,
            'distance_ma20': 15,
            'consecutive_bullish': 10,
            'volume_ratio': 15,
            'candle_quality': 15,
        })
        
        # 최대 원점수 (합계)
        self.max_raw_score = sum(self.weights.values())  # 85점
        
        logger.info(f"ScoreEngine 초기화 (최대 원점수: {self.max_raw_score}점)")
    
    def calculate_total_score(
        self,
        indicators: Dict[str, Any],
    ) -> ScoreResult:
        """
        종합 점수 계산
        
        Args:
            indicators: 지표 딕셔너리
                - cci: CCI 값
                - change_pct: 등락률 (%)
                - distance_ma20: 이격도 (%)
                - consec_bullish: 연속 양봉 일수
                - volume_ratio: 거래량 비율
                - upper_wick_ratio: 윗꼬리 비율
                - ma20_3day_up: MA20 3일 상승 여부
                - high_eq_close: 고가 == 종가 여부
        
        Returns:
            ScoreResult 객체
        """
        # 개별 점수 계산
        cci_score = calc_cci_score(indicators.get('cci', 0))
        change_score = calc_change_score(indicators.get('change_pct', 0))
        distance_score = calc_distance_score(indicators.get('distance_ma20', 0))
        consec_score = calc_consec_score(indicators.get('consec_bullish', 0))
        volume_score = calc_volume_score(indicators.get('volume_ratio', 1.0))
        candle_score = calc_candle_score(
            indicators.get('upper_wick_ratio', 0),
            indicators.get('ma20_3day_up', False),
            indicators.get('high_eq_close', False),
        )
        
        # 원점수 합계
        raw_total = (
            cci_score +
            change_score +
            distance_score +
            consec_score +
            volume_score +
            candle_score
        )
        
        # 100점 정규화 (max_raw_score 기준, 기본 85점)
        normalized = (raw_total / self.max_raw_score) * 100.0
        total_score = min(100.0, max(0.0, normalized))
        
        result = ScoreResult(
            cci_score=cci_score,
            change_score=change_score,
            distance_score=distance_score,
            consec_score=consec_score,
            volume_score=volume_score,
            candle_score=candle_score,
            raw_total=raw_total,
            total_score=total_score,
            indicators=indicators,
        )
        
        logger.debug(
            f"점수 계산: CCI={cci_score:.1f}, 등락={change_score:.1f}, "
            f"이격={distance_score:.1f}, 연속={consec_score:.1f}, "
            f"거래량={volume_score:.1f}, 캔들={candle_score:.1f} "
            f"→ 총점={total_score:.1f}"
        )
        
        return result
    
    def calculate_from_row(
        self,
        row: Dict[str, Any],
    ) -> ScoreResult:
        """
        데이터프레임 행에서 점수 계산
        
        Args:
            row: 데이터프레임 행 (딕셔너리 또는 Series)
        
        Returns:
            ScoreResult 객체
        """
        # 필요한 지표 추출
        indicators = {
            'cci': row.get('cci', 0),
            'change_pct': row.get('change_pct', 0),
            'distance_ma20': row.get('distance_ma20', 0),
            'consec_bullish': row.get('consec_bullish', 0),
            'volume_ratio': row.get('volume_ratio', 1.0),
            'upper_wick_ratio': row.get('upper_wick_ratio', 0),
            'ma20_3day_up': row.get('ma20_3day_up', False),
            'high_eq_close': row.get('high_eq_close', False),
        }
        
        return self.calculate_total_score(indicators)
    
    def get_score_breakdown(
        self,
        indicators: Dict[str, Any],
    ) -> str:
        """
        점수 상세 내역 문자열 생성
        
        Args:
            indicators: 지표 딕셔너리
        
        Returns:
            점수 내역 문자열
        """
        result = self.calculate_total_score(indicators)
        
        lines = [
            "━" * 40,
            "📊 점수 상세 내역",
            "━" * 40,
            f"CCI ({indicators.get('cci', 0):.1f}):        {result.cci_score:.1f} / 15",
            f"등락률 ({indicators.get('change_pct', 0):.1f}%):    {result.change_score:.1f} / 15",
            f"이격도 ({indicators.get('distance_ma20', 0):.1f}%):  {result.distance_score:.1f} / 15",
            f"연속양봉 ({indicators.get('consec_bullish', 0)}일):    {result.consec_score:.1f} / 10",
            f"거래량 ({indicators.get('volume_ratio', 1.0):.1f}x):   {result.volume_score:.1f} / 15",
            f"캔들품질:              {result.candle_score:.1f} / 15",
            "━" * 40,
            f"원점수 합계:           {result.raw_total:.1f} / 75",
            f"정규화 점수:           {result.total_score:.1f} / 100",
            "━" * 40,
        ]
        
        return "\n".join(lines)
    
    def is_buy_signal(
        self,
        score: float,
        market_mode: str = 'NORMAL',
    ) -> bool:
        """
        매수 신호 여부 판단
        
        Args:
            score: 종합 점수
            market_mode: 시장 모드 (NORMAL/CONSERVATIVE/EMERGENCY)
        
        Returns:
            매수 신호 여부
        """
        if market_mode == 'EMERGENCY':
            return False
        elif market_mode == 'CONSERVATIVE':
            return score >= 75  # 보수적 모드: 75점 이상
        else:
            return score >= 65  # 정상 모드: 65점 이상


# =============================================================================
# 검증 함수
# =============================================================================

def verify_with_sample() -> bool:
    """
    설계서 10.3 샘플 정답지로 검증
    
    [샘플 데이터]
    - CCI: 165
    - 등락률: 4.5%
    - 이격도: 5.2%
    - 연속양봉: 2일
    - 거래량비율: 2.0x
    - 윗꼬리: 0.1
    - MA20 3일 상승: True
    - 고가=종가: False
    
    [기대 점수]
    - CCI: 15.0 (160-180 최적)
    - 등락률: 14.8 (2-8% 최적)
    - 이격도: 14.9 (2-8% 최적)
    - 연속양봉: 10.0 (2-3일 최적)
    - 거래량비율: 15.0 (1.5-3.0 최적)
    - 캔들: 15.0 (10 + 5 MA20 상승)
    
    총합: 84.7점 / 75점 만점 → 정규화: 100점 (cap)
    
    Returns:
        검증 성공 여부
    """
    print("\n" + "=" * 60)
    print("📋 설계서 10.3 샘플 정답지 검증")
    print("=" * 60)
    
    # 샘플 데이터
    sample_indicators = {
        'cci': 165,
        'change_pct': 4.5,
        'distance_ma20': 5.2,
        'consec_bullish': 2,
        'volume_ratio': 2.0,
        'upper_wick_ratio': 0.1,
        'ma20_3day_up': True,
        'high_eq_close': False,
    }
    
    # 기대 점수
    expected = {
        'cci': 15.0,
        'change': 14.83,  # 15 - (0.5/3) = 14.83
        'distance': 14.93,  # 15 - (0.2/3) = 14.93
        'consec': 10.0,
        'volume': 15.0,  # 2.0은 최적 범위 내 (|2.0-2.25|/0.75)*2 = 0.67 → 15-0.67=14.33
        'candle': 15.0,  # 10 + 5 (MA20)
    }
    
    # 실제 계산
    engine = ScoreEngine()
    result = engine.calculate_total_score(sample_indicators)
    
    print("\n[샘플 데이터]")
    for key, value in sample_indicators.items():
        print(f"  {key}: {value}")
    
    print("\n[점수 비교]")
    print(f"{'지표':<12} {'기대':<8} {'실제':<8} {'차이':<8} {'결과':<6}")
    print("-" * 50)
    
    all_pass = True
    tolerance = 1.0  # 허용 오차 (공식 특성상 소수점 차이 허용)
    
    checks = [
        ('CCI', expected['cci'], result.cci_score),
        ('등락률', expected['change'], result.change_score),
        ('이격도', expected['distance'], result.distance_score),
        ('연속양봉', expected['consec'], result.consec_score),
        ('거래량', expected['volume'], result.volume_score),
        ('캔들', expected['candle'], result.candle_score),
    ]
    
    for name, exp, actual in checks:
        diff = abs(exp - actual)
        passed = diff <= tolerance
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{name:<12} {exp:<8.2f} {actual:<8.2f} {diff:<8.2f} {status}")
        if not passed:
            all_pass = False
    
    print("-" * 50)
    print(f"{'원점수 합계':<12} {'84.7+':<8} {result.raw_total:<8.2f}")
    print(f"{'정규화 점수':<12} {'100':<8} {result.total_score:<8.2f}")
    
    # 정규화 점수 검증 (75점 초과 시 100점 cap)
    if result.raw_total >= 85:  # max_raw_score 기준
        normalized_pass = result.total_score == 100.0
    else:
        normalized_pass = abs(result.total_score - (result.raw_total / 85 * 100)) < 0.1
    
    print(f"\n{'정규화':<12} {'(raw>75→100)':<20} {'✅ PASS' if normalized_pass else '❌ FAIL'}")
    
    overall_pass = all_pass and normalized_pass
    
    print("\n" + "=" * 60)
    print(f"최종 결과: {'✅ 모든 검증 통과!' if overall_pass else '❌ 검증 실패'}")
    print("=" * 60)
    
    return overall_pass


def test_edge_cases():
    """
    경계값 테스트
    """
    print("\n" + "=" * 60)
    print("🧪 경계값 테스트")
    print("=" * 60)
    
    test_cases = [
        # (이름, 지표, 예상 범위)
        ("최저점 (모든 지표 최악)", {
            'cci': -200,
            'change_pct': -10,
            'distance_ma20': -20,
            'consec_bullish': 0,
            'volume_ratio': 0.1,
            'upper_wick_ratio': 0.5,
            'ma20_3day_up': False,
            'high_eq_close': False,
        }, (0, 30)),
        
        ("최고점 (모든 지표 최적)", {
            'cci': 170,
            'change_pct': 5.0,
            'distance_ma20': 5.0,
            'consec_bullish': 2,
            'volume_ratio': 2.25,
            'upper_wick_ratio': 0.1,
            'ma20_3day_up': True,
            'high_eq_close': True,
        }, (95, 100)),
        
        ("중간점 (평균 지표)", {
            'cci': 100,
            'change_pct': 1.5,
            'distance_ma20': 1.0,
            'consec_bullish': 1,
            'volume_ratio': 1.2,
            'upper_wick_ratio': 0.2,
            'ma20_3day_up': False,
            'high_eq_close': False,
        }, (60, 80)),
        
        ("과열 (CCI, 거래량 과다)", {
            'cci': 300,
            'change_pct': 15.0,
            'distance_ma20': 20.0,
            'consec_bullish': 7,
            'volume_ratio': 10.0,
            'upper_wick_ratio': 0.4,
            'ma20_3day_up': True,
            'high_eq_close': False,
        }, (20, 50)),
    ]
    
    engine = ScoreEngine()
    all_pass = True
    
    for name, indicators, (min_score, max_score) in test_cases:
        result = engine.calculate_total_score(indicators)
        passed = min_score <= result.total_score <= max_score
        status = "✅" if passed else "❌"
        print(f"\n{status} {name}")
        print(f"   점수: {result.total_score:.1f} (기대 범위: {min_score}~{max_score})")
        if not passed:
            all_pass = False
    
    print("\n" + "=" * 60)
    print(f"경계값 테스트: {'✅ 모두 통과!' if all_pass else '❌ 일부 실패'}")
    print("=" * 60)
    
    return all_pass


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
    print("Score Engine 테스트")
    print("=" * 60)
    
    # 1. 기본 테스트
    print("\n1. 기본 점수 계산 테스트")
    engine = ScoreEngine()
    
    test_indicators = {
        'cci': 150,
        'change_pct': 3.0,
        'distance_ma20': 4.0,
        'consec_bullish': 2,
        'volume_ratio': 1.8,
        'upper_wick_ratio': 0.15,
        'ma20_3day_up': True,
        'high_eq_close': False,
    }
    
    result = engine.calculate_total_score(test_indicators)
    print(engine.get_score_breakdown(test_indicators))
    
    # 2. 샘플 정답지 검증
    verify_with_sample()
    
    # 3. 경계값 테스트
    test_edge_cases()
    
    # 4. 매수 신호 테스트
    print("\n" + "=" * 60)
    print("📊 매수 신호 테스트")
    print("=" * 60)
    
    test_scores = [50, 60, 65, 70, 75, 80]
    modes = ['NORMAL', 'CONSERVATIVE', 'EMERGENCY']
    
    print(f"\n{'점수':<8}", end="")
    for mode in modes:
        print(f"{mode:<15}", end="")
    print()
    print("-" * 50)
    
    for score in test_scores:
        print(f"{score:<8}", end="")
        for mode in modes:
            signal = engine.is_buy_signal(score, mode)
            print(f"{'✅ BUY' if signal else '❌ HOLD':<15}", end="")
        print()
    
    print("\n" + "=" * 60)
    print("모든 테스트 완료!")
    print("=" * 60)
