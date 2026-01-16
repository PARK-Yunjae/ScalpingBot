#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Circuit Breaker (서킷 브레이커)
============================================================================
연속 실패 시 일시적으로 작업을 차단하는 안전 장치

상태:
- CLOSED: 정상 (작업 허용)
- OPEN: 차단 (작업 거부)
- HALF_OPEN: 테스트 (제한적 허용)

사용법:
    breaker = CircuitBreaker(failure_threshold=5, reset_timeout=60)
    
    if breaker.can_execute():
        try:
            result = do_something()
            breaker.record_success()
        except:
            breaker.record_failure()
============================================================================
"""

import time
import logging
import threading
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

# 로거 설정
logger = logging.getLogger('ScalpingBot.CircuitBreaker')


# =============================================================================
# 상태 열거형
# =============================================================================

class CircuitState(Enum):
    """서킷 상태"""
    CLOSED = "CLOSED"       # 정상 (허용)
    OPEN = "OPEN"           # 차단 (거부)
    HALF_OPEN = "HALF_OPEN" # 테스트 (제한)


# =============================================================================
# 서킷 브레이커 클래스
# =============================================================================

class CircuitBreaker:
    """
    서킷 브레이커
    
    연속 실패 시 자동으로 차단하고,
    일정 시간 후 복구를 시도합니다.
    """
    
    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        success_threshold: int = 2,
        reset_timeout: float = 60,
        half_open_max: int = 3,
        on_open: Callable[[], None] = None,
        on_close: Callable[[], None] = None,
    ):
        """
        초기화
        
        Args:
            name: 브레이커 이름
            failure_threshold: 실패 임계값 (이만큼 실패하면 OPEN)
            success_threshold: 성공 임계값 (HALF_OPEN에서 이만큼 성공하면 CLOSED)
            reset_timeout: OPEN → HALF_OPEN 전환 시간 (초)
            half_open_max: HALF_OPEN에서 허용되는 최대 시도 수
            on_open: OPEN 전환 시 콜백
            on_close: CLOSED 전환 시 콜백
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max = half_open_max
        
        # 콜백
        self.on_open = on_open
        self.on_close = on_close
        
        # 상태
        self._state = CircuitState.CLOSED
        self._lock = threading.Lock()
        
        # 카운터
        self._failure_count = 0
        self._success_count = 0
        self._half_open_count = 0
        
        # 타이밍
        self._last_failure_time: Optional[float] = None
        self._opened_at: Optional[float] = None
        
        # 통계
        self._stats = {
            'total_calls': 0,
            'total_failures': 0,
            'total_success': 0,
            'times_opened': 0,
        }
        
        logger.info(
            f"CircuitBreaker[{name}] 초기화 "
            f"(실패 임계: {failure_threshold}, 리셋: {reset_timeout}초)"
        )
    
    # =========================================================================
    # 상태 조회
    # =========================================================================
    
    @property
    def state(self) -> CircuitState:
        """현재 상태"""
        with self._lock:
            self._check_state_transition()
            return self._state
    
    @property
    def is_closed(self) -> bool:
        """정상 상태인가"""
        return self.state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        """차단 상태인가"""
        return self.state == CircuitState.OPEN
    
    # =========================================================================
    # 실행 제어
    # =========================================================================
    
    def can_execute(self) -> bool:
        """
        실행 가능 여부
        
        Returns:
            True: 실행 가능, False: 차단됨
        """
        with self._lock:
            self._check_state_transition()
            
            if self._state == CircuitState.CLOSED:
                return True
            
            elif self._state == CircuitState.OPEN:
                return False
            
            else:  # HALF_OPEN
                # 제한된 수만 허용
                if self._half_open_count < self.half_open_max:
                    self._half_open_count += 1
                    return True
                return False
    
    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """
        함수 실행 (자동 기록)
        
        Args:
            func: 실행할 함수
            *args, **kwargs: 함수 인자
        
        Returns:
            함수 결과
        
        Raises:
            CircuitOpenError: 서킷이 열려있을 때
        """
        if not self.can_execute():
            raise CircuitOpenError(f"서킷 브레이커[{self.name}] 열림")
        
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure()
            raise
    
    # =========================================================================
    # 결과 기록
    # =========================================================================
    
    def record_success(self):
        """성공 기록"""
        with self._lock:
            self._stats['total_calls'] += 1
            self._stats['total_success'] += 1
            
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                
                if self._success_count >= self.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
            
            elif self._state == CircuitState.CLOSED:
                # 연속 실패 카운터 리셋
                self._failure_count = 0
    
    def record_failure(self):
        """실패 기록"""
        with self._lock:
            self._stats['total_calls'] += 1
            self._stats['total_failures'] += 1
            self._last_failure_time = time.time()
            
            if self._state == CircuitState.CLOSED:
                self._failure_count += 1
                
                if self._failure_count >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
            
            elif self._state == CircuitState.HALF_OPEN:
                # 테스트 실패 → 다시 OPEN
                self._transition_to(CircuitState.OPEN)
    
    # =========================================================================
    # 상태 전이
    # =========================================================================
    
    def _check_state_transition(self):
        """상태 전이 체크 (락 내부 호출)"""
        if self._state == CircuitState.OPEN:
            # 타임아웃 체크
            if self._opened_at and time.time() - self._opened_at >= self.reset_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
    
    def _transition_to(self, new_state: CircuitState):
        """상태 전이 (락 내부 호출)"""
        old_state = self._state
        self._state = new_state
        
        logger.info(f"CircuitBreaker[{self.name}]: {old_state.value} → {new_state.value}")
        
        if new_state == CircuitState.OPEN:
            self._opened_at = time.time()
            self._stats['times_opened'] += 1
            
            if self.on_open:
                try:
                    self.on_open()
                except Exception as e:
                    logger.error(f"on_open 콜백 에러: {e}")
        
        elif new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
            
            if self.on_close:
                try:
                    self.on_close()
                except Exception as e:
                    logger.error(f"on_close 콜백 에러: {e}")
        
        elif new_state == CircuitState.HALF_OPEN:
            self._success_count = 0
            self._half_open_count = 0
    
    # =========================================================================
    # 수동 제어
    # =========================================================================
    
    def reset(self):
        """강제 리셋 (CLOSED로)"""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0
            self._success_count = 0
            logger.warning(f"CircuitBreaker[{self.name}] 강제 리셋")
    
    def trip(self, reason: str = "수동"):
        """강제 트립 (OPEN으로)"""
        with self._lock:
            self._transition_to(CircuitState.OPEN)
            logger.warning(f"CircuitBreaker[{self.name}] 강제 트립: {reason}")
    
    # =========================================================================
    # 통계 및 정보
    # =========================================================================
    
    def get_stats(self) -> Dict:
        """통계 조회"""
        with self._lock:
            return {
                **self._stats,
                'state': self._state.value,
                'failure_count': self._failure_count,
                'success_count': self._success_count,
            }
    
    def get_status(self) -> Dict:
        """상태 정보"""
        with self._lock:
            time_in_state = 0
            if self._opened_at and self._state == CircuitState.OPEN:
                time_in_state = time.time() - self._opened_at
            
            return {
                'name': self.name,
                'state': self._state.value,
                'failure_count': self._failure_count,
                'failure_threshold': self.failure_threshold,
                'time_in_state': time_in_state,
                'reset_timeout': self.reset_timeout,
            }


