#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Models (데이터 모델)
============================================================================
데이터베이스 테이블에 대응하는 데이터 모델 정의

모델:
- Trade: 매매 기록
- DailySummary: 일일 요약
- Position: 포지션
- AILearning: AI 학습 데이터
- Setting: 설정

사용법:
    trade = Trade(
        stock_code="005930",
        stock_name="삼성전자",
        trade_type="BUY",
        ...
    )
============================================================================
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from datetime import datetime, date
from enum import Enum
import json


# =============================================================================
# 열거형
# =============================================================================

class TradeType(Enum):
    """거래 유형"""
    BUY = "BUY"
    SELL = "SELL"


class SellReason(Enum):
    """매도 사유"""
    TAKE_PROFIT = "익절"
    STOP_LOSS = "손절"
    TRAILING_STOP = "트레일링"
    TIME_LIMIT = "시간청산"
    EMERGENCY = "비상청산"
    MANUAL = "수동청산"


class PositionGrade(Enum):
    """포지션 등급"""
    S = "S"  # 90점 이상
    A = "A"  # 80점 이상
    B = "B"  # 70점 이상
    C = "C"  # 60점 이상


class MarketMode(Enum):
    """시장 모드"""
    NORMAL = "NORMAL"
    CONSERVATIVE = "CONSERVATIVE"
    EMERGENCY = "EMERGENCY"


class AIDecision(Enum):
    """AI 결정"""
    BUY = "BUY"
    HOLD = "HOLD"


# =============================================================================
# 데이터 모델
# =============================================================================

@dataclass
class Trade:
    """매매 기록"""
    stock_code: str
    stock_name: str
    trade_type: str           # BUY / SELL
    quantity: int
    price: float
    amount: float
    trade_date: date = None
    profit: float = 0.0
    profit_pct: float = 0.0
    rule_score: float = 0.0
    ai_confidence: float = 0.0
    sell_reason: str = ""
    market_mode: str = ""
    id: int = None
    created_at: datetime = None
    
    def __post_init__(self):
        if self.trade_date is None:
            self.trade_date = date.today()
        if self.created_at is None:
            self.created_at = datetime.now()
    
    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            'id': self.id,
            'trade_date': str(self.trade_date),
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'trade_type': self.trade_type,
            'quantity': self.quantity,
            'price': self.price,
            'amount': self.amount,
            'profit': self.profit,
            'profit_pct': self.profit_pct,
            'rule_score': self.rule_score,
            'ai_confidence': self.ai_confidence,
            'sell_reason': self.sell_reason,
            'market_mode': self.market_mode,
            'created_at': str(self.created_at) if self.created_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Trade':
        """딕셔너리에서 생성"""
        return cls(
            id=data.get('id'),
            trade_date=data.get('trade_date'),
            stock_code=data['stock_code'],
            stock_name=data.get('stock_name', ''),
            trade_type=data['trade_type'],
            quantity=data['quantity'],
            price=data['price'],
            amount=data['amount'],
            profit=data.get('profit', 0),
            profit_pct=data.get('profit_pct', 0),
            rule_score=data.get('rule_score', 0),
            ai_confidence=data.get('ai_confidence', 0),
            sell_reason=data.get('sell_reason', ''),
            market_mode=data.get('market_mode', ''),
        )


@dataclass
class DailySummary:
    """일일 요약"""
    trade_date: date
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_profit: float = 0.0
    win_rate: float = 0.0
    avg_profit: float = 0.0
    avg_loss: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    id: int = None
    created_at: datetime = None
    
    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            'id': self.id,
            'trade_date': str(self.trade_date),
            'total_trades': self.total_trades,
            'wins': self.wins,
            'losses': self.losses,
            'total_profit': self.total_profit,
            'win_rate': self.win_rate,
            'avg_profit': self.avg_profit,
            'avg_loss': self.avg_loss,
            'max_profit': self.max_profit,
            'max_loss': self.max_loss,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'DailySummary':
        """딕셔너리에서 생성"""
        return cls(
            id=data.get('id'),
            trade_date=data['trade_date'],
            total_trades=data.get('total_trades', 0),
            wins=data.get('wins', 0),
            losses=data.get('losses', 0),
            total_profit=data.get('total_profit', 0),
            win_rate=data.get('win_rate', 0),
            avg_profit=data.get('avg_profit', 0),
            avg_loss=data.get('avg_loss', 0),
            max_profit=data.get('max_profit', 0),
            max_loss=data.get('max_loss', 0),
        )
    
    def calculate_stats(self, trades: List[Trade]):
        """거래 목록에서 통계 계산"""
        sell_trades = [t for t in trades if t.trade_type == 'SELL']
        
        self.total_trades = len(sell_trades)
        
        profits = [t.profit for t in sell_trades if t.profit > 0]
        losses = [t.profit for t in sell_trades if t.profit < 0]
        
        self.wins = len(profits)
        self.losses = len(losses)
        self.total_profit = sum(t.profit for t in sell_trades)
        
        if self.total_trades > 0:
            self.win_rate = self.wins / self.total_trades * 100
        
        if profits:
            self.avg_profit = sum(profits) / len(profits)
            self.max_profit = max(profits)
        
        if losses:
            self.avg_loss = sum(losses) / len(losses)
            self.max_loss = min(losses)


