#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Repository (데이터 레포지토리)
============================================================================
데이터베이스 접근을 추상화하는 레포지토리 계층

레포지토리:
- TradeRepository: 매매 기록
- PositionRepository: 포지션
- SummaryRepository: 일일 요약
- AILearningRepository: AI 학습
- SettingRepository: 설정

사용법:
    repo = TradeRepository(db)
    
    # 저장
    trade_id = repo.save(trade)
    
    # 조회
    trades = repo.find_by_date(date.today())
============================================================================
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, date
from abc import ABC, abstractmethod

from scalping.storage.database import Database
from scalping.storage.models import (
    Trade, DailySummary, Position, AILearning, Setting
)

# 로거 설정
logger = logging.getLogger('ScalpingBot.Repository')


# =============================================================================
# 베이스 레포지토리
# =============================================================================

class BaseRepository(ABC):
    """베이스 레포지토리"""
    
    def __init__(self, db: Database):
        self.db = db
    
    @abstractmethod
    def save(self, entity) -> int:
        """저장"""
        pass
    
    @abstractmethod
    def find_by_id(self, id: int):
        """ID로 조회"""
        pass
    
    @abstractmethod
    def delete(self, id: int) -> bool:
        """삭제"""
        pass


# =============================================================================
# Trade 레포지토리
# =============================================================================

