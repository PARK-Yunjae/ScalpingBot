#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Data 모듈
============================================================================
데이터 수집 및 시장 모니터링

모듈:
- market_monitor: 코스피/코스닥 지수 모니터링
- ohlcv_loader: OHLCV 데이터 로드
- universe_filter: 유니버스 필터링
- realtime_feed: 실시간 시세
- stock_mapper: 종목 코드 매핑
============================================================================
"""

from scalping.data.market_monitor import (
    MarketMonitor,
    MarketState,
    MarketMode,
)

from scalping.data.ohlcv_loader import (
    OHLCVLoader,
)

from scalping.data.universe_filter import (
    UniverseFilter,
    StockInfo,
)

from scalping.data.realtime_feed import (
    RealtimeFeed,
    PriceTick,
    OrderbookTick,
    FeedType,
)

# 하위 호환성을 위한 별칭 (기존 코드에서 TickData/OrderbookData 사용 시)
TickData = PriceTick
OrderbookData = OrderbookTick

from scalping.data.stock_mapper import (
    StockMapper,
    StockMeta,
    get_mapper,
    code_to_name,
    name_to_code,
)

__all__ = [
    # market_monitor
    'MarketMonitor',
    'MarketState',
    'MarketMode',
    
    # ohlcv_loader
    'OHLCVLoader',
    
    # universe_filter
    'UniverseFilter',
    'StockInfo',
    
    # realtime_feed
    'RealtimeFeed',
    'PriceTick',
    'OrderbookTick',
    'TickData',      # 별칭 (하위 호환)
    'OrderbookData', # 별칭 (하위 호환)
    'FeedType',
    
    # stock_mapper
    'StockMapper',
    'StockMeta',
    'get_mapper',
    'code_to_name',
    'name_to_code',
]
