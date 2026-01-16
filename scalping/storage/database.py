#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Database (데이터베이스)
============================================================================
SQLite 데이터베이스 연결 및 관리

핵심 기능:
- 연결 풀 관리
- 트랜잭션 지원
- 자동 테이블 생성
- 마이그레이션

사용법:
    db = Database("db/trading.db")
    
    with db.connection() as conn:
        cursor = conn.execute("SELECT * FROM trades")
============================================================================
"""

import sqlite3
import logging
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

# 로거 설정
logger = logging.getLogger('ScalpingBot.Database')


# =============================================================================
# 데이터베이스 클래스
# =============================================================================

class Database:
    """
    SQLite 데이터베이스 관리
    
    스레드 안전한 연결 관리와 자동 테이블 생성을 제공합니다.
    """
    
    def __init__(
        self,
        db_path: str = "db/trading.db",
        auto_create: bool = True,
        check_same_thread: bool = False,
    ):
        """
        초기화
        
        Args:
            db_path: 데이터베이스 파일 경로
            auto_create: 자동 테이블 생성 여부
            check_same_thread: 동일 스레드 체크 여부
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.check_same_thread = check_same_thread
        
        # 스레드별 연결 저장
        self._local = threading.local()
        self._lock = threading.Lock()
        
        # 연결 설정
        self._connection_settings = {
            'check_same_thread': check_same_thread,
            'timeout': 30.0,
            'isolation_level': None,  # 자동 커밋
        }
        
        if auto_create:
            self._create_tables()
        
        logger.info(f"Database 초기화: {self.db_path}")
    
    # =========================================================================
    # 연결 관리
    # =========================================================================
    
    def _get_connection(self) -> sqlite3.Connection:
        """스레드별 연결 가져오기"""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                str(self.db_path),
                **self._connection_settings
            )
            # Row Factory 설정 (딕셔너리 형태로 결과 반환)
            self._local.connection.row_factory = sqlite3.Row
        
        return self._local.connection
    
    @contextmanager
    def connection(self):
        """
        연결 컨텍스트 매니저
        
        사용법:
            with db.connection() as conn:
                conn.execute("...")
        """
        conn = self._get_connection()
        try:
            yield conn
        except Exception as e:
            conn.rollback()
            raise
        else:
            conn.commit()
    
    @contextmanager
    def transaction(self):
        """
        트랜잭션 컨텍스트 매니저
        
        사용법:
            with db.transaction() as conn:
                conn.execute("INSERT ...")
                conn.execute("UPDATE ...")
        """
        conn = self._get_connection()
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception as e:
            conn.execute("ROLLBACK")
            logger.error(f"트랜잭션 롤백: {e}")
            raise
    
    def close(self):
        """연결 닫기"""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
    
    # =========================================================================
    # 쿼리 실행
    # =========================================================================
    
    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        단일 쿼리 실행
        
        Args:
            query: SQL 쿼리
            params: 파라미터
        
        Returns:
            커서
        """
        conn = self._get_connection()
        return conn.execute(query, params)
    
    def executemany(self, query: str, params_list: List[tuple]) -> sqlite3.Cursor:
        """
        다중 쿼리 실행 (배치)
        
        Args:
            query: SQL 쿼리
            params_list: 파라미터 리스트
        
        Returns:
            커서
        """
        conn = self._get_connection()
        return conn.executemany(query, params_list)
    
    def fetch_one(self, query: str, params: tuple = ()) -> Optional[Dict]:
        """
        단일 행 조회
        
        Args:
            query: SQL 쿼리
            params: 파라미터
        
        Returns:
            딕셔너리 또는 None
        """
        cursor = self.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def fetch_all(self, query: str, params: tuple = ()) -> List[Dict]:
        """
        전체 행 조회
        
        Args:
            query: SQL 쿼리
            params: 파라미터
        
        Returns:
            딕셔너리 리스트
        """
        cursor = self.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # 테이블 생성
    # =========================================================================
    
    def _create_tables(self):
        """테이블 자동 생성"""
        with self.connection() as conn:
            # 매매 기록 테이블
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date DATE NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT,
                    trade_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    profit REAL DEFAULT 0,
                    profit_pct REAL DEFAULT 0,
                    rule_score REAL DEFAULT 0,
                    ai_confidence REAL DEFAULT 0,
                    sell_reason TEXT,
                    market_mode TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 일일 요약 테이블
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date DATE UNIQUE NOT NULL,
                    total_trades INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    total_profit REAL DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    avg_profit REAL DEFAULT 0,
                    avg_loss REAL DEFAULT 0,
                    max_profit REAL DEFAULT 0,
                    max_loss REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 포지션 테이블
            conn.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT UNIQUE NOT NULL,
                    stock_name TEXT,
                    entry_price REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_time TIMESTAMP NOT NULL,
                    score REAL DEFAULT 0,
                    ai_confidence REAL DEFAULT 0,
                    grade TEXT DEFAULT 'C',
                    high_price REAL DEFAULT 0,
                    target_profit REAL DEFAULT 1.0,
                    trailing_stop REAL DEFAULT 0.3,
                    stop_loss REAL DEFAULT -1.5,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # AI 학습 데이터 테이블
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_learning (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date DATE NOT NULL,
                    stock_code TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    actual_profit REAL,
                    is_win INTEGER,
                    rule_score REAL,
                    market_mode TEXT,
                    indicators TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 설정 테이블
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 인덱스 생성
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(trade_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock_code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_learning_date ON ai_learning(trade_date)")
        
        logger.info("테이블 생성 완료")
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def table_exists(self, table_name: str) -> bool:
        """테이블 존재 여부"""
        result = self.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return result is not None
    
    def get_table_info(self, table_name: str) -> List[Dict]:
        """테이블 정보 조회"""
        return self.fetch_all(f"PRAGMA table_info({table_name})")
    
    def vacuum(self):
        """데이터베이스 최적화"""
        self.execute("VACUUM")
        logger.info("데이터베이스 최적화 완료")
    
    def backup(self, backup_path: str):
        """데이터베이스 백업"""
        import shutil
        shutil.copy2(self.db_path, backup_path)
        logger.info(f"백업 완료: {backup_path}")


# =============================================================================
# 전역 인스턴스
# =============================================================================

_db_instance: Optional[Database] = None


def get_database(db_path: str = "db/trading.db") -> Database:
    """전역 Database 인스턴스"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(db_path)
    return _db_instance


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("Database 테스트")
    print("=" * 60)
    
    import tempfile
    db_path = Path(tempfile.gettempdir()) / "test_trading.db"
    
    db = Database(str(db_path))
    
    print("\n1. 테이블 확인:")
    for table in ['trades', 'daily_summary', 'positions', 'ai_learning', 'settings']:
        exists = db.table_exists(table)
        print(f"   {table}: {'✅' if exists else '❌'}")
    
    print("\n2. 데이터 삽입 테스트:")
    with db.transaction() as conn:
        conn.execute("""
            INSERT INTO trades (trade_date, stock_code, stock_name, trade_type, quantity, price, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ('2026-01-16', '005930', '삼성전자', 'BUY', 10, 70000, 700000))
    print("   ✅ 삽입 성공")
    
    print("\n3. 데이터 조회 테스트:")
    result = db.fetch_one("SELECT * FROM trades WHERE stock_code = ?", ('005930',))
    if result:
        print(f"   종목: {result['stock_name']}")
        print(f"   수량: {result['quantity']}주")
        print(f"   금액: {result['amount']:,.0f}원")
    
    print("\n4. 정리:")
    db.close()
    db_path.unlink(missing_ok=True)
    print("   ✅ 테스트 DB 삭제")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
