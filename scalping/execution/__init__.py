#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Execution 모듈
============================================================================
주문 실행 및 포지션 관리

모듈:
- broker: 한투 API 브로커 (주문 실행, 조회)
- position_manager: 포지션 관리 (손절/익절/트레일링)
- cooldown_tracker: 재매수 쿨타임 관리
- price_validator: AI 분석 후 가격 유효성 검증
============================================================================
"""

from scalping.execution.broker import (
    KISBroker,
    OrderResult,
    Position,
    PendingOrder,
    OrderType,
    OrderSide,
    get_tick_size,
    round_price,
)

from scalping.execution.position_manager import (
    PositionManager,
    PositionInfo,
    SellSignal,
    SellReason,
    PositionGrade,
    PROFIT_TARGETS,
)

from scalping.execution.cooldown_tracker import (
    CooldownTracker,
    CooldownInfo,
)

from scalping.execution.price_validator import (
    PriceValidator,
    PriceValidation,
    ValidationResult,
)

__all__ = [
    # broker
    'KISBroker',
    'OrderResult',
    'Position',
    'PendingOrder',
    'OrderType',
    'OrderSide',
    'get_tick_size',
    'round_price',
    
    # position_manager
    'PositionManager',
    'PositionInfo',
    'SellSignal',
    'SellReason',
    'PositionGrade',
    'PROFIT_TARGETS',
    
    # cooldown_tracker
    'CooldownTracker',
    'CooldownInfo',
    
    # price_validator
    'PriceValidator',
    'PriceValidation',
    'ValidationResult',
]
