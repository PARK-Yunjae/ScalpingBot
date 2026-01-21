#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v3.0 - Minute Indicators (분봉 기술적 지표)
============================================================================
5분봉 기반 스캘핑용 기술적 지표 계산

지표:
- CCI (14봉): 모멘텀
- RSI (14봉): 과매수/과매도
- VWAP: 당일 거래량 가중 평균가
- 거래량비: 평균 대비 현재 거래량
- EMA (5, 10, 20): 단기 추세

사용법:
    calc = MinuteIndicators()
    calc.update(ohlcv_data)
    indicators = calc.get_indicators()
============================================================================
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from collections import deque
import logging

logger = logging.getLogger('ScalpingBot.MinuteIndicators')


# =============================================================================
# 상수
# =============================================================================

CCI_PERIOD = 14          # CCI 기간 (5분봉 14개 = 70분)
RSI_PERIOD = 14          # RSI 기간
EMA_PERIODS = [5, 10, 20]  # EMA 기간들
VOLUME_AVG_PERIOD = 10   # 거래량 평균 기간


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class OHLCV:
    """단일 봉 데이터"""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    
    @property
    def typical_price(self) -> float:
        """TP = (H + L + C) / 3"""
        return (self.high + self.low + self.close) / 3
    
    @property
    def is_bullish(self) -> bool:
        """양봉 여부"""
        return self.close > self.open
    
    @property
    def body_ratio(self) -> float:
        """몸통 비율"""
        total_range = self.high - self.low
        if total_range == 0:
            return 0
        body = abs(self.close - self.open)
        return body / total_range


@dataclass
class MinuteIndicatorResult:
    """분봉 지표 계산 결과"""
    # 기본 정보
    timestamp: str = ""
    price: float = 0.0
    
    # 모멘텀 지표
    cci: float = 0.0
    rsi: float = 50.0
    
    # 추세 지표
    ema5: float = 0.0
    ema10: float = 0.0
    ema20: float = 0.0
    
    # VWAP
    vwap: float = 0.0
    vwap_distance: float = 0.0  # VWAP 이격도 (%)
    
    # 거래량
    volume: int = 0
    volume_ratio: float = 1.0   # 평균 대비 배수
    
    # 가격 정보
    day_high: float = 0.0       # 당일 고가
    day_low: float = 0.0        # 당일 저가
    day_change_pct: float = 0.0 # 당일 등락률
    from_day_high_pct: float = 0.0  # 당일 고점 대비 (%)
    
    # 캔들 정보
    is_bullish: bool = False
    body_ratio: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'timestamp': self.timestamp,
            'price': self.price,
            'cci': self.cci,
            'rsi': self.rsi,
            'ema5': self.ema5,
            'ema10': self.ema10,
            'ema20': self.ema20,
            'vwap': self.vwap,
            'vwap_distance': self.vwap_distance,
            'volume': self.volume,
            'volume_ratio': self.volume_ratio,
            'day_high': self.day_high,
            'day_low': self.day_low,
            'day_change_pct': self.day_change_pct,
            'from_day_high_pct': self.from_day_high_pct,
            'is_bullish': self.is_bullish,
            'body_ratio': self.body_ratio,
        }


# =============================================================================
# 분봉 지표 계산기
# =============================================================================

