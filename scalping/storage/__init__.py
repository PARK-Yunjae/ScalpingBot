#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Storage 모듈
============================================================================
데이터 저장 및 접근

모듈:
- database: SQLite 데이터베이스 연결
- models: 데이터 모델 정의
- repository: 데이터 접근 레포지토리
============================================================================
"""

from scalping.storage.database import Database, get_database
from scalping.storage.models import (
    Trade,
    DailySummary,
    Position,
    AILearning,
    Setting,
    TradeType,
    SellReason,
    PositionGrade,
    MarketMode,
    AIDecision,
)
from scalping.storage.repository import (
    TradeRepository,
    PositionRepository,
    SummaryRepository,
    AILearningRepository,
    SettingRepository,
)

__all__ = [
    # database
    'Database',
    'get_database',
    
    # models
    'Trade',
    'DailySummary',
    'Position',
    'AILearning',
    'Setting',
    'TradeType',
    'SellReason',
    'PositionGrade',
    'MarketMode',
    'AIDecision',
    
    # repository
    'TradeRepository',
    'PositionRepository',
    'SummaryRepository',
    'AILearningRepository',
    'SettingRepository',
]