@dataclass
class Position:
    """포지션"""
    stock_code: str
    stock_name: str
    entry_price: float
    quantity: int
    entry_time: datetime = None
    score: float = 0.0
    ai_confidence: float = 0.0
    grade: str = "C"
    high_price: float = 0.0
    target_profit: float = 1.0
    trailing_stop: float = 0.3
    stop_loss: float = -1.5
    id: int = None
    updated_at: datetime = None
    
    # 계산 필드 (DB에 저장 안함)
    current_price: float = field(default=0.0, repr=False)
    profit_pct: float = field(default=0.0, repr=False)
    
    def __post_init__(self):
        if self.entry_time is None:
            self.entry_time = datetime.now()
        if self.high_price == 0:
            self.high_price = self.entry_price
    
    def update_price(self, price: float):
        """가격 업데이트"""
        self.current_price = price
        self.profit_pct = (price - self.entry_price) / self.entry_price * 100
        
        if price > self.high_price:
            self.high_price = price
    
    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            'id': self.id,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'entry_price': self.entry_price,
            'quantity': self.quantity,
            'entry_time': str(self.entry_time) if self.entry_time else None,
            'score': self.score,
            'ai_confidence': self.ai_confidence,
            'grade': self.grade,
            'high_price': self.high_price,
            'target_profit': self.target_profit,
            'trailing_stop': self.trailing_stop,
            'stop_loss': self.stop_loss,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Position':
        """딕셔너리에서 생성"""
        return cls(
            id=data.get('id'),
            stock_code=data['stock_code'],
            stock_name=data.get('stock_name', ''),
            entry_price=data['entry_price'],
            quantity=data['quantity'],
            entry_time=data.get('entry_time'),
            score=data.get('score', 0),
            ai_confidence=data.get('ai_confidence', 0),
            grade=data.get('grade', 'C'),
            high_price=data.get('high_price', 0),
            target_profit=data.get('target_profit', 1.0),
            trailing_stop=data.get('trailing_stop', 0.3),
            stop_loss=data.get('stop_loss', -1.5),
        )


@dataclass
class AILearning:
    """AI 학습 데이터"""
    stock_code: str
    decision: str              # BUY / HOLD
    confidence: float
    trade_date: date = None
    actual_profit: float = None
    is_win: bool = None
    rule_score: float = 0.0
    market_mode: str = ""
    indicators: Dict = None
    id: int = None
    created_at: datetime = None
    
    def __post_init__(self):
        if self.trade_date is None:
            self.trade_date = date.today()
        if self.indicators is None:
            self.indicators = {}
    
    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            'id': self.id,
            'trade_date': str(self.trade_date),
            'stock_code': self.stock_code,
            'decision': self.decision,
            'confidence': self.confidence,
            'actual_profit': self.actual_profit,
            'is_win': self.is_win,
            'rule_score': self.rule_score,
            'market_mode': self.market_mode,
            'indicators': json.dumps(self.indicators) if self.indicators else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'AILearning':
        """딕셔너리에서 생성"""
        indicators = data.get('indicators')
        if isinstance(indicators, str):
            indicators = json.loads(indicators)
        
        return cls(
            id=data.get('id'),
            trade_date=data.get('trade_date'),
            stock_code=data['stock_code'],
            decision=data['decision'],
            confidence=data['confidence'],
            actual_profit=data.get('actual_profit'),
            is_win=data.get('is_win'),
            rule_score=data.get('rule_score', 0),
            market_mode=data.get('market_mode', ''),
            indicators=indicators,
        )


@dataclass
class Setting:
    """설정"""
    key: str
    value: str
    updated_at: datetime = None
    
    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            'key': self.key,
            'value': self.value,
            'updated_at': str(self.updated_at) if self.updated_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Setting':
        """딕셔너리에서 생성"""
        return cls(
            key=data['key'],
            value=data['value'],
            updated_at=data.get('updated_at'),
        )


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("Models 테스트")
    print("=" * 60)
    
    # 1. Trade 모델 테스트
    print("\n1. Trade 모델:")
    trade = Trade(
        stock_code="005930",
        stock_name="삼성전자",
        trade_type="BUY",
        quantity=10,
        price=70000,
        amount=700000,
    )
    print(f"   {trade.stock_name} {trade.quantity}주 @ {trade.price:,.0f}원")
    print(f"   딕셔너리: {list(trade.to_dict().keys())}")
    
    # 2. Position 모델 테스트
    print("\n2. Position 모델:")
    pos = Position(
        stock_code="005930",
        stock_name="삼성전자",
        entry_price=70000,
        quantity=10,
        score=85,
    )
    pos.update_price(71000)
    print(f"   {pos.stock_name}: 진입 {pos.entry_price:,.0f} → 현재 {pos.current_price:,.0f}")
    print(f"   수익률: {pos.profit_pct:+.2f}%")
    
    # 3. DailySummary 모델 테스트
    print("\n3. DailySummary 모델:")
    summary = DailySummary(trade_date=date.today())
    trades = [
        Trade("005930", "삼성전자", "SELL", 10, 71000, 710000, profit=10000),
        Trade("000660", "SK하이닉스", "SELL", 5, 95000, 475000, profit=-5000),
    ]
    summary.calculate_stats(trades)
    print(f"   총 거래: {summary.total_trades}건")
    print(f"   승률: {summary.win_rate:.1f}%")
    print(f"   총 수익: {summary.total_profit:+,.0f}원")
    
    # 4. AILearning 모델 테스트
    print("\n4. AILearning 모델:")
    learning = AILearning(
        stock_code="005930",
        decision="BUY",
        confidence=0.85,
        rule_score=80,
        indicators={'cci': 45, 'volume_ratio': 1.5},
    )
    print(f"   {learning.stock_code}: {learning.decision} ({learning.confidence*100:.0f}%)")
    print(f"   지표: {learning.indicators}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
