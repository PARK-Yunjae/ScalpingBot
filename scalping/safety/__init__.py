#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Safety 모듈
============================================================================
안전 장치 및 비상 정지

모듈:
- kill_switch: 비상 정지 및 안전 한도 관리
- circuit_breaker: 연속 실패 시 차단
============================================================================
"""

from scalping.safety.kill_switch import (
    KillSwitch,
    SafetyStatus,
    StopReason,
    SystemState,
)

from scalping.safety.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitOpenError,
)

__all__ = [
    # kill_switch
    'KillSwitch',
    'SafetyStatus',
    'StopReason',
    'SystemState',
    
    # circuit_breaker
    'CircuitBreaker',
    'CircuitState',
    'CircuitOpenError',
]