class TradeRepository(BaseRepository):
    """매매 기록 레포지토리"""
    
    def save(self, trade: Trade) -> int:
        """매매 기록 저장"""
        if trade.id:
            # 업데이트
            self.db.execute("""
                UPDATE trades SET
                    trade_date = ?, stock_code = ?, stock_name = ?,
                    trade_type = ?, quantity = ?, price = ?, amount = ?,
                    profit = ?, profit_pct = ?, rule_score = ?,
                    ai_confidence = ?, sell_reason = ?, market_mode = ?
                WHERE id = ?
            """, (
                str(trade.trade_date), trade.stock_code, trade.stock_name,
                trade.trade_type, trade.quantity, trade.price, trade.amount,
                trade.profit, trade.profit_pct, trade.rule_score,
                trade.ai_confidence, trade.sell_reason, trade.market_mode,
                trade.id
            ))
            return trade.id
        else:
            # 삽입
            cursor = self.db.execute("""
                INSERT INTO trades (
                    trade_date, stock_code, stock_name, trade_type,
                    quantity, price, amount, profit, profit_pct,
                    rule_score, ai_confidence, sell_reason, market_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(trade.trade_date), trade.stock_code, trade.stock_name,
                trade.trade_type, trade.quantity, trade.price, trade.amount,
                trade.profit, trade.profit_pct, trade.rule_score,
                trade.ai_confidence, trade.sell_reason, trade.market_mode
            ))
            return cursor.lastrowid
    
    def find_by_id(self, id: int) -> Optional[Trade]:
        """ID로 조회"""
        data = self.db.fetch_one("SELECT * FROM trades WHERE id = ?", (id,))
        return Trade.from_dict(data) if data else None
    
    def find_by_date(self, trade_date: date) -> List[Trade]:
        """날짜로 조회"""
        rows = self.db.fetch_all(
            "SELECT * FROM trades WHERE trade_date = ? ORDER BY created_at",
            (str(trade_date),)
        )
        return [Trade.from_dict(row) for row in rows]
    
    def find_by_stock(self, stock_code: str, limit: int = 100) -> List[Trade]:
        """종목으로 조회"""
        rows = self.db.fetch_all(
            "SELECT * FROM trades WHERE stock_code = ? ORDER BY created_at DESC LIMIT ?",
            (stock_code, limit)
        )
        return [Trade.from_dict(row) for row in rows]
    
    def find_recent(self, limit: int = 100) -> List[Trade]:
        """최근 거래 조회"""
        rows = self.db.fetch_all(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        return [Trade.from_dict(row) for row in rows]
    
    def find_date_range(self, start_date: date, end_date: date) -> List[Trade]:
        """기간 조회"""
        rows = self.db.fetch_all(
            "SELECT * FROM trades WHERE trade_date BETWEEN ? AND ? ORDER BY created_at",
            (str(start_date), str(end_date))
        )
        return [Trade.from_dict(row) for row in rows]
    
    def delete(self, id: int) -> bool:
        """삭제"""
        self.db.execute("DELETE FROM trades WHERE id = ?", (id,))
        return True
    
    def count_by_date(self, trade_date: date) -> int:
        """날짜별 건수"""
        result = self.db.fetch_one(
            "SELECT COUNT(*) as cnt FROM trades WHERE trade_date = ?",
            (str(trade_date),)
        )
        return result['cnt'] if result else 0


# =============================================================================
# Position 레포지토리
# =============================================================================

class PositionRepository(BaseRepository):
    """포지션 레포지토리"""
    
    def save(self, position: Position) -> int:
        """포지션 저장"""
        if position.id:
            # 업데이트
            self.db.execute("""
                UPDATE positions SET
                    stock_name = ?, entry_price = ?, quantity = ?,
                    score = ?, ai_confidence = ?, grade = ?,
                    high_price = ?, target_profit = ?, trailing_stop = ?,
                    stop_loss = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                position.stock_name, position.entry_price, position.quantity,
                position.score, position.ai_confidence, position.grade,
                position.high_price, position.target_profit, position.trailing_stop,
                position.stop_loss, position.id
            ))
            return position.id
        else:
            # 삽입 또는 업데이트 (UPSERT)
            cursor = self.db.execute("""
                INSERT INTO positions (
                    stock_code, stock_name, entry_price, quantity, entry_time,
                    score, ai_confidence, grade, high_price,
                    target_profit, trailing_stop, stop_loss
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stock_code) DO UPDATE SET
                    entry_price = excluded.entry_price,
                    quantity = excluded.quantity,
                    high_price = excluded.high_price,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                position.stock_code, position.stock_name, position.entry_price,
                position.quantity, str(position.entry_time), position.score,
                position.ai_confidence, position.grade, position.high_price,
                position.target_profit, position.trailing_stop, position.stop_loss
            ))
            return cursor.lastrowid
    
    def find_by_id(self, id: int) -> Optional[Position]:
        """ID로 조회"""
        data = self.db.fetch_one("SELECT * FROM positions WHERE id = ?", (id,))
        return Position.from_dict(data) if data else None
    
    def find_by_code(self, stock_code: str) -> Optional[Position]:
        """종목코드로 조회"""
        data = self.db.fetch_one(
            "SELECT * FROM positions WHERE stock_code = ?",
            (stock_code,)
        )
        return Position.from_dict(data) if data else None
    
    def find_all(self) -> List[Position]:
        """전체 조회"""
        rows = self.db.fetch_all("SELECT * FROM positions ORDER BY entry_time")
        return [Position.from_dict(row) for row in rows]
    
    def delete(self, id: int) -> bool:
        """삭제"""
        self.db.execute("DELETE FROM positions WHERE id = ?", (id,))
        return True
    
    def delete_by_code(self, stock_code: str) -> bool:
        """종목코드로 삭제"""
        self.db.execute("DELETE FROM positions WHERE stock_code = ?", (stock_code,))
        return True
    
    def delete_all(self) -> int:
        """전체 삭제"""
        cursor = self.db.execute("DELETE FROM positions")
        return cursor.rowcount
    
    def count(self) -> int:
        """전체 건수"""
        result = self.db.fetch_one("SELECT COUNT(*) as cnt FROM positions")
        return result['cnt'] if result else 0


# =============================================================================
# DailySummary 레포지토리
# =============================================================================

class SummaryRepository(BaseRepository):
    """일일 요약 레포지토리"""
    
    def save(self, summary: DailySummary) -> int:
        """요약 저장 (UPSERT)"""
        cursor = self.db.execute("""
            INSERT INTO daily_summary (
                trade_date, total_trades, wins, losses, total_profit,
                win_rate, avg_profit, avg_loss, max_profit, max_loss
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                total_trades = excluded.total_trades,
                wins = excluded.wins,
                losses = excluded.losses,
                total_profit = excluded.total_profit,
                win_rate = excluded.win_rate,
                avg_profit = excluded.avg_profit,
                avg_loss = excluded.avg_loss,
                max_profit = excluded.max_profit,
                max_loss = excluded.max_loss
        """, (
            str(summary.trade_date), summary.total_trades, summary.wins,
            summary.losses, summary.total_profit, summary.win_rate,
            summary.avg_profit, summary.avg_loss, summary.max_profit,
            summary.max_loss
        ))
        return cursor.lastrowid
    
    def find_by_id(self, id: int) -> Optional[DailySummary]:
        """ID로 조회"""
        data = self.db.fetch_one("SELECT * FROM daily_summary WHERE id = ?", (id,))
        return DailySummary.from_dict(data) if data else None
    
    def find_by_date(self, trade_date: date) -> Optional[DailySummary]:
        """날짜로 조회"""
        data = self.db.fetch_one(
            "SELECT * FROM daily_summary WHERE trade_date = ?",
            (str(trade_date),)
        )
        return DailySummary.from_dict(data) if data else None
    
    def find_recent(self, limit: int = 30) -> List[DailySummary]:
        """최근 N일 조회"""
        rows = self.db.fetch_all(
            "SELECT * FROM daily_summary ORDER BY trade_date DESC LIMIT ?",
            (limit,)
        )
        return [DailySummary.from_dict(row) for row in rows]
    
    def find_date_range(self, start_date: date, end_date: date) -> List[DailySummary]:
        """기간 조회"""
        rows = self.db.fetch_all(
            "SELECT * FROM daily_summary WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
            (str(start_date), str(end_date))
        )
        return [DailySummary.from_dict(row) for row in rows]
    
    def delete(self, id: int) -> bool:
        """삭제"""
        self.db.execute("DELETE FROM daily_summary WHERE id = ?", (id,))
        return True
    
    def get_total_stats(self) -> Dict:
        """전체 통계"""
        result = self.db.fetch_one("""
            SELECT 
                SUM(total_trades) as total_trades,
                SUM(wins) as total_wins,
                SUM(losses) as total_losses,
                SUM(total_profit) as total_profit,
                AVG(win_rate) as avg_win_rate
            FROM daily_summary
        """)
        return dict(result) if result else {}


# =============================================================================
# AILearning 레포지토리
# =============================================================================

class AILearningRepository(BaseRepository):
    """AI 학습 데이터 레포지토리"""
    
    def save(self, learning: AILearning) -> int:
        """학습 데이터 저장"""
        cursor = self.db.execute("""
            INSERT INTO ai_learning (
                trade_date, stock_code, decision, confidence,
                actual_profit, is_win, rule_score, market_mode, indicators
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(learning.trade_date), learning.stock_code, learning.decision,
            learning.confidence, learning.actual_profit,
            1 if learning.is_win else 0 if learning.is_win is not None else None,
            learning.rule_score, learning.market_mode,
            str(learning.indicators) if learning.indicators else None
        ))
        return cursor.lastrowid
    
    def find_by_id(self, id: int) -> Optional[AILearning]:
        """ID로 조회"""
        data = self.db.fetch_one("SELECT * FROM ai_learning WHERE id = ?", (id,))
        return AILearning.from_dict(data) if data else None
    
    def find_recent(self, limit: int = 1000) -> List[AILearning]:
        """최근 학습 데이터"""
        rows = self.db.fetch_all(
            "SELECT * FROM ai_learning ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        return [AILearning.from_dict(row) for row in rows]
    
    def delete(self, id: int) -> bool:
        """삭제"""
        self.db.execute("DELETE FROM ai_learning WHERE id = ?", (id,))
        return True
    
    def get_stats(self) -> Dict:
        """학습 통계"""
        result = self.db.fetch_one("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN is_win = 1 THEN 1 ELSE 0 END) as wins,
                AVG(confidence) as avg_confidence,
                AVG(CASE WHEN is_win IS NOT NULL THEN actual_profit ELSE NULL END) as avg_profit
            FROM ai_learning
            WHERE actual_profit IS NOT NULL
        """)
        
        if result and result['total'] > 0:
            wins = result['wins'] or 0
            total = result['total']
            return {
                'total': total,
                'wins': wins,
                'winrate': wins / total * 100 if total > 0 else 0,
                'avg_confidence': result['avg_confidence'] or 0,
                'avg_profit': result['avg_profit'] or 0,
            }
        return {'total': 0, 'wins': 0, 'winrate': 0, 'avg_confidence': 0, 'avg_profit': 0}


# =============================================================================
# Setting 레포지토리
# =============================================================================

class SettingRepository(BaseRepository):
    """설정 레포지토리"""
    
    def save(self, setting: Setting) -> int:
        """설정 저장 (UPSERT)"""
        self.db.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
        """, (setting.key, setting.value))
        return 0
    
    def find_by_id(self, id: int) -> Optional[Setting]:
        """ID로 조회 (사용 안함)"""
        return None
    
    def get(self, key: str, default: str = None) -> Optional[str]:
        """키로 값 조회"""
        data = self.db.fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
        return data['value'] if data else default
    
    def set(self, key: str, value: str):
        """키-값 설정"""
        self.save(Setting(key=key, value=value))
    
    def delete(self, id: int) -> bool:
        """삭제 (사용 안함)"""
        return False
    
    def delete_by_key(self, key: str) -> bool:
        """키로 삭제"""
        self.db.execute("DELETE FROM settings WHERE key = ?", (key,))
        return True
    
    def get_all(self) -> Dict[str, str]:
        """전체 설정 조회"""
        rows = self.db.fetch_all("SELECT key, value FROM settings")
        return {row['key']: row['value'] for row in rows}


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("Repository 테스트")
    print("=" * 60)
    
    import tempfile
    from pathlib import Path
    
    db_path = Path(tempfile.gettempdir()) / "test_repo.db"
    db = Database(str(db_path))
    
    # 1. Trade Repository 테스트
    print("\n1. TradeRepository 테스트:")
    trade_repo = TradeRepository(db)
    
    trade = Trade(
        stock_code="005930",
        stock_name="삼성전자",
        trade_type="BUY",
        quantity=10,
        price=70000,
        amount=700000,
    )
    trade_id = trade_repo.save(trade)
    print(f"   저장: ID={trade_id}")
    
    loaded = trade_repo.find_by_id(trade_id)
    print(f"   조회: {loaded.stock_name} {loaded.quantity}주")
    
    # 2. Position Repository 테스트
    print("\n2. PositionRepository 테스트:")
    pos_repo = PositionRepository(db)
    
    position = Position(
        stock_code="005930",
        stock_name="삼성전자",
        entry_price=70000,
        quantity=10,
        score=85,
    )
    pos_repo.save(position)
    
    loaded = pos_repo.find_by_code("005930")
    print(f"   조회: {loaded.stock_name} @ {loaded.entry_price:,.0f}원")
    print(f"   전체: {pos_repo.count()}개")
    
    # 3. Setting Repository 테스트
    print("\n3. SettingRepository 테스트:")
    setting_repo = SettingRepository(db)
    
    setting_repo.set("mode", "LIVE_MICRO")
    setting_repo.set("max_positions", "5")
    
    mode = setting_repo.get("mode")
    print(f"   mode: {mode}")
    
    all_settings = setting_repo.get_all()
    print(f"   전체: {all_settings}")
    
    # 정리
    db.close()
    db_path.unlink(missing_ok=True)
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
