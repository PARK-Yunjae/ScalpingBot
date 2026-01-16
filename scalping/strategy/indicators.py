#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Technical Indicators (기술적 지표 계산)
============================================================================
CCI, 이동평균, 이격도, 캔들 분석 등 기술적 지표 계산 모듈

지원 지표:
- CCI (Commodity Channel Index)
- 이동평균선 (SMA, EMA)
- 이격도 (Distance from MA)
- 거래량 비율
- 연속 양봉/음봉 일수
- 캔들 패턴 분석 (윗꼬리, 아랫꼬리, 도지 등)

사용법:
    import pandas as pd
    from scalping.strategy.indicators import (
        calculate_cci,
        calculate_distance_from_ma,
        calculate_volume_ratio,
        count_consecutive_bullish,
        analyze_candle,
    )
    
    # 데이터프레임에 지표 추가
    df['cci'] = calculate_cci(df, period=14)
    df['distance_ma20'] = calculate_distance_from_ma(df, period=20)
============================================================================
"""

import numpy as np
import pandas as pd
from typing import Dict, Union, Optional, Tuple
import logging

# 로거 설정
logger = logging.getLogger('ScalpingBot.Indicators')


# =============================================================================
# CCI (Commodity Channel Index)
# =============================================================================

def calculate_cci(
    df: pd.DataFrame,
    period: int = 14,
    constant: float = 0.015,
) -> pd.Series:
    """
    CCI (Commodity Channel Index) 계산
    
    CCI = (TP - SMA(TP)) / (constant * MAD)
    TP (Typical Price) = (High + Low + Close) / 3
    MAD = Mean Absolute Deviation
    
    해석:
    - CCI > 100: 과매수 구간 (상승 추세 강함)
    - CCI < -100: 과매도 구간 (하락 추세 강함)
    - -100 ~ 100: 중립 구간
    
    Args:
        df: OHLCV 데이터프레임 (high, low, close 컬럼 필요)
        period: CCI 계산 기간 (기본값: 14)
        constant: CCI 상수 (기본값: 0.015)
    
    Returns:
        CCI 시리즈
    """
    # Typical Price 계산
    tp = (df['high'] + df['low'] + df['close']) / 3
    
    # TP의 이동평균
    tp_sma = tp.rolling(window=period).mean()
    
    # Mean Absolute Deviation (MAD)
    # MAD = mean(|TP - SMA(TP)|)
    mad = tp.rolling(window=period).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))),
        raw=True
    )
    
    # CCI 계산
    cci = (tp - tp_sma) / (constant * mad)
    
    return cci


def calculate_cci_single(
    high: float,
    low: float,
    close: float,
    tp_history: list,
    period: int = 14,
    constant: float = 0.015,
) -> float:
    """
    단일 시점 CCI 계산 (실시간 계산용)
    
    Args:
        high: 고가
        low: 저가
        close: 종가
        tp_history: 최근 TP 히스토리 (period-1개)
        period: CCI 기간
        constant: CCI 상수
    
    Returns:
        CCI 값
    """
    # 현재 Typical Price
    current_tp = (high + low + close) / 3
    
    # 전체 TP 리스트
    all_tp = list(tp_history) + [current_tp]
    
    if len(all_tp) < period:
        return 0.0
    
    # 최근 period개만 사용
    recent_tp = all_tp[-period:]
    
    # SMA
    tp_mean = np.mean(recent_tp)
    
    # MAD
    mad = np.mean(np.abs(np.array(recent_tp) - tp_mean))
    
    if mad == 0:
        return 0.0
    
    # CCI
    cci = (current_tp - tp_mean) / (constant * mad)
    
    return float(cci)


# =============================================================================
# 이동평균선 (Moving Averages)
# =============================================================================

def calculate_sma(
    series: pd.Series,
    period: int,
) -> pd.Series:
    """
    단순 이동평균 (SMA) 계산
    
    Args:
        series: 가격 시리즈
        period: 이동평균 기간
    
    Returns:
        SMA 시리즈
    """
    return series.rolling(window=period).mean()


def calculate_ema(
    series: pd.Series,
    period: int,
) -> pd.Series:
    """
    지수 이동평균 (EMA) 계산
    
    Args:
        series: 가격 시리즈
        period: 이동평균 기간
    
    Returns:
        EMA 시리즈
    """
    return series.ewm(span=period, adjust=False).mean()


def calculate_ma_trend(
    df: pd.DataFrame,
    period: int = 20,
    lookback: int = 3,
) -> pd.Series:
    """
    이동평균선 상승/하락 추세 판단
    
    Args:
        df: 데이터프레임 (close 컬럼 필요)
        period: 이동평균 기간
        lookback: 추세 판단 기간 (일)
    
    Returns:
        불리언 시리즈 (True: 상승 추세)
    """
    ma = calculate_sma(df['close'], period)
    
    # lookback 기간 동안 MA가 계속 상승했는지 확인
    ma_rising = ma.diff().rolling(window=lookback).min() > 0
    
    return ma_rising


# =============================================================================
# 이격도 (Distance from MA)
# =============================================================================

def calculate_distance_from_ma(
    df: pd.DataFrame,
    period: int = 20,
    ma_type: str = 'sma',
) -> pd.Series:
    """
    이격도 계산 (현재가와 이동평균선의 거리)
    
    이격도(%) = (현재가 - MA) / MA * 100
    
    해석:
    - 양수: 이동평균선 위 (상승 추세)
    - 음수: 이동평균선 아래 (하락 추세)
    - |이격도| > 10%: 과열/과매도 구간
    
    Args:
        df: 데이터프레임 (close 컬럼 필요)
        period: 이동평균 기간
        ma_type: 이동평균 타입 ('sma' 또는 'ema')
    
    Returns:
        이격도 시리즈 (%)
    """
    if ma_type == 'ema':
        ma = calculate_ema(df['close'], period)
    else:
        ma = calculate_sma(df['close'], period)
    
    distance = (df['close'] - ma) / ma * 100
    
    return distance


def calculate_distance_single(
    close: float,
    ma_value: float,
) -> float:
    """
    단일 시점 이격도 계산
    
    Args:
        close: 현재가
        ma_value: 이동평균 값
    
    Returns:
        이격도 (%)
    """
    if ma_value == 0:
        return 0.0
    
    return (close - ma_value) / ma_value * 100


# =============================================================================
# 거래량 비율 (Volume Ratio)
# =============================================================================

def calculate_volume_ratio(
    df: pd.DataFrame,
    period: int = 20,
) -> pd.Series:
    """
    거래량 비율 계산 (현재 거래량 / 평균 거래량)
    
    해석:
    - > 1.5: 거래량 증가 (관심 증가)
    - > 3.0: 거래량 급증 (급등/급락 가능성)
    - < 0.5: 거래량 감소 (관심 저조)
    
    Args:
        df: 데이터프레임 (volume 컬럼 필요)
        period: 평균 거래량 계산 기간
    
    Returns:
        거래량 비율 시리즈
    """
    avg_volume = df['volume'].rolling(window=period).mean()
    
    # 0 나누기 방지
    avg_volume = avg_volume.replace(0, np.nan)
    
    ratio = df['volume'] / avg_volume
    
    return ratio.fillna(1.0)


def calculate_volume_ratio_single(
    current_volume: int,
    avg_volume: float,
) -> float:
    """
    단일 시점 거래량 비율 계산
    
    Args:
        current_volume: 현재 거래량
        avg_volume: 평균 거래량
    
    Returns:
        거래량 비율
    """
    if avg_volume == 0:
        return 1.0
    
    return current_volume / avg_volume


# =============================================================================
# 연속 양봉/음봉 (Consecutive Candles)
# =============================================================================

def count_consecutive_bullish(
    df: pd.DataFrame,
    idx: int = -1,
) -> int:
    """
    연속 양봉 일수 계산
    
    Args:
        df: 데이터프레임 (close, open 컬럼 필요)
        idx: 계산 시작 인덱스 (기본값: 마지막)
    
    Returns:
        연속 양봉 일수
    """
    if len(df) == 0:
        return 0
    
    # 양봉 여부 (종가 > 시가)
    bullish = df['close'] > df['open']
    
    # 인덱스 정규화
    if idx < 0:
        idx = len(df) + idx
    
    if idx < 0 or idx >= len(df):
        return 0
    
    # 역순으로 연속 양봉 카운트
    count = 0
    for i in range(idx, -1, -1):
        if bullish.iloc[i]:
            count += 1
        else:
            break
    
    return count


def count_consecutive_bearish(
    df: pd.DataFrame,
    idx: int = -1,
) -> int:
    """
    연속 음봉 일수 계산
    
    Args:
        df: 데이터프레임 (close, open 컬럼 필요)
        idx: 계산 시작 인덱스 (기본값: 마지막)
    
    Returns:
        연속 음봉 일수
    """
    if len(df) == 0:
        return 0
    
    # 음봉 여부 (종가 < 시가)
    bearish = df['close'] < df['open']
    
    # 인덱스 정규화
    if idx < 0:
        idx = len(df) + idx
    
    if idx < 0 or idx >= len(df):
        return 0
    
    # 역순으로 연속 음봉 카운트
    count = 0
    for i in range(idx, -1, -1):
        if bearish.iloc[i]:
            count += 1
        else:
            break
    
    return count


# =============================================================================
# 캔들 패턴 분석
# =============================================================================

def analyze_candle(
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> Dict[str, float]:
    """
    단일 캔들 분석
    
    Args:
        open_price: 시가
        high: 고가
        low: 저가
        close: 종가
    
    Returns:
        캔들 분석 결과 딕셔너리
        {
            'body_size': float,      # 몸통 크기 (%)
            'upper_wick': float,     # 윗꼬리 길이 (절대값)
            'lower_wick': float,     # 아랫꼬리 길이 (절대값)
            'upper_wick_ratio': float,  # 윗꼬리 비율 (0~1)
            'lower_wick_ratio': float,  # 아랫꼬리 비율 (0~1)
            'is_bullish': bool,      # 양봉 여부
            'is_doji': bool,         # 도지 여부
            'high_eq_close': bool,   # 고가 == 종가
            'low_eq_close': bool,    # 저가 == 종가
        }
    """
    # 캔들 전체 길이
    total_range = high - low
    
    if total_range == 0:
        # 변동 없음
        return {
            'body_size': 0,
            'upper_wick': 0,
            'lower_wick': 0,
            'upper_wick_ratio': 0,
            'lower_wick_ratio': 0,
            'is_bullish': False,
            'is_doji': True,
            'high_eq_close': True,
            'low_eq_close': True,
        }
    
    # 몸통 크기
    body = abs(close - open_price)
    body_ratio = body / total_range
    body_pct = body / open_price * 100 if open_price > 0 else 0
    
    # 양봉/음봉 판별
    is_bullish = close > open_price
    
    # 꼬리 계산
    if is_bullish:
        upper_wick = high - close
        lower_wick = open_price - low
    else:
        upper_wick = high - open_price
        lower_wick = close - low
    
    # 꼬리 비율
    upper_wick_ratio = upper_wick / total_range if total_range > 0 else 0
    lower_wick_ratio = lower_wick / total_range if total_range > 0 else 0
    
    # 도지 판별 (몸통이 전체의 10% 미만)
    is_doji = body_ratio < 0.1
    
    # 고가/저가와 종가 일치 여부 (0.1% 오차 허용)
    tolerance = high * 0.001
    high_eq_close = abs(high - close) < tolerance
    low_eq_close = abs(low - close) < tolerance
    
    return {
        'body_size': body_pct,
        'upper_wick': upper_wick,
        'lower_wick': lower_wick,
        'upper_wick_ratio': upper_wick_ratio,
        'lower_wick_ratio': lower_wick_ratio,
        'is_bullish': is_bullish,
        'is_doji': is_doji,
        'high_eq_close': high_eq_close,
        'low_eq_close': low_eq_close,
    }


def analyze_candles_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    데이터프레임의 모든 캔들 분석
    
    Args:
        df: OHLCV 데이터프레임
    
    Returns:
        캔들 분석 컬럼이 추가된 데이터프레임
    """
    result = df.copy()
    
    # 캔들 전체 길이
    total_range = df['high'] - df['low']
    total_range = total_range.replace(0, np.nan)
    
    # 몸통 크기
    body = (df['close'] - df['open']).abs()
    result['body_size'] = body / df['open'] * 100
    
    # 양봉 여부
    result['is_bullish'] = df['close'] > df['open']
    
    # 윗꼬리 계산
    upper_wick_bull = df['high'] - df['close']
    upper_wick_bear = df['high'] - df['open']
    result['upper_wick'] = np.where(result['is_bullish'], upper_wick_bull, upper_wick_bear)
    result['upper_wick_ratio'] = result['upper_wick'] / total_range
    
    # 아랫꼬리 계산
    lower_wick_bull = df['open'] - df['low']
    lower_wick_bear = df['close'] - df['low']
    result['lower_wick'] = np.where(result['is_bullish'], lower_wick_bull, lower_wick_bear)
    result['lower_wick_ratio'] = result['lower_wick'] / total_range
    
    # 도지 여부
    body_ratio = body / total_range
    result['is_doji'] = body_ratio < 0.1
    
    # 고가=종가 여부
    tolerance = df['high'] * 0.001
    result['high_eq_close'] = (df['high'] - df['close']).abs() < tolerance
    
    # 저가=종가 여부
    result['low_eq_close'] = (df['low'] - df['close']).abs() < tolerance
    
    return result