# =============================================================================
# 예외
# =============================================================================

class CircuitOpenError(Exception):
    """서킷 브레이커 열림 예외"""
    pass


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("CircuitBreaker 테스트")
    print("=" * 60)
    
    # 콜백
    def on_open():
        print("   [콜백] 서킷 열림!")
    
    def on_close():
        print("   [콜백] 서킷 닫힘!")
    
    breaker = CircuitBreaker(
        name="test",
        failure_threshold=3,
        success_threshold=2,
        reset_timeout=5,
        on_open=on_open,
        on_close=on_close,
    )
    
    print("\n1. 초기 상태:")
    print(f"   상태: {breaker.state.value}")
    print(f"   실행 가능: {breaker.can_execute()}")
    
    print("\n2. 연속 실패 테스트:")
    for i in range(4):
        breaker.record_failure()
        print(f"   실패 {i+1}: 상태={breaker.state.value}")
    
    print("\n3. 차단 상태:")
    print(f"   실행 가능: {breaker.can_execute()}")
    
    print("\n4. 타임아웃 대기 (5초)...")
    time.sleep(6)
    
    print("\n5. HALF_OPEN 상태:")
    print(f"   상태: {breaker.state.value}")
    print(f"   실행 가능: {breaker.can_execute()}")
    
    print("\n6. 성공 기록 (복구):")
    breaker.record_success()
    breaker.record_success()
    print(f"   상태: {breaker.state.value}")
    
    print("\n7. 통계:")
    stats = breaker.get_stats()
    for key, value in stats.items():
        print(f"   {key}: {value}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
