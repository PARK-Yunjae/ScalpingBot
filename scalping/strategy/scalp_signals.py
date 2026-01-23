#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v3.0 - Scalp Signals (스캘핑 시그널 생성)
============================================================================
분봉 지표 기반 매수/매도 시그널 생성

전략:
1. 돌파 매수 (Breakout): 고점 돌파 + 거래량 급증
2. 풀백 매수 (Pullback): 상승 후 조정에서 매수
3. 갭 플레이 (Gap Play): 갭 상승 후 첫 조정에서 매수

사용법:
    from scalping.strategy.scalp_signals import ScalpSignalGenerator
    
    gen = ScalpSignalGenerator(config)
    signal = gen.evaluate(stock_code, indicators, context)
============================================================================
"""

import logging
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, time

from scalping.strategy.minute_indicators import MinuteIndicatorResult

logger = logging.getLogger('ScalpingBot.ScalpSignals')


# =============================================================================
# 상수 & 설정
# =============================================================================

# 거래 비용 (슬리피지 + 수수료 + 세금)
TOTAL_COST = 0.54  # %

# 손절/익절 기본값
DEFAULT_STOP_LOSS = -0.7      # 손절선 (%)
DEFAULT_TAKE_PROFIT_1 = 1.5   # 1차 익절 (%)
DEFAULT_TAKE_PROFIT_2 = 2.5   # 2차 익절 (%)
DEFAULT_TRAILING_START = 0.5  # 트레일링 시작 (%)
DEFAULT_TRAILING_STOP = 0.4   # 트레일링 스탑 폭 (%)

# 시그널 조건 기본값
class SignalParams:
    """시그널 파라미터"""
    # 돌파 매수
    BREAKOUT_CCI_MIN = 100
    BREAKOUT_RSI_MAX = 80
    BREAKOUT_VOLUME_MIN = 2.0  # 평균의 2배
    
    # 풀백 매수
    PULLBACK_MIN_RISE = 2.0     # 최소 상승폭 (%)
    PULLBACK_CORRECTION_MIN = -0.5  # 최소 조정폭 (%)
    PULLBACK_CORRECTION_MAX = -1.5  # 최대 조정폭 (%)
    PULLBACK_RSI_MIN = 40
    PULLBACK_VOLUME_DECREASE = 0.7  # 거래량 감소 기준
    
    # 갭 플레이
    GAP_MIN = 0.5   # 최소 갭 (%)
    GAP_MAX = 3.0   # 최대 갭 (%)
    GAP_PULLBACK_MIN = -0.3  # 갭 풀백 최소 (%)
    GAP_PULLBACK_MAX = -1.0  # 갭 풀백 최대 (%)
    
    # 공통 필터
    VWAP_ABOVE_REQUIRED = True  # VWAP 위 필수
    MIN_SCORE = 80              # v3.2.3 기본값 (실전 데이터 기준)


# =============================================================================
# 데이터 클래스
# =============================================================================

class SignalType(Enum):
    """시그널 타입"""
    NONE = "none"
    BREAKOUT = "breakout"     # 돌파 매수
    PULLBACK = "pullback"     # 풀백 매수
    GAP_PLAY = "gap_play"     # 갭 플레이
    VWAP_BOUNCE = "vwap_bounce"  # VWAP 바운스


class SignalStrength(Enum):
    """시그널 강도"""
    WEAK = "weak"       # 약함 (40~54점)
    MEDIUM = "medium"   # 보통 (55~69점)
    STRONG = "strong"   # 강함 (70~84점)
    VERY_STRONG = "very_strong"  # 매우 강함 (85+점)


@dataclass
class ScalpSignal:
    """스캘핑 시그널"""
    stock_code: str
    signal_type: SignalType = SignalType.NONE
    action: str = "HOLD"  # BUY / HOLD / SKIP
    
    # 점수
    score: float = 0.0
    strength: SignalStrength = SignalStrength.WEAK
    
    # 가격 정보
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    
    # 구조 기반 손절용 (신규)
    breakout_level: float = 0.0     # 돌파 기준가 (이 가격 아래로 복귀 시 손절)
    vwap_at_entry: float = 0.0      # 진입 시점 VWAP (이탈 시 손절)
    
    # 세부 점수
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    
    # 이유
    reason: str = ""
    warnings: List[str] = field(default_factory=list)
    
    # 지표 스냅샷
    indicators: Dict[str, Any] = field(default_factory=dict)
    
    # 타임스탬프
    timestamp: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'stock_code': self.stock_code,
            'signal_type': self.signal_type.value,
            'action': self.action,
            'score': self.score,
            'strength': self.strength.value,
            'entry_price': self.entry_price,
            'stop_loss': self.stop_loss,
            'take_profit_1': self.take_profit_1,
            'take_profit_2': self.take_profit_2,
            'breakout_level': self.breakout_level,
            'vwap_at_entry': self.vwap_at_entry,
            'score_breakdown': self.score_breakdown,
            'reason': self.reason,
            'warnings': self.warnings,
            'timestamp': self.timestamp,
        }


@dataclass
class MarketContext:
    """시장 컨텍스트"""
    # 전일 정보
    prev_close: float = 0.0
    prev_high: float = 0.0
    prev_low: float = 0.0
    prev_volume: int = 0
    
    # 지수 정보
    kospi_change_pct: float = 0.0
    kosdaq_change_pct: float = 0.0
    
    # 시간대
    current_time: time = None
    market_phase: str = "NORMAL"  # OPENING, NORMAL, CLOSING
    
    # 모드
    conservative_mode: bool = False
    emergency_mode: bool = False


# =============================================================================
# 스캘핑 시그널 생성기
# =============================================================================

class ScalpSignalGenerator:
    """
    스캘핑 시그널 생성기
    
    분봉 지표를 분석하여 매수 시그널을 생성합니다.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Args:
            config: 설정 딕셔너리
        """
        self.config = config or {}
        
        # 손절/익절 설정
        trading_config = self.config.get('trading', {})
        self.stop_loss = trading_config.get('stop_loss', DEFAULT_STOP_LOSS)
        self.take_profit_1 = trading_config.get('take_profit_1', DEFAULT_TAKE_PROFIT_1)
        self.take_profit_2 = trading_config.get('take_profit_2', DEFAULT_TAKE_PROFIT_2)
        self.trailing_start = trading_config.get('trailing_start', DEFAULT_TRAILING_START)
        self.trailing_stop = trading_config.get('trailing_stop', DEFAULT_TRAILING_STOP)
        
        # 점수 임계값
        self.min_score = trading_config.get('min_score', SignalParams.MIN_SCORE)
        self.min_score_conservative = self.min_score + 10
        
        # 🆕 v3.2 Soft Scoring 설정
        scoring_config = self.config.get('scoring', {})
        penalties = scoring_config.get('penalties', {})
        bonuses = scoring_config.get('bonuses', {})
        
        # 감점 설정
        self.cci_overheat_threshold = penalties.get('cci_overheat_threshold', 150)
        self.cci_overheat_penalty = penalties.get('cci_overheat_penalty', -10)
        self.cci_extreme_threshold = penalties.get('cci_extreme_threshold', 200)
        self.cci_extreme_penalty = penalties.get('cci_extreme_penalty', -20)
        
        self.rsi_overbought_threshold = penalties.get('rsi_overbought_threshold', 80)
        self.rsi_overbought_penalty = penalties.get('rsi_overbought_penalty', -10)
        self.rsi_extreme_threshold = penalties.get('rsi_extreme_threshold', 85)
        self.rsi_extreme_penalty = penalties.get('rsi_extreme_penalty', -15)
        
        self.below_vwap_penalty = penalties.get('below_vwap_penalty', -15)
        self.ema_bearish_penalty = penalties.get('ema_bearish_penalty', -5)
        self.low_volume_threshold = penalties.get('low_volume_threshold', 1.5)
        self.low_volume_penalty = penalties.get('low_volume_penalty', -10)
        
        # 가점 설정
        self.ema_bullish_bonus = bonuses.get('ema_bullish', 10)
        self.high_volume_threshold = bonuses.get('high_volume_threshold', 3.0)
        self.high_volume_bonus = bonuses.get('high_volume_bonus', 15)
        self.vwap_strong_threshold = bonuses.get('vwap_strong_threshold', 1.0)
        self.vwap_strong_bonus = bonuses.get('vwap_strong_bonus', 10)
        
        # 🆕 v3.2 Hard Firewall 설정
        firewall_config = self.config.get('hard_firewall', {})
        self.min_volume_ratio = firewall_config.get('min_volume_ratio', 0.5)
        self.max_day_change_pct = firewall_config.get('max_day_change_pct', 15.0)
        
        # 전략 활성화 설정
        indicators_config = self.config.get('indicators', {})
        strategies_config = indicators_config.get('strategies', {})
        self.enable_breakout = strategies_config.get('breakout', True)
        self.enable_pullback = strategies_config.get('pullback', True)
        self.enable_gap_play = strategies_config.get('gap_play', True)
        self.enable_vwap_bounce = strategies_config.get('vwap_bounce', True)
        
        # 비활성화된 전략 로그
        disabled = []
        if not self.enable_breakout: disabled.append('breakout')
        if not self.enable_pullback: disabled.append('pullback')
        if not self.enable_gap_play: disabled.append('gap_play')
        if not self.enable_vwap_bounce: disabled.append('vwap_bounce')
        
        logger.info(f"ScalpSignalGenerator 초기화 "
                   f"(손절:{self.stop_loss}%, 익절1:{self.take_profit_1}%, "
                   f"최소점수:{self.min_score})")
        if disabled:
            logger.info(f"   ⚠️ 비활성화 전략: {', '.join(disabled)}")
    
    def set_min_score(self, min_score: int):
        """
        🆕 v3.2.3 AdaptiveMode에서 min_score 동적 업데이트
        
        Args:
            min_score: 새로운 최소 점수
        """
        old_score = self.min_score
        self.min_score = min_score
        self.min_score_conservative = min_score + 10
        
        if old_score != min_score:
            logger.info(f"📊 min_score 변경: {old_score} → {min_score}")
    
    def evaluate(
        self,
        stock_code: str,
        indicators: MinuteIndicatorResult,
        context: MarketContext,
        stock_name: str = "",
    ) -> ScalpSignal:
        """
        시그널 평가 (v3.2)
        
        구조: Hard Firewall → 전략 점수 → Soft Scoring → 최종 판정
        
        Args:
            stock_code: 종목 코드
            indicators: 분봉 지표
            context: 시장 컨텍스트
            stock_name: 종목명
        
        Returns:
            ScalpSignal
        """
        signal = ScalpSignal(
            stock_code=stock_code,
            timestamp=indicators.timestamp,
            entry_price=indicators.price,
        )
        
        # 지표 스냅샷 저장
        signal.indicators = indicators.to_dict()
        
        # 긴급 모드 체크
        if context.emergency_mode:
            signal.action = "SKIP"
            signal.reason = "긴급 모드 - 신규 진입 금지"
            return signal
        
        # 🆕 v3.2 Hard Firewall (최소화: 거래량, 당일급등만)
        if not self._pass_hard_firewall(indicators, context, signal):
            return signal
        
        # 각 전략 평가 (활성화된 전략만)
        strategies = []
        if self.enable_breakout:
            strategies.append(self._evaluate_breakout)
        if self.enable_pullback:
            strategies.append(self._evaluate_pullback)
        if self.enable_gap_play:
            strategies.append(self._evaluate_gap_play)
        if self.enable_vwap_bounce:
            strategies.append(self._evaluate_vwap_bounce)
        
        best_signal = signal
        best_score = 0
        best_breakdown = {}
        best_warnings = []
        
        for strategy_fn in strategies:
            result = strategy_fn(indicators, context)
            if result['score'] > best_score:
                best_score = result['score']
                best_signal.signal_type = result['type']
                best_breakdown = result['breakdown'].copy()
                best_signal.reason = result['reason']
                best_warnings = result.get('warnings', [])
        
        # 🆕 v3.2 Soft Scoring 적용 (CCI, RSI, VWAP, EMA 감점/가점)
        final_score, final_breakdown, soft_warnings = self._apply_soft_scoring(
            indicators, best_score, best_breakdown
        )
        
        best_signal.score = final_score
        best_signal.score_breakdown = final_breakdown
        best_signal.warnings = best_warnings + soft_warnings
        
        # 최종 판정
        min_score = self.min_score_conservative if context.conservative_mode else self.min_score
        
        if best_signal.score >= min_score:
            best_signal.action = "BUY"
            best_signal.strength = self._get_strength(best_signal.score)
            
            # 손절/익절가 계산
            best_signal.stop_loss = indicators.price * (1 + self.stop_loss / 100)
            best_signal.take_profit_1 = indicators.price * (1 + self.take_profit_1 / 100)
            best_signal.take_profit_2 = indicators.price * (1 + self.take_profit_2 / 100)
            
            # 구조 기반 손절용 레벨 설정
            if best_signal.signal_type == SignalType.BREAKOUT:
                best_signal.breakout_level = indicators.day_high * 0.997
            else:
                best_signal.breakout_level = indicators.vwap * 0.995
            
            best_signal.vwap_at_entry = indicators.vwap
        else:
            best_signal.action = "HOLD"
            if best_signal.score > 0:
                best_signal.reason = f"점수 미달 ({best_signal.score:.0f} < {min_score})"
        
        return best_signal
    
    # =========================================================================
    # 🆕 v3.2 Hard Firewall (최소화 - 4개만)
    # =========================================================================
    
    def _pass_hard_firewall(
        self,
        indicators: MinuteIndicatorResult,
        context: MarketContext,
        signal: ScalpSignal,
    ) -> bool:
        """
        Hard Firewall 통과 여부 (v3.2)
        
        최소한의 절대 조건만 체크:
        1. 거래량 0.5배 이상 (유동성)
        2. 당일 +15% 미만 (상한가 위험)
        
        나머지(CCI, RSI, VWAP, EMA)는 점수화!
        """
        
        # 1. 거래량 체크 (유동성 - 절대 필요)
        if indicators.volume_ratio < self.min_volume_ratio:
            signal.action = "SKIP"
            signal.reason = f"[HARD] 거래량 부족 ({indicators.volume_ratio:.2f}x < {self.min_volume_ratio}x)"
            return False
        
        # 2. 당일 급등 체크 (상한가 위험)
        if indicators.day_change_pct > self.max_day_change_pct:
            signal.action = "SKIP"
            signal.reason = f"[HARD] 당일 급등 ({indicators.day_change_pct:.1f}% > {self.max_day_change_pct}%)"
            return False
        
        return True
    
    # =========================================================================
    # 🆕 v3.2 Soft Scoring (감점/가점)
    # =========================================================================
    
    def _apply_soft_scoring(
        self,
        indicators: MinuteIndicatorResult,
        base_score: float,
        breakdown: Dict[str, float],
    ) -> Tuple[float, Dict[str, float], List[str]]:
        """
        Soft Scoring 적용 (v3.2)
        
        기존 하드 필터 → 점수화:
        - CCI 과열 → 감점
        - RSI 과매수 → 감점
        - VWAP 아래 → 감점
        - EMA 역배열 → 감점
        - 거래량 부족 → 감점
        
        가점:
        - EMA 정배열 → 가점
        - 거래량 폭증 → 가점
        - VWAP 상방 강함 → 가점
        """
        score = base_score
        warnings = []
        
        # === 감점 ===
        
        # CCI 과열
        if indicators.cci >= self.cci_extreme_threshold:
            score += self.cci_extreme_penalty
            breakdown['CCI극과열'] = self.cci_extreme_penalty
            warnings.append(f"CCI {indicators.cci:.0f} (극과열)")
        elif indicators.cci >= self.cci_overheat_threshold:
            score += self.cci_overheat_penalty
            breakdown['CCI과열'] = self.cci_overheat_penalty
            warnings.append(f"CCI {indicators.cci:.0f} (과열)")
        
        # RSI 과매수
        if indicators.rsi >= self.rsi_extreme_threshold:
            score += self.rsi_extreme_penalty
            breakdown['RSI극과열'] = self.rsi_extreme_penalty
            warnings.append(f"RSI {indicators.rsi:.1f} (극과열)")
        elif indicators.rsi >= self.rsi_overbought_threshold:
            score += self.rsi_overbought_penalty
            breakdown['RSI과매수'] = self.rsi_overbought_penalty
            warnings.append(f"RSI {indicators.rsi:.1f} (과매수)")
        
        # VWAP 아래
        if indicators.vwap_distance < 0:
            score += self.below_vwap_penalty
            breakdown['VWAP아래'] = self.below_vwap_penalty
            warnings.append(f"VWAP 아래 ({indicators.vwap_distance:.2f}%)")
        
        # EMA 역배열
        if indicators.ema9 > 0 and indicators.ema20 > 0:
            if indicators.ema9 < indicators.ema20:
                score += self.ema_bearish_penalty
                breakdown['EMA역배열'] = self.ema_bearish_penalty
                warnings.append("EMA 역배열")
        
        # 거래량 부족 (0.5~1.5 사이)
        if indicators.volume_ratio < self.low_volume_threshold:
            score += self.low_volume_penalty
            breakdown['거래량부족'] = self.low_volume_penalty
            warnings.append(f"거래량 {indicators.volume_ratio:.1f}x (부족)")
        
        # === 가점 ===
        
        # EMA 정배열
        if indicators.ema9 > 0 and indicators.ema20 > 0:
            if indicators.ema9 >= indicators.ema20:
                score += self.ema_bullish_bonus
                breakdown['EMA정배열'] = self.ema_bullish_bonus
        
        # 거래량 폭증
        if indicators.volume_ratio >= self.high_volume_threshold:
            score += self.high_volume_bonus
            breakdown['거래량폭증'] = self.high_volume_bonus
        
        # VWAP 상방 강함
        if indicators.vwap_distance >= self.vwap_strong_threshold:
            score += self.vwap_strong_bonus
            breakdown['VWAP강함'] = self.vwap_strong_bonus
        
        return max(0, score), breakdown, warnings
    
    # =========================================================================
    # 전략 1: 돌파 매수 (Breakout)
    # =========================================================================
    
    def _evaluate_breakout(
        self,
        indicators: MinuteIndicatorResult,
        context: MarketContext,
    ) -> Dict[str, Any]:
        """
        돌파 매수 전략
        
        조건:
        - 당일 신고가 돌파 (또는 전일 고가 돌파)
        - 거래량 급증
        - CCI > 100
        - RSI < 80
        """
        score = 0
        breakdown = {}
        warnings = []
        
        # 1. 고점 돌파 체크 (당일 고가 근접 = 돌파 시도)
        if indicators.from_day_high_pct >= -0.1:  # 고점 0.1% 이내
            score += 25
            breakdown['고점돌파'] = 25
        elif indicators.from_day_high_pct >= -0.3:
            score += 15
            breakdown['고점근접'] = 15
        
        # 2. 전일 고가 돌파
        if context.prev_high > 0 and indicators.price > context.prev_high:
            score += 20
            breakdown['전일고가돌파'] = 20
        
        # 3. 거래량 조건
        if indicators.volume_ratio >= 3.0:
            score += 20
            breakdown['거래량폭증'] = 20
        elif indicators.volume_ratio >= SignalParams.BREAKOUT_VOLUME_MIN:
            score += 15
            breakdown['거래량증가'] = 15
        elif indicators.volume_ratio >= 1.5:
            score += 8
            breakdown['거래량보통'] = 8
        
        # 4. CCI 모멘텀 (v3.2: 차단 제거, 감점은 Soft Scoring에서 처리)
        if indicators.cci >= SignalParams.BREAKOUT_CCI_MIN:
            score += 15
            breakdown['CCI적정'] = 15
        elif indicators.cci >= 50:
            score += 8
            breakdown['CCI약함'] = 8
        # CCI 과열 감점은 _apply_soft_scoring에서 처리
        
        # 5. RSI 적정 (v3.2: 차단 제거, 감점은 Soft Scoring에서 처리)
        if 50 <= indicators.rsi <= 70:
            score += 10
            breakdown['RSI적정'] = 10
        elif indicators.rsi < 50:
            score += 5
            breakdown['RSI낮음'] = 5
            warnings.append("RSI가 낮아 모멘텀 부족 가능")
        # RSI 과열 감점은 _apply_soft_scoring에서 처리
        
        # 6. VWAP 위치 (v3.2: VWAP 아래도 진입 가능, 감점은 Soft Scoring에서)
        if indicators.vwap_distance > 1.0:
            score += 10
            breakdown['VWAP상방'] = 10
        elif indicators.vwap_distance > 0:
            score += 5
            breakdown['VWAP위'] = 5
        # VWAP 아래 감점은 _apply_soft_scoring에서 처리
        
        # 7. 양봉 확인
        if indicators.is_bullish and indicators.body_ratio > 0.5:
            score += 5
            breakdown['강한양봉'] = 5
        
        # EMA 정배열/역배열은 _apply_soft_scoring에서 처리
        
        return {
            'type': SignalType.BREAKOUT,
            'score': max(0, score),
            'breakdown': breakdown,
            'reason': f"돌파 매수 (CCI:{indicators.cci:.0f}, 거래량:{indicators.volume_ratio:.1f}x)",
            'warnings': warnings,
        }
    
    # =========================================================================
    # 전략 2: 풀백 매수 (Pullback)
    # =========================================================================
    
    def _evaluate_pullback(
        self,
        indicators: MinuteIndicatorResult,
        context: MarketContext,
    ) -> Dict[str, Any]:
        """
        풀백 매수 전략
        
        조건:
        - 당일 +2% 이상 상승 이력
        - 고점 대비 -0.5% ~ -1.5% 조정
        - 거래량 감소 (건강한 조정)
        - VWAP 위
        """
        score = 0
        breakdown = {}
        warnings = []
        
        # 1. 당일 상승폭 체크
        if indicators.day_change_pct < SignalParams.PULLBACK_MIN_RISE:
            # 상승폭 부족 → 풀백 전략 해당 없음
            return {
                'type': SignalType.PULLBACK,
                'score': 0,
                'breakdown': {'조건불충족': '당일 상승폭 부족'},
                'reason': "풀백 조건 미충족",
                'warnings': [],
            }
        
        score += 15
        breakdown['당일상승'] = 15
        
        # 2. 조정폭 체크 (고점 대비)
        correction = indicators.from_day_high_pct
        
        if SignalParams.PULLBACK_CORRECTION_MAX <= correction <= SignalParams.PULLBACK_CORRECTION_MIN:
            score += 25
            breakdown['적정조정'] = 25
        elif -0.3 <= correction <= 0:
            # 조정이 너무 얕음
            score += 10
            breakdown['얕은조정'] = 10
            warnings.append("조정폭이 얕음, 추가 조정 가능")
        elif correction < SignalParams.PULLBACK_CORRECTION_MAX:
            # 조정이 너무 깊음
            score += 5
            breakdown['깊은조정'] = 5
            warnings.append("조정폭이 깊음, 추세 약화 가능")
        
        # 3. 거래량 감소 (건강한 조정)
        if indicators.volume_ratio < SignalParams.PULLBACK_VOLUME_DECREASE:
            score += 15
            breakdown['거래량감소'] = 15
        elif indicators.volume_ratio < 1.0:
            score += 10
            breakdown['거래량유지'] = 10
        else:
            # 조정 중 거래량 증가 = 매도 압력
            score -= 5
            breakdown['거래량증가'] = -5
            warnings.append("조정 시 거래량 증가 - 매도 압력")
        
        # 4. RSI 체크 (v3.2: 가점만, 감점은 Soft Scoring)
        if indicators.rsi >= SignalParams.PULLBACK_RSI_MIN:
            score += 10
            breakdown['RSI유지'] = 10
        # RSI 감점은 _apply_soft_scoring에서 처리
        
        # 5. VWAP 위치 (v3.2: 가점만, 감점은 Soft Scoring)
        if indicators.vwap_distance > 0.5:
            score += 15
            breakdown['VWAP상방'] = 15
        elif indicators.vwap_distance > 0:
            score += 10
            breakdown['VWAP위'] = 10
        # VWAP 아래 감점은 _apply_soft_scoring에서 처리
        
        # 6. CCI 체크 (v3.2: 가점만)
        if 50 <= indicators.cci <= 150:
            score += 10
            breakdown['CCI적정'] = 10
        elif indicators.cci > 150:
            score += 5
            breakdown['CCI강함'] = 5
        # CCI 과열 감점은 _apply_soft_scoring에서 처리
        
        # EMA 정배열/역배열은 _apply_soft_scoring에서 처리
        
        return {
            'type': SignalType.PULLBACK,
            'score': max(0, score),
            'breakdown': breakdown,
            'reason': f"풀백 매수 (조정:{correction:.1f}%, 당일:{indicators.day_change_pct:.1f}%)",
            'warnings': warnings,
        }
    
    # =========================================================================
    # 전략 3: 갭 플레이 (Gap Play)
    # =========================================================================
    
    def _evaluate_gap_play(
        self,
        indicators: MinuteIndicatorResult,
        context: MarketContext,
    ) -> Dict[str, Any]:
        """
        갭 플레이 전략 (09:05~09:30 전용)
        
        조건:
        - 갭 상승 +0.5% ~ +3%
        - 첫 조정 발생
        - 반등 시그널
        """
        score = 0
        breakdown = {}
        warnings = []
        
        # 시간대 체크 (09:05~09:30)
        if context.current_time:
            if not (time(9, 5) <= context.current_time <= time(9, 30)):
                return {
                    'type': SignalType.GAP_PLAY,
                    'score': 0,
                    'breakdown': {'시간대': '갭플레이 시간대 아님'},
                    'reason': "갭 플레이 시간대 아님",
                    'warnings': [],
                }
        
        # 갭 크기 계산 (전일 종가 대비 시가)
        if context.prev_close <= 0:
            return {
                'type': SignalType.GAP_PLAY,
                'score': 0,
                'breakdown': {'데이터': '전일 종가 없음'},
                'reason': "전일 종가 데이터 없음",
                'warnings': [],
            }
        
        # 갭 계산 (day_change_pct를 갭으로 사용)
        gap_pct = indicators.day_change_pct
        
        # 1. 갭 크기 체크
        if SignalParams.GAP_MIN <= gap_pct <= SignalParams.GAP_MAX:
            score += 25
            breakdown['적정갭'] = 25
        elif gap_pct > SignalParams.GAP_MAX:
            score += 10
            breakdown['큰갭'] = 10
            warnings.append("갭이 큼, 차익 실현 매물 주의")
        else:
            return {
                'type': SignalType.GAP_PLAY,
                'score': 0,
                'breakdown': {'갭크기': '갭 없음 또는 갭 하락'},
                'reason': "갭 상승 아님",
                'warnings': [],
            }
        
        # 2. 조정 여부 (고점 대비)
        correction = indicators.from_day_high_pct
        
        if SignalParams.GAP_PULLBACK_MAX <= correction <= SignalParams.GAP_PULLBACK_MIN:
            score += 20
            breakdown['갭풀백'] = 20
        elif correction < SignalParams.GAP_PULLBACK_MAX:
            score += 10
            breakdown['깊은풀백'] = 10
            warnings.append("풀백이 깊음, 갭 메우기 가능")
        elif correction > -0.1:
            # 아직 조정 안 옴
            score += 5
            breakdown['조정대기'] = 5
            warnings.append("아직 조정이 오지 않음")
        
        # 3. 거래량
        if indicators.volume_ratio >= 2.0:
            score += 15
            breakdown['거래량강함'] = 15
        elif indicators.volume_ratio >= 1.0:
            score += 10
            breakdown['거래량유지'] = 10
        
        # 4. RSI
        if 40 <= indicators.rsi <= 70:
            score += 10
            breakdown['RSI적정'] = 10
        
        # 5. 양봉 확인 (반등 시그널)
        if indicators.is_bullish:
            score += 10
            breakdown['양봉반등'] = 10
        else:
            score += 0
            breakdown['음봉'] = 0
            warnings.append("아직 양봉 반등 미확인")
        
        return {
            'type': SignalType.GAP_PLAY,
            'score': max(0, score),
            'breakdown': breakdown,
            'reason': f"갭 플레이 (갭:{gap_pct:.1f}%, 풀백:{correction:.1f}%)",
            'warnings': warnings,
        }
    
    # =========================================================================
    # 전략 4: VWAP 바운스
    # =========================================================================
    
    def _evaluate_vwap_bounce(
        self,
        indicators: MinuteIndicatorResult,
        context: MarketContext,
    ) -> Dict[str, Any]:
        """
        VWAP 바운스 전략
        
        조건:
        - VWAP 근접 (0% ~ +0.5%)
        - 양봉 전환
        - 당일 상승 추세
        """
        score = 0
        breakdown = {}
        warnings = []
        
        # 1. VWAP 근접 체크
        if 0 <= indicators.vwap_distance <= 0.3:
            score += 30
            breakdown['VWAP근접'] = 30
        elif -0.2 <= indicators.vwap_distance < 0:
            score += 20
            breakdown['VWAP터치'] = 20
        elif 0.3 < indicators.vwap_distance <= 0.8:
            score += 15
            breakdown['VWAP상방'] = 15
        else:
            # VWAP에서 너무 멀면 해당 없음
            return {
                'type': SignalType.VWAP_BOUNCE,
                'score': 0,
                'breakdown': {'VWAP': 'VWAP에서 이격'},
                'reason': "VWAP 바운스 조건 미충족",
                'warnings': [],
            }
        
        # 2. 당일 상승 추세
        if indicators.day_change_pct > 0:
            score += 15
            breakdown['당일상승'] = 15
        else:
            score += 0
            breakdown['당일하락'] = 0
            warnings.append("당일 하락 중")
        
        # 3. 양봉 확인
        if indicators.is_bullish:
            score += 15
            breakdown['양봉'] = 15
        else:
            score += 5
            breakdown['음봉'] = 5
            warnings.append("양봉 전환 대기")
        
        # 4. 거래량
        if indicators.volume_ratio >= 1.5:
            score += 10
            breakdown['거래량'] = 10
        elif indicators.volume_ratio >= 1.0:
            score += 5
            breakdown['거래량보통'] = 5
        
        # 5. RSI
        if 45 <= indicators.rsi <= 65:
            score += 10
            breakdown['RSI중립'] = 10
        
        return {
            'type': SignalType.VWAP_BOUNCE,
            'score': max(0, score),
            'breakdown': breakdown,
            'reason': f"VWAP 바운스 (VWAP이격:{indicators.vwap_distance:.2f}%)",
            'warnings': warnings,
        }
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def _get_strength(self, score: float) -> SignalStrength:
        """점수로 강도 계산"""
        if score >= 85:
            return SignalStrength.VERY_STRONG
        elif score >= 70:
            return SignalStrength.STRONG
        elif score >= 55:
            return SignalStrength.MEDIUM
        else:
            return SignalStrength.WEAK


# =============================================================================
# 테스트
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    
    print("=" * 60)
    print("ScalpSignalGenerator 테스트")
    print("=" * 60)
    
    # 시그널 생성기 초기화
    config = {
        'trading': {
            'stop_loss': -0.7,
            'take_profit_1': 1.5,
            'take_profit_2': 2.5,
            'min_score': 55,
        }
    }
    gen = ScalpSignalGenerator(config)
    
    # 테스트 케이스
    test_cases = [
        {
            'name': '돌파 매수 시나리오',
            'indicators': MinuteIndicatorResult(
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
            ),
            'context': MarketContext(
                prev_close=10200,
                prev_high=10400,
                current_time=time(9, 15),
            ),
        },
        {
            'name': '풀백 매수 시나리오',
            'indicators': MinuteIndicatorResult(
                timestamp='10:30',
                price=10400,
                cci=80,
                rsi=55,
                vwap=10250,
                vwap_distance=1.46,
                volume_ratio=0.6,
                day_high=10600,
                day_change_pct=2.5,
                from_day_high_pct=-1.89,
                is_bullish=False,
                body_ratio=0.3,
            ),
            'context': MarketContext(
                prev_close=10150,
                prev_high=10300,
            ),
        },
        {
            'name': '갭 플레이 시나리오',
            'indicators': MinuteIndicatorResult(
                timestamp='09:10',
                price=10350,
                cci=120,
                rsi=60,
                vwap=10400,
                vwap_distance=-0.48,
                volume_ratio=2.0,
                day_high=10500,
                day_change_pct=1.7,
                from_day_high_pct=-1.43,
                is_bullish=True,
                body_ratio=0.5,
            ),
            'context': MarketContext(
                prev_close=10180,
                prev_high=10300,
                current_time=time(9, 10),
            ),
        },
        {
            'name': 'VWAP 바운스 시나리오',
            'indicators': MinuteIndicatorResult(
                timestamp='11:00',
                price=10280,
                cci=60,
                rsi=52,
                vwap=10250,
                vwap_distance=0.29,
                volume_ratio=1.2,
                day_high=10400,
                day_change_pct=1.2,
                from_day_high_pct=-1.15,
                is_bullish=True,
                body_ratio=0.4,
            ),
            'context': MarketContext(
                prev_close=10160,
            ),
        },
        {
            'name': '스킵 시나리오 (RSI 과열)',
            'indicators': MinuteIndicatorResult(
                timestamp='14:00',
                price=11000,
                cci=250,
                rsi=88,
                vwap=10500,
                vwap_distance=4.76,
                volume_ratio=3.0,
                day_high=11000,
                day_change_pct=8.0,
                from_day_high_pct=0,
                is_bullish=True,
                body_ratio=0.8,
            ),
            'context': MarketContext(
                prev_close=10200,
            ),
        },
    ]
    
    for tc in test_cases:
        print(f"\n{'='*60}")
        print(f"📊 {tc['name']}")
        print(f"{'='*60}")
        
        signal = gen.evaluate(
            stock_code="005930",
            indicators=tc['indicators'],
            context=tc['context'],
        )
        
        print(f"   신호 타입: {signal.signal_type.value}")
        print(f"   판정: {signal.action}")
        print(f"   점수: {signal.score:.0f}점 ({signal.strength.value})")
        print(f"   이유: {signal.reason}")
        
        if signal.score_breakdown:
            print(f"   점수 내역:")
            for k, v in signal.score_breakdown.items():
                print(f"      - {k}: {v:+.0f}")
        
        if signal.warnings:
            print(f"   ⚠️ 경고:")
            for w in signal.warnings:
                print(f"      - {w}")
        
        if signal.action == "BUY":
            print(f"   진입가: {signal.entry_price:,.0f}원")
            print(f"   손절가: {signal.stop_loss:,.0f}원 ({(signal.stop_loss/signal.entry_price-1)*100:+.2f}%)")
            print(f"   익절1: {signal.take_profit_1:,.0f}원 ({(signal.take_profit_1/signal.entry_price-1)*100:+.2f}%)")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)