# =============================================================================
# 등락률 (Change Rate)
# =============================================================================

def calculate_change_rate(
    df: pd.DataFrame,
    period: int = 1,
) -> pd.Series:
    """
    등락률 계산
    
    Args:
        df: 데이터프레임 (close 컬럼 필요)
        period: 비교 기간 (1 = 전일 대비)
    
    Returns:
        등락률 시리즈 (%)
    """
    return df['close'].pct_change(periods=period) * 100


def calculate_change_rate_single(
    current_price: float,
    previous_price: float,
) -> float:
    """
    단일 시점 등락률 계산
    
    Args:
        current_price: 현재가
        previous_price: 이전가
    
    Returns:
        등락률 (%)
    """
    if previous_price == 0:
        return 0.0
    
    return (current_price - previous_price) / previous_price * 100


# =============================================================================
# 모든 지표 일괄 계산
# =============================================================================

def calculate_all_indicators(
    df: pd.DataFrame,
    cci_period: int = 14,
    ma_period: int = 20,
    volume_period: int = 20,
) -> pd.DataFrame:
    """
    모든 기술적 지표 일괄 계산
    
    Args:
        df: OHLCV 데이터프레임
        cci_period: CCI 계산 기간
        ma_period: 이동평균 기간
        volume_period: 거래량 평균 기간
    
    Returns:
        지표가 추가된 데이터프레임
    """
    result = df.copy()
    
    # 이동평균
    result['ma5'] = calculate_sma(df['close'], 5)
    result['ma10'] = calculate_sma(df['close'], 10)
    result['ma20'] = calculate_sma(df['close'], ma_period)
    result['ma60'] = calculate_sma(df['close'], 60)
    
    # CCI
    result['cci'] = calculate_cci(df, period=cci_period)
    
    # 이격도
    result['distance_ma20'] = calculate_distance_from_ma(df, period=ma_period)
    
    # 거래량 비율
    result['volume_ratio'] = calculate_volume_ratio(df, period=volume_period)
    
    # 등락률
    result['change_pct'] = calculate_change_rate(df)
    
    # MA20 상승 추세 (3일)
    result['ma20_3day_up'] = calculate_ma_trend(df, period=ma_period, lookback=3)
    
    # 캔들 분석
    result = analyze_candles_df(result)
    
    return result


