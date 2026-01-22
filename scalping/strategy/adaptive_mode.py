#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v3.2 - Adaptive Mode (자동 강약 조절)
============================================================================
시장 상황 + 봇 성과에 따라 min_score, 쿨타임 등 자동 조정

모드:
- DEFENSIVE: 방어 모드 - 확실한 것만 (min_score 75)
- BALANCED: 균형 모드 - 기본값 (min_score 70)
- AGGRESSIVE: 공격 모드 - 기회 확대 (min_score 65)

트리거:
- 연속 손절 → DEFENSIVE
- 지수 하락 → DEFENSIVE
- 당일 수익 + 연승 → AGGRESSIVE
============================================================================
"""

import logging
from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger('ScalpingBot.AdaptiveMode')


class TradingMode(Enum):
    """매매 모드"""
    DEFENSIVE = "DEFENSIVE"
    BALANCED = "BALANCED"
    AGGRESSIVE = "AGGRESSIVE"


@dataclass
class ModeSettings:
    """모드별 설정"""
    min_score: int
    cooldown_minutes: int
    description: str


# 기본 모드 설정
DEFAULT_MODE_SETTINGS = {
    TradingMode.DEFENSIVE: ModeSettings(
        min_score=75,
        cooldown_minutes=15,
        description="방어 모드 - 확실한 것만"
    ),
    TradingMode.BALANCED: ModeSettings(
        min_score=70,
        cooldown_minutes=10,
        description="균형 모드 - 기본값"
    ),
    TradingMode.AGGRESSIVE: ModeSettings(
        min_score=65,
        cooldown_minutes=5,
        description="공격 모드 - 기회 확대"
    ),
}


class AdaptiveMode:
    """
    Adaptive Mode 관리자
    
    시장 상황과 봇 성과를 종합해서 자동으로 모드 전환
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Args:
            config: adaptive_mode 설정
        """
        self.config = config or {}
        self.enabled = self.config.get('enabled', True)
        
        # 기본 모드
        default_mode_str = self.config.get('default_mode', 'BALANCED')
        self.default_mode = TradingMode[default_mode_str]
        self.current_mode = self.default_mode
        
        # 모드별 설정 로드
        self.mode_settings = {}
        modes_config = self.config.get('modes', {})
        
        for mode in TradingMode:
            mode_config = modes_config.get(mode.value, {})
            default = DEFAULT_MODE_SETTINGS[mode]
            
            self.mode_settings[mode] = ModeSettings(
                min_score=mode_config.get('min_score', default.min_score),
                cooldown_minutes=mode_config.get('cooldown_minutes', default.cooldown_minutes),
                description=mode_config.get('description', default.description),
            )
        
        # 트리거 설정 로드
        triggers_config = self.config.get('triggers', {})
        
        # DEFENSIVE 트리거
        to_defensive = triggers_config.get('to_defensive', {})
        self.defensive_consecutive_losses = to_defensive.get('consecutive_losses', 3)
        self.defensive_index_drop_pct = to_defensive.get('index_drop_pct', -1.5)
        self.defensive_daily_loss_pct = to_defensive.get('daily_loss_pct', -1.5)
        
        # AGGRESSIVE 트리거
        to_aggressive = triggers_config.get('to_aggressive', {})
        self.aggressive_daily_profit_pct = to_aggressive.get('daily_profit_pct', 1.0)
        self.aggressive_consecutive_wins = to_aggressive.get('consecutive_wins', 3)
        self.aggressive_index_rise_pct = to_aggressive.get('index_rise_pct', 1.0)
        
        # BALANCED 복귀 트리거
        to_balanced = triggers_config.get('to_balanced', {})
        self.balanced_from_defensive_wins = to_balanced.get('from_defensive_wins', 2)
        self.balanced_from_aggressive_losses = to_balanced.get('from_aggressive_losses', 2)
        
        # 상태 추적
        self._wins_in_current_mode = 0
        self._losses_in_current_mode = 0
        self._last_mode_change = datetime.now()
        
        logger.info(f"AdaptiveMode 초기화 (enabled={self.enabled}, default={self.default_mode.value})")
    
    def update(
        self,
        consecutive_losses: int = 0,
        consecutive_wins: int = 0,
        daily_profit_pct: float = 0.0,
        index_change_pct: float = 0.0,
    ) -> TradingMode:
        """
        상황에 따라 모드 업데이트
        
        Args:
            consecutive_losses: 연속 손절 횟수
            consecutive_wins: 연속 익절 횟수
            daily_profit_pct: 당일 수익률 (%)
            index_change_pct: 지수 등락률 (%)
        
        Returns:
            현재 모드
        """
        if not self.enabled:
            return self.current_mode
        
        old_mode = self.current_mode
        new_mode = self._evaluate_mode(
            consecutive_losses, consecutive_wins,
            daily_profit_pct, index_change_pct
        )
        
        if new_mode != old_mode:
            self._switch_mode(new_mode, reason=self._get_switch_reason(
                old_mode, new_mode, consecutive_losses, consecutive_wins,
                daily_profit_pct, index_change_pct
            ))
        
        return self.current_mode
    
    def _evaluate_mode(
        self,
        consecutive_losses: int,
        consecutive_wins: int,
        daily_profit_pct: float,
        index_change_pct: float,
    ) -> TradingMode:
        """모드 결정 로직"""
        
        # === DEFENSIVE 우선 체크 (안전 최우선) ===
        
        # 연속 손절 N회 이상
        if consecutive_losses >= self.defensive_consecutive_losses:
            return TradingMode.DEFENSIVE
        
        # 지수 급락
        if index_change_pct <= self.defensive_index_drop_pct:
            return TradingMode.DEFENSIVE
        
        # 당일 손실 심함
        if daily_profit_pct <= self.defensive_daily_loss_pct:
            return TradingMode.DEFENSIVE
        
        # === BALANCED 복귀 체크 ===
        
        # DEFENSIVE에서 연승 시 복귀
        if self.current_mode == TradingMode.DEFENSIVE:
            if self._wins_in_current_mode >= self.balanced_from_defensive_wins:
                return TradingMode.BALANCED
        
        # AGGRESSIVE에서 연패 시 복귀
        if self.current_mode == TradingMode.AGGRESSIVE:
            if self._losses_in_current_mode >= self.balanced_from_aggressive_losses:
                return TradingMode.BALANCED
        
        # === AGGRESSIVE 체크 (충분히 안전할 때만) ===
        
        # 당일 수익 좋고 연승 중
        if (daily_profit_pct >= self.aggressive_daily_profit_pct and
            consecutive_wins >= self.aggressive_consecutive_wins):
            return TradingMode.AGGRESSIVE
        
        # 지수 강세
        if index_change_pct >= self.aggressive_index_rise_pct:
            # 단, 손실 중이면 AGGRESSIVE 안 함
            if daily_profit_pct >= 0:
                return TradingMode.AGGRESSIVE
        
        # 현재 모드 유지
        return self.current_mode
    
    def _switch_mode(self, new_mode: TradingMode, reason: str = ""):
        """모드 전환"""
        old_mode = self.current_mode
        self.current_mode = new_mode
        self._wins_in_current_mode = 0
        self._losses_in_current_mode = 0
        self._last_mode_change = datetime.now()
        
        logger.info(f"🔄 모드 전환: {old_mode.value} → {new_mode.value}")
        logger.info(f"   사유: {reason}")
        logger.info(f"   설정: min_score={self.get_min_score()}, "
                   f"cooldown={self.get_cooldown_minutes()}분")
    
    def _get_switch_reason(
        self,
        old_mode: TradingMode,
        new_mode: TradingMode,
        consecutive_losses: int,
        consecutive_wins: int,
        daily_profit_pct: float,
        index_change_pct: float,
    ) -> str:
        """전환 사유 생성"""
        if new_mode == TradingMode.DEFENSIVE:
            if consecutive_losses >= self.defensive_consecutive_losses:
                return f"연속 손절 {consecutive_losses}회"
            if index_change_pct <= self.defensive_index_drop_pct:
                return f"지수 하락 {index_change_pct:.1f}%"
            if daily_profit_pct <= self.defensive_daily_loss_pct:
                return f"당일 손실 {daily_profit_pct:.1f}%"
        
        elif new_mode == TradingMode.AGGRESSIVE:
            if consecutive_wins >= self.aggressive_consecutive_wins:
                return f"연속 익절 {consecutive_wins}회 + 당일 {daily_profit_pct:+.1f}%"
            if index_change_pct >= self.aggressive_index_rise_pct:
                return f"지수 상승 {index_change_pct:+.1f}%"
        
        elif new_mode == TradingMode.BALANCED:
            if old_mode == TradingMode.DEFENSIVE:
                return f"DEFENSIVE에서 {self._wins_in_current_mode}연승으로 복귀"
            if old_mode == TradingMode.AGGRESSIVE:
                return f"AGGRESSIVE에서 {self._losses_in_current_mode}연패로 복귀"
        
        return "조건 변경"
    
    def record_trade_result(self, is_win: bool):
        """
        거래 결과 기록 (모드 내 연승/연패 추적)
        
        Args:
            is_win: 익절 여부
        """
        if is_win:
            self._wins_in_current_mode += 1
            self._losses_in_current_mode = 0
        else:
            self._losses_in_current_mode += 1
            self._wins_in_current_mode = 0
    
    def get_min_score(self) -> int:
        """현재 모드의 min_score 반환"""
        return self.mode_settings[self.current_mode].min_score
    
    def get_cooldown_minutes(self) -> int:
        """현재 모드의 쿨타임 반환"""
        return self.mode_settings[self.current_mode].cooldown_minutes
    
    def get_current_mode(self) -> TradingMode:
        """현재 모드 반환"""
        return self.current_mode
    
    def get_mode_info(self) -> Dict[str, Any]:
        """현재 모드 정보 반환"""
        settings = self.mode_settings[self.current_mode]
        return {
            'mode': self.current_mode.value,
            'min_score': settings.min_score,
            'cooldown_minutes': settings.cooldown_minutes,
            'description': settings.description,
            'wins_in_mode': self._wins_in_current_mode,
            'losses_in_mode': self._losses_in_current_mode,
            'last_change': self._last_mode_change.isoformat(),
        }
    
    def force_mode(self, mode: TradingMode, reason: str = "수동 전환"):
        """강제 모드 전환 (디버깅/테스트용)"""
        self._switch_mode(mode, reason)
