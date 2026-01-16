#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Strategy 모듈
============================================================================
기술적 지표 계산 및 점수 엔진

모듈:
- indicators: CCI, 이동평균, 이격도, 캔들 분석 등
- score_engine: 6대 지표 점수화 및 종합 점수 계산
============================================================================
"""

from scalping.strategy.indicators import (
    calculate_cci,
    calculate_sma,
    calculate_ema,
    calculate_distance_from_ma,
    calculate_volume_ratio,
    count_consecutive_bullish,
    count_consecutive_bearish,
    analyze_candle,
    analyze_candles_df,
    calculate_all_indicators,
    RealtimeIndicators,
)

from scalping.strategy.score_engine import (
    ScoreEngine,
    ScoreResult,
    calc_cci_score,
    calc_change_score,
    calc_distance_score,
    calc_consec_score,
    calc_volume_score,
    calc_candle_score,
    verify_with_sample,
)

__all__ = [
    # indicators
    'calculate_cci',
    'calculate_sma',
    'calculate_ema',
    'calculate_distance_from_ma',
    'calculate_volume_ratio',
    'count_consecutive_bullish',
    'count_consecutive_bearish',
    'analyze_candle',
    'analyze_candles_df',
    'calculate_all_indicators',
    'RealtimeIndicators',
    
    # score_engine
    'ScoreEngine',
    'ScoreResult',
    'calc_cci_score',
    'calc_change_score',
    'calc_distance_score',
    'calc_consec_score',
    'calc_volume_score',
    'calc_candle_score',
    'verify_with_sample',
]