# =============================================================================
# 실시간 지표 계산 헬퍼
# =============================================================================

class RealtimeIndicators:
    """
    실시간 지표 계산을 위한 헬퍼 클래스
    
    최근 데이터를 메모리에 유지하면서 실시간으로
    지표를 계산합니다.
    """
    
    def __init__(
        self,
        cci_period: int = 14,
        ma_period: int = 20,
        volume_period: int = 20,
    ):
        self.cci_period = cci_period
        self.ma_period = ma_period
        self.volume_period = volume_period
        
        # 데이터 버퍼
        self._tp_history: list = []
        self._close_history: list = []
        self._volume_history: list = []
    
    def update(
        self,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: int,
    ) -> Dict[str, float]:
        """
        새 데이터로 지표 업데이트
        
        Args:
            open_price: 시가
            high: 고가
            low: 저가
            close: 종가
            volume: 거래량
        
        Returns:
            계산된 지표 딕셔너리
        """
        # Typical Price 추가
        tp = (high + low + close) / 3
        self._tp_history.append(tp)
        if len(self._tp_history) > self.cci_period:
            self._tp_history.pop(0)
        
        # 종가 히스토리 추가
        self._close_history.append(close)
        if len(self._close_history) > self.ma_period:
            self._close_history.pop(0)
        
        # 거래량 히스토리 추가
        self._volume_history.append(volume)
        if len(self._volume_history) > self.volume_period:
            self._volume_history.pop(0)
        
        # 지표 계산
        cci = calculate_cci_single(
            high, low, close,
            self._tp_history[:-1],
            self.cci_period
        )
        
        ma20 = np.mean(self._close_history) if len(self._close_history) >= self.ma_period else close
        distance_ma20 = calculate_distance_single(close, ma20)
        
        avg_volume = np.mean(self._volume_history) if len(self._volume_history) >= self.volume_period else volume
        volume_ratio = calculate_volume_ratio_single(volume, avg_volume)
        
        candle = analyze_candle(open_price, high, low, close)
        
        return {
            'cci': cci,
            'ma20': ma20,
            'distance_ma20': distance_ma20,
            'volume_ratio': volume_ratio,
            **candle,
        }
    
    def reset(self):
        """히스토리 초기화"""
        self._tp_history.clear()
        self._close_history.clear()
        self._volume_history.clear()


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("Indicators 모듈 테스트")
    print("=" * 60)
    
    # 테스트 데이터 생성
    np.random.seed(42)
    n = 30
    
    dates = pd.date_range('2024-01-01', periods=n, freq='D')
    close = 100 + np.cumsum(np.random.randn(n) * 2)
    
    df = pd.DataFrame({
        'date': dates,
        'open': close - np.random.rand(n) * 2,
        'high': close + np.random.rand(n) * 3,
        'low': close - np.random.rand(n) * 3,
        'close': close,
        'volume': np.random.randint(100000, 500000, n),
    })
    
    print("\n1. 원본 데이터 (마지막 5행):")
    print(df.tail())
    
    # 모든 지표 계산
    print("\n2. 지표 계산...")
    df_with_indicators = calculate_all_indicators(df)
    
    print("\n3. 계산된 지표 (마지막 행):")
    last_row = df_with_indicators.iloc[-1]
    indicators = ['cci', 'distance_ma20', 'volume_ratio', 'change_pct', 
                  'ma20_3day_up', 'upper_wick_ratio', 'is_bullish', 'high_eq_close']
    
    for ind in indicators:
        val = last_row[ind]
        if isinstance(val, (bool, np.bool_)):
            print(f"   {ind}: {val}")
        else:
            print(f"   {ind}: {val:.2f}")
    
    # 연속 양봉 테스트
    print("\n4. 연속 양봉 일수:", count_consecutive_bullish(df_with_indicators))
    
    # 실시간 지표 계산 테스트
    print("\n5. 실시간 지표 계산 테스트...")
    rt = RealtimeIndicators()
    
    for i in range(min(20, len(df))):
        row = df.iloc[i]
        indicators = rt.update(
            row['open'], row['high'], row['low'], row['close'], row['volume']
        )
    
    print(f"   CCI: {indicators['cci']:.2f}")
    print(f"   이격도: {indicators['distance_ma20']:.2f}%")
    print(f"   거래량비율: {indicators['volume_ratio']:.2f}x")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