class MinuteIndicators:
    """
    분봉 기반 기술적 지표 계산기
    
    실시간으로 봉 데이터를 받아 지표를 계산합니다.
    """
    
    def __init__(
        self,
        cci_period: int = CCI_PERIOD,
        rsi_period: int = RSI_PERIOD,
        volume_period: int = VOLUME_AVG_PERIOD,
        prev_close: float = 0.0,  # 전일 종가
    ):
        self.cci_period = cci_period
        self.rsi_period = rsi_period
        self.volume_period = volume_period
        self.prev_close = prev_close
        
        # 데이터 버퍼
        self._candles: deque = deque(maxlen=max(cci_period, rsi_period, 20) + 5)
        
        # VWAP 계산용
        self._cumulative_tp_volume: float = 0.0
        self._cumulative_volume: int = 0
        
        # 당일 고저가
        self._day_high: float = 0.0
        self._day_low: float = float('inf')
        self._day_open: float = 0.0
        
        # RSI 계산용 (Wilder's smoothing)
        self._avg_gain: float = 0.0
        self._avg_loss: float = 0.0
        self._rsi_initialized: bool = False
        
        # EMA 계산용
        self._ema_values: Dict[int, float] = {}
    
    def reset(self, prev_close: float = 0.0):
        """
        일일 리셋 (장 시작 시 호출)
        
        Args:
            prev_close: 전일 종가
        """
        self._candles.clear()
        self._cumulative_tp_volume = 0.0
        self._cumulative_volume = 0
        self._day_high = 0.0
        self._day_low = float('inf')
        self._day_open = 0.0
        self._avg_gain = 0.0
        self._avg_loss = 0.0
        self._rsi_initialized = False
        self._ema_values.clear()
        self.prev_close = prev_close
        
        logger.debug(f"MinuteIndicators reset (prev_close={prev_close})")
    
    def update(self, candle: OHLCV) -> MinuteIndicatorResult:
        """
        새 봉 데이터로 지표 업데이트
        
        Args:
            candle: OHLCV 봉 데이터
        
        Returns:
            MinuteIndicatorResult
        """
        # 봉 추가
        self._candles.append(candle)
        
        # 당일 고저가 업데이트
        if self._day_open == 0:
            self._day_open = candle.open
        self._day_high = max(self._day_high, candle.high)
        self._day_low = min(self._day_low, candle.low)
        
        # VWAP 업데이트
        self._cumulative_tp_volume += candle.typical_price * candle.volume
        self._cumulative_volume += candle.volume
        
        # 지표 계산
        result = MinuteIndicatorResult()
        result.timestamp = candle.timestamp
        result.price = candle.close
        result.volume = candle.volume
        result.is_bullish = candle.is_bullish
        result.body_ratio = candle.body_ratio
        
        # 당일 정보
        result.day_high = self._day_high
        result.day_low = self._day_low
        
        # 당일 등락률
        if self.prev_close > 0:
            result.day_change_pct = (candle.close - self.prev_close) / self.prev_close * 100
        elif self._day_open > 0:
            result.day_change_pct = (candle.close - self._day_open) / self._day_open * 100
        
        # 고점 대비
        if self._day_high > 0:
            result.from_day_high_pct = (candle.close - self._day_high) / self._day_high * 100
        
        # VWAP
        if self._cumulative_volume > 0:
            result.vwap = self._cumulative_tp_volume / self._cumulative_volume
            if result.vwap > 0:
                result.vwap_distance = (candle.close - result.vwap) / result.vwap * 100
        
        # 거래량 비율
        result.volume_ratio = self._calc_volume_ratio()
        
        # CCI
        result.cci = self._calc_cci()
        
        # RSI
        result.rsi = self._calc_rsi()
        
        # EMA
        result.ema5 = self._calc_ema(5)
        result.ema10 = self._calc_ema(10)
        result.ema20 = self._calc_ema(20)
        
        return result
    
    def update_from_dict(self, data: Dict[str, Any]) -> MinuteIndicatorResult:
        """딕셔너리에서 업데이트"""
        candle = OHLCV(
            timestamp=data.get('timestamp', ''),
            open=float(data.get('open', 0)),
            high=float(data.get('high', 0)),
            low=float(data.get('low', 0)),
            close=float(data.get('close', 0)),
            volume=int(data.get('volume', 0)),
        )
        return self.update(candle)
    
    def get_current(self) -> Optional[MinuteIndicatorResult]:
        """현재 지표 반환 (마지막 봉 기준)"""
        if not self._candles:
            return None
        
        candle = self._candles[-1]
        return self.update(candle)
    
    # =========================================================================
    # CCI 계산
    # =========================================================================
    
    def _calc_cci(self) -> float:
        """
        CCI = (TP - SMA(TP)) / (0.015 * MAD)
        """
        if len(self._candles) < self.cci_period:
            return 0.0
        
        # 최근 N개 TP
        tps = [c.typical_price for c in list(self._candles)[-self.cci_period:]]
        
        # SMA(TP)
        tp_mean = np.mean(tps)
        
        # MAD (Mean Absolute Deviation)
        mad = np.mean([abs(tp - tp_mean) for tp in tps])
        
        if mad == 0:
            return 0.0
        
        # CCI
        current_tp = self._candles[-1].typical_price
        cci = (current_tp - tp_mean) / (0.015 * mad)
        
        return float(cci)
    
    # =========================================================================
    # RSI 계산 (Wilder's Smoothing)
    # =========================================================================
    
    def _calc_rsi(self) -> float:
        """
        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss
        """
        if len(self._candles) < 2:
            return 50.0
        
        # 현재 변화량
        current_close = self._candles[-1].close
        prev_close = self._candles[-2].close
        change = current_close - prev_close
        
        gain = max(change, 0)
        loss = abs(min(change, 0))
        
        if not self._rsi_initialized:
            # 초기화 (첫 N개 봉)
            if len(self._candles) < self.rsi_period + 1:
                return 50.0
            
            # 초기 평균 계산
            gains = []
            losses = []
            candles = list(self._candles)
            
            for i in range(1, self.rsi_period + 1):
                chg = candles[i].close - candles[i-1].close
                gains.append(max(chg, 0))
                losses.append(abs(min(chg, 0)))
            
            self._avg_gain = np.mean(gains)
            self._avg_loss = np.mean(losses)
            self._rsi_initialized = True
        else:
            # Wilder's smoothing
            self._avg_gain = (self._avg_gain * (self.rsi_period - 1) + gain) / self.rsi_period
            self._avg_loss = (self._avg_loss * (self.rsi_period - 1) + loss) / self.rsi_period
        
        if self._avg_loss == 0:
            return 100.0 if self._avg_gain > 0 else 50.0
        
        rs = self._avg_gain / self._avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return float(rsi)
    
    # =========================================================================
    # EMA 계산
    # =========================================================================
    
    def _calc_ema(self, period: int) -> float:
        """
        EMA = Price × k + EMA(prev) × (1 - k)
        k = 2 / (period + 1)
        """
        if len(self._candles) < period:
            # 초기: SMA 사용
            closes = [c.close for c in list(self._candles)[-period:]]
            return np.mean(closes) if closes else 0.0
        
        k = 2 / (period + 1)
        current_price = self._candles[-1].close
        
        if period not in self._ema_values:
            # 첫 EMA: SMA로 시작
            closes = [c.close for c in list(self._candles)[-period:]]
            self._ema_values[period] = np.mean(closes)
        else:
            # EMA 업데이트
            prev_ema = self._ema_values[period]
            self._ema_values[period] = current_price * k + prev_ema * (1 - k)
        
        return self._ema_values[period]
    
    # =========================================================================
    # 거래량 비율
    # =========================================================================
    
    def _calc_volume_ratio(self) -> float:
        """현재 거래량 / 평균 거래량"""
        if len(self._candles) < 2:
            return 1.0
        
        # 최근 N개 거래량 (현재 제외)
        volumes = [c.volume for c in list(self._candles)[-(self.volume_period+1):-1]]
        
        if not volumes:
            return 1.0
        
        avg_volume = np.mean(volumes)
        
        if avg_volume == 0:
            return 1.0
        
        current_volume = self._candles[-1].volume
        return current_volume / avg_volume
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def get_candle_count(self) -> int:
        """저장된 봉 개수"""
        return len(self._candles)
    
    def get_recent_candles(self, n: int = 5) -> List[OHLCV]:
        """최근 N개 봉"""
        return list(self._candles)[-n:]
    
    def is_ready(self) -> bool:
        """지표 계산 준비 완료 여부"""
        return len(self._candles) >= max(self.cci_period, self.rsi_period)


