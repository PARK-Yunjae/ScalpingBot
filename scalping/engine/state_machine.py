#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - State Machine (상태 머신)
============================================================================
트레이딩 시스템의 상태를 관리하는 상태 머신

상태:
- IDLE: 대기
- INITIALIZING: 초기화
- PRE_MARKET: 장 시작 전 준비
- TRADING: 매매 중
- CLOSING: 청산 중
- POST_MARKET: 장 종료 후
- STOPPED: 정지
- EMERGENCY: 비상

사용법:
    sm = StateMachine()
    sm.transition_to(State.TRADING)
    
    if sm.is_trading():
        # 매매 로직
============================================================================
"""

import logging
import threading
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
from enum import Enum, auto
from dataclasses import dataclass

# 로거 설정
logger = logging.getLogger('ScalpingBot.StateMachine')


# =============================================================================
# 상태 열거형
# =============================================================================

class State(Enum):
    """시스템 상태"""
    IDLE = auto()           # 대기
    INITIALIZING = auto()   # 초기화
    PRE_MARKET = auto()     # 장 시작 전 준비
    TRADING = auto()        # 매매 중
    CLOSING = auto()        # 청산 중
    POST_MARKET = auto()    # 장 종료 후
    STOPPED = auto()        # 정지
    EMERGENCY = auto()      # 비상


# 상태 전이 규칙 (현재 상태 -> 가능한 다음 상태들)
VALID_TRANSITIONS = {
    State.IDLE: [State.INITIALIZING, State.STOPPED],
    State.INITIALIZING: [State.PRE_MARKET, State.STOPPED, State.EMERGENCY],
    State.PRE_MARKET: [State.TRADING, State.STOPPED, State.EMERGENCY],
    State.TRADING: [State.CLOSING, State.STOPPED, State.EMERGENCY],
    State.CLOSING: [State.POST_MARKET, State.STOPPED, State.EMERGENCY],
    State.POST_MARKET: [State.IDLE, State.STOPPED],
    State.STOPPED: [State.IDLE],
    State.EMERGENCY: [State.STOPPED, State.IDLE],
}


# =============================================================================
# 상태 변경 이벤트
# =============================================================================

@dataclass
class StateChange:
    """상태 변경 이벤트"""
    from_state: State
    to_state: State
    timestamp: datetime
    reason: str = ""
    
    def __str__(self) -> str:
        return f"{self.from_state.name} → {self.to_state.name} ({self.reason})"


# =============================================================================
# 상태 머신 클래스
# =============================================================================

class StateMachine:
    """
    트레이딩 상태 머신
    
    시스템의 상태를 관리하고 상태 전이를 제어합니다.
    """
    
    def __init__(
        self,
        initial_state: State = State.IDLE,
        on_state_change: Callable[[StateChange], None] = None,
    ):
        """
        초기화
        
        Args:
            initial_state: 초기 상태
            on_state_change: 상태 변경 콜백
        """
        self._state = initial_state
        self._lock = threading.Lock()
        self._on_state_change = on_state_change
        
        # 상태별 콜백
        self._enter_callbacks: Dict[State, List[Callable]] = {s: [] for s in State}
        self._exit_callbacks: Dict[State, List[Callable]] = {s: [] for s in State}
        
        # 상태 변경 이력
        self._history: List[StateChange] = []
        self._max_history = 100
        
        # 상태 진입 시간
        self._state_entered_at = datetime.now()
        
        logger.info(f"StateMachine 초기화 (초기 상태: {initial_state.name})")
    
    # =========================================================================
    # 상태 조회
    # =========================================================================
    
    @property
    def state(self) -> State:
        """현재 상태"""
        return self._state
    
    @property
    def state_name(self) -> str:
        """현재 상태명"""
        return self._state.name
    
    def get_state_duration(self) -> float:
        """현재 상태 유지 시간 (초)"""
        return (datetime.now() - self._state_entered_at).total_seconds()
    
    def get_history(self, limit: int = 10) -> List[StateChange]:
        """상태 변경 이력"""
        return self._history[-limit:]
    
    # =========================================================================
    # 상태 전이
    # =========================================================================
    
    def transition_to(self, new_state: State, reason: str = "") -> bool:
        """
        상태 전이
        
        Args:
            new_state: 새 상태
            reason: 전이 사유
        
        Returns:
            전이 성공 여부
        """
        with self._lock:
            # 같은 상태면 무시
            if new_state == self._state:
                return True
            
            # 유효한 전이인지 확인
            valid_next = VALID_TRANSITIONS.get(self._state, [])
            if new_state not in valid_next:
                logger.warning(
                    f"잘못된 상태 전이: {self._state.name} → {new_state.name}"
                )
                return False
            
            old_state = self._state
            
            # 이전 상태 exit 콜백
            self._call_callbacks(self._exit_callbacks[old_state], old_state)
            
            # 상태 변경
            self._state = new_state
            self._state_entered_at = datetime.now()
            
            # 이력 저장
            change = StateChange(
                from_state=old_state,
                to_state=new_state,
                timestamp=datetime.now(),
                reason=reason,
            )
            self._history.append(change)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            
            logger.info(f"상태 전이: {change}")
            
            # 새 상태 enter 콜백
            self._call_callbacks(self._enter_callbacks[new_state], new_state)
            
            # 전역 콜백
            if self._on_state_change:
                try:
                    self._on_state_change(change)
                except Exception as e:
                    logger.error(f"상태 변경 콜백 에러: {e}")
            
            return True
    
    def force_state(self, new_state: State, reason: str = "강제 전이"):
        """
        강제 상태 전이 (규칙 무시)
        
        Args:
            new_state: 새 상태
            reason: 사유
        """
        with self._lock:
            old_state = self._state
            self._state = new_state
            self._state_entered_at = datetime.now()
            
            change = StateChange(
                from_state=old_state,
                to_state=new_state,
                timestamp=datetime.now(),
                reason=f"[FORCE] {reason}",
            )
            self._history.append(change)
            
            logger.warning(f"강제 상태 전이: {change}")
    
    # =========================================================================
    # 상태 체크 헬퍼
    # =========================================================================
    
    def is_idle(self) -> bool:
        """대기 상태인가"""
        return self._state == State.IDLE
    
    def is_trading(self) -> bool:
        """매매 중인가"""
        return self._state == State.TRADING
    
    def is_closing(self) -> bool:
        """청산 중인가"""
        return self._state == State.CLOSING
    
    def is_stopped(self) -> bool:
        """정지 상태인가"""
        return self._state == State.STOPPED
    
    def is_emergency(self) -> bool:
        """비상 상태인가"""
        return self._state == State.EMERGENCY
    
    def can_trade(self) -> bool:
        """매매 가능한 상태인가"""
        return self._state == State.TRADING
    
    def can_open_position(self) -> bool:
        """포지션 오픈 가능한 상태인가"""
        return self._state == State.TRADING
    
    # =========================================================================
    # 콜백 등록
    # =========================================================================
    
    def on_enter(self, state: State, callback: Callable):
        """상태 진입 시 콜백 등록"""
        self._enter_callbacks[state].append(callback)
    
    def on_exit(self, state: State, callback: Callable):
        """상태 이탈 시 콜백 등록"""
        self._exit_callbacks[state].append(callback)
    
    def _call_callbacks(self, callbacks: List[Callable], state: State):
        """콜백 호출"""
        for callback in callbacks:
            try:
                callback(state)
            except Exception as e:
                logger.error(f"콜백 에러: {e}")
    
    # =========================================================================
    # 편의 메서드
    # =========================================================================
    
    def start(self):
        """시작 (IDLE → INITIALIZING)"""
        self.transition_to(State.INITIALIZING, "시작")
    
    def ready(self):
        """준비 완료 (INITIALIZING → PRE_MARKET)"""
        self.transition_to(State.PRE_MARKET, "초기화 완료")
    
    def begin_trading(self):
        """매매 시작 (PRE_MARKET → TRADING)"""
        self.transition_to(State.TRADING, "장 시작")
    
    def begin_closing(self):
        """청산 시작 (TRADING → CLOSING)"""
        self.transition_to(State.CLOSING, "청산 시작")
    
    def end_day(self):
        """장 마감 (CLOSING → POST_MARKET)"""
        self.transition_to(State.POST_MARKET, "장 마감")
    
    def stop(self, reason: str = "정지"):
        """정지"""
        self.transition_to(State.STOPPED, reason)
    
    def emergency(self, reason: str = "비상 상황"):
        """비상 정지"""
        self.force_state(State.EMERGENCY, reason)
    
    def reset(self):
        """리셋 (→ IDLE)"""
        self.force_state(State.IDLE, "리셋")


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("StateMachine 테스트")
    print("=" * 60)
    
    # 콜백
    def on_change(change: StateChange):
        print(f"   [콜백] {change}")
    
    sm = StateMachine(on_state_change=on_change)
    
    print("\n1. 정상 플로우:")
    print(f"   초기: {sm.state_name}")
    
    sm.start()
    sm.ready()
    sm.begin_trading()
    
    print(f"\n   매매 가능: {sm.can_trade()}")
    print(f"   상태 유지: {sm.get_state_duration():.2f}초")
    
    sm.begin_closing()
    sm.end_day()
    
    print("\n2. 비상 상황:")
    sm.reset()
    sm.start()
    sm.ready()
    sm.begin_trading()
    sm.emergency("연속 손절")
    
    print(f"   비상 상태: {sm.is_emergency()}")
    print(f"   매매 가능: {sm.can_trade()}")
    
    print("\n3. 상태 이력:")
    for change in sm.get_history():
        print(f"   {change}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