# =============================================================================
# DataFrame 기반 일괄 계산 (백테스팅용)
# =============================================================================

def calculate_minute_indicators_df(
    df: pd.DataFrame,
    prev_close: float = 0.0,
) -> pd.DataFrame:
    """
    DataFrame에 분봉 지표 일괄 계산
    
    Args:
        df: OHLCV DataFrame (open, high, low, close, volume 컬럼 필요)
        prev_close: 전일 종가
    
    Returns:
        지표가 추가된 DataFrame
    """
    result = df.copy()
    
    # Typical Price
    result['tp'] = (df['high'] + df['low'] + df['close']) / 3
    
    # VWAP
    result['cumulative_tp_vol'] = (result['tp'] * result['volume']).cumsum()
    result['cumulative_vol'] = result['volume'].cumsum()
    result['vwap'] = result['cumulative_tp_vol'] / result['cumulative_vol']
    result['vwap_distance'] = (result['close'] - result['vwap']) / result['vwap'] * 100
    
    # CCI
    tp_sma = result['tp'].rolling(window=CCI_PERIOD).mean()
    tp_mad = result['tp'].rolling(window=CCI_PERIOD).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
    )
    result['cci'] = (result['tp'] - tp_sma) / (0.015 * tp_mad)
    
    # RSI
    delta = result['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)
    
    avg_gain = gain.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    avg_loss = loss.ewm(alpha=1/RSI_PERIOD, min_periods=RSI_PERIOD).mean()
    
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result['rsi'] = 100 - (100 / (1 + rs))
    result['rsi'] = result['rsi'].fillna(50)
    
    # EMA
    for period in EMA_PERIODS:
        result[f'ema{period}'] = result['close'].ewm(span=period, adjust=False).mean()
    
    # 거래량 비율
    result['volume_ratio'] = result['volume'] / result['volume'].rolling(window=VOLUME_AVG_PERIOD).mean()
    result['volume_ratio'] = result['volume_ratio'].fillna(1.0)
    
    # 당일 고저가
    result['day_high'] = result['high'].cummax()
    result['day_low'] = result['low'].cummin()
    
    # 당일 등락률
    if prev_close > 0:
        result['day_change_pct'] = (result['close'] - prev_close) / prev_close * 100
    else:
        first_open = result['open'].iloc[0] if len(result) > 0 else 0
        if first_open > 0:
            result['day_change_pct'] = (result['close'] - first_open) / first_open * 100
        else:
            result['day_change_pct'] = 0
    
    # 고점 대비
    result['from_day_high_pct'] = (result['close'] - result['day_high']) / result['day_high'] * 100
    
    # 양봉 여부
    result['is_bullish'] = result['close'] > result['open']
    
    # 정리
    drop_cols = ['tp', 'cumulative_tp_vol', 'cumulative_vol']
    result = result.drop(columns=[c for c in drop_cols if c in result.columns])
    
    return result


# =============================================================================
# 테스트
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    
    print("=" * 60)
    print("MinuteIndicators 테스트")
    print("=" * 60)
    
    # 테스트 데이터 생성
    np.random.seed(42)
    n = 30
    
    prices = [10000]
    for i in range(n - 1):
        change = np.random.randn() * 50
        prices.append(prices[-1] + change)
    
    candles = []
    for i, close in enumerate(prices):
        candle = OHLCV(
            timestamp=f"09:{i:02d}",
            open=close - np.random.rand() * 30,
            high=close + np.random.rand() * 50,
            low=close - np.random.rand() * 50,
            close=close,
            volume=int(np.random.randint(10000, 50000)),
        )
        candles.append(candle)
    
    # 실시간 계산 테스트
    print("\n1. 실시간 계산 테스트")
    calc = MinuteIndicators(prev_close=prices[0])
    
    for i, candle in enumerate(candles):
        result = calc.update(candle)
        
        if i >= 14:  # CCI/RSI 준비 후
            print(f"   [{candle.timestamp}] "
                  f"가격:{result.price:.0f} "
                  f"CCI:{result.cci:+.1f} "
                  f"RSI:{result.rsi:.1f} "
                  f"VWAP:{result.vwap:.0f} "
                  f"거래량비:{result.volume_ratio:.2f}x")
    
    # DataFrame 테스트
    print("\n2. DataFrame 일괄 계산 테스트")
    df = pd.DataFrame([
        {
            'timestamp': c.timestamp,
            'open': c.open,
            'high': c.high,
            'low': c.low,
            'close': c.close,
            'volume': c.volume,
        }
        for c in candles
    ])
    
    df_with_indicators = calculate_minute_indicators_df(df, prev_close=prices[0])
    print(df_with_indicators[['timestamp', 'close', 'cci', 'rsi', 'vwap', 'volume_ratio']].tail(10))
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
