#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Learning Store (누적 학습 저장소)
============================================================================
AI 매매 결과를 기록하고 통계를 제공하는 저장소

핵심 기능:
- 매매 결과 기록 (종목, 결정, 신뢰도, 수익률)
- 승률/평균 수익률 통계 계산
- 패턴별 성과 분석 (CCI 구간, 점수 구간 등)
- SQLite 영구 저장
- 메모리 캐싱 (빠른 조회)

사용법:
    store = LearningStore()
    
    # 결과 기록
    store.add_result(
        stock_code="005930",
        decision="BUY",
        confidence=0.85,
        profit=1.2,
        win=True
    )
    
    # 통계 조회
    stats = store.get_stats()
    print(f"승률: {stats['winrate']:.1f}%")
============================================================================
"""

import os
import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict

# 로거 설정
logger = logging.getLogger('ScalpingBot.Learning')


# =============================================================================
# 상수 정의
# =============================================================================

# 데이터베이스 경로
DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / 'db' / 'learning.db'

# 최근 N일 데이터만 통계에 사용
STATS_LOOKBACK_DAYS = 30

# 메모리 캐시 갱신 주기 (초)
CACHE_REFRESH_INTERVAL = 60


# =============================================================================
# Learning Store 클래스
# =============================================================================

class LearningStore:
    """
    AI 누적 학습 저장소
    
    매매 결과를 SQLite에 저장하고, 통계를 계산하여
    AI 프롬프트에 활용합니다.
    """
    
    def __init__(self, db_path: Path = None):
        """
        초기화
        
        Args:
            db_path: SQLite 데이터베이스 경로 (기본값: db/learning.db)
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        
        # DB 디렉토리 생성
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 스레드 안전성을 위한 락
        self._lock = threading.Lock()
        
        # 메모리 캐시
        self._cache = {
            'stats': None,
            'pattern_stats': None,
            'last_update': 0,
        }
        
        # 오늘 매매 기록 (메모리)
        self._today_results: List[Dict] = []
        
        # DB 초기화
        self._init_database()
        
        logger.info(f"LearningStore 초기화 완료 (DB: {self.db_path})")
    
    # =========================================================================
    # 데이터베이스 초기화
    # =========================================================================
    
    def _init_database(self):
        """데이터베이스 테이블 생성"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 매매 결과 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trade_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    profit REAL NOT NULL,
                    win INTEGER NOT NULL,
                    
                    -- 추가 컨텍스트 (선택)
                    rule_score REAL,
                    cci REAL,
                    market_mode TEXT,
                    
                    -- 타임스탬프
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    trade_date DATE
                )
            """)
            
            # 인덱스 생성
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_trade_date 
                ON trade_results(trade_date)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_stock_code 
                ON trade_results(stock_code)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_decision 
                ON trade_results(decision)
            """)
            
            # 일별 집계 테이블 (성능 최적화)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date DATE UNIQUE NOT NULL,
                    total_trades INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    total_profit REAL DEFAULT 0,
                    avg_confidence REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            logger.debug("DB 테이블 초기화 완료")
    
    # =========================================================================
    # 결과 기록
    # =========================================================================
    
    def add_result(
        self,
        stock_code: str,
        decision: str,
        confidence: float,
        profit: float,
        win: bool,
        rule_score: float = None,
        cci: float = None,
        market_mode: str = None,
    ):
        """
        매매 결과 기록
        
        Args:
            stock_code: 종목 코드
            decision: AI 결정 (BUY/HOLD/SELL)
            confidence: AI 신뢰도 (0.0~1.0)
            profit: 실제 수익률 (%)
            win: 승리 여부
            rule_score: 규칙 기반 점수 (선택)
            cci: CCI 값 (선택)
            market_mode: 시장 모드 (선택)
        """
        today = datetime.now().date()
        
        with self._lock:
            try:
                # DB에 저장
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO trade_results 
                        (stock_code, decision, confidence, profit, win, 
                         rule_score, cci, market_mode, trade_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        stock_code, decision, confidence, profit, 
                        1 if win else 0,
                        rule_score, cci, market_mode, today
                    ))
                    conn.commit()
                
                # 메모리에도 저장 (오늘 기록)
                self._today_results.append({
                    'stock_code': stock_code,
                    'decision': decision,
                    'confidence': confidence,
                    'profit': profit,
                    'win': win,
                    'timestamp': datetime.now(),
                })
                
                # 캐시 무효화
                self._cache['stats'] = None
                
                logger.debug(
                    f"매매 결과 기록: {stock_code} {decision} "
                    f"신뢰도:{confidence:.2f} 수익:{profit:+.2f}% {'✅' if win else '❌'}"
                )
                
            except Exception as e:
                logger.error(f"매매 결과 기록 실패: {e}")
    
    def add_result_batch(self, results: List[Dict]):
        """
        매매 결과 일괄 기록 (성능 최적화)
        
        Args:
            results: 결과 딕셔너리 리스트
        """
        today = datetime.now().date()
        
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.executemany("""
                        INSERT INTO trade_results 
                        (stock_code, decision, confidence, profit, win, 
                         rule_score, cci, market_mode, trade_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, [
                        (
                            r['stock_code'], r['decision'], r['confidence'],
                            r['profit'], 1 if r['win'] else 0,
                            r.get('rule_score'), r.get('cci'), r.get('market_mode'),
                            today
                        )
                        for r in results
                    ])
                    conn.commit()
                
                # 캐시 무효화
                self._cache['stats'] = None
                
                logger.info(f"매매 결과 {len(results)}건 일괄 기록 완료")
                
            except Exception as e:
                logger.error(f"매매 결과 일괄 기록 실패: {e}")
    
    # =========================================================================
    # 통계 조회
    # =========================================================================
    
    def get_stats(self, lookback_days: int = STATS_LOOKBACK_DAYS) -> Dict:
        """
        전체 통계 조회
        
        Args:
            lookback_days: 조회 기간 (일)
        
        Returns:
            통계 딕셔너리
            {
                'total_trades': int,
                'win_count': int,
                'loss_count': int,
                'winrate': float (%),
                'avg_profit': float (%),
                'avg_confidence': float,
                'total_profit': float (%),
            }
        """
        # 캐시 확인
        import time
        now = time.time()
        if (self._cache['stats'] is not None and 
            now - self._cache['last_update'] < CACHE_REFRESH_INTERVAL):
            return self._cache['stats']
        
        with self._lock:
            try:
                cutoff_date = (datetime.now() - timedelta(days=lookback_days)).date()
                
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    # 전체 통계
                    cursor.execute("""
                        SELECT 
                            COUNT(*) as total,
                            SUM(win) as wins,
                            AVG(profit) as avg_profit,
                            AVG(confidence) as avg_conf,
                            SUM(profit) as total_profit
                        FROM trade_results
                        WHERE trade_date >= ?
                    """, (cutoff_date,))
                    
                    row = cursor.fetchone()
                    
                    total = row[0] or 0
                    wins = row[1] or 0
                    avg_profit = row[2] or 0
                    avg_conf = row[3] or 0.5
                    total_profit = row[4] or 0
                    
                    stats = {
                        'total_trades': total,
                        'win_count': wins,
                        'loss_count': total - wins,
                        'winrate': (wins / total * 100) if total > 0 else 50,
                        'avg_profit': avg_profit,
                        'avg_confidence': avg_conf,
                        'total_profit': total_profit,
                        'lookback_days': lookback_days,
                    }
                    
                    # 캐시 업데이트
                    self._cache['stats'] = stats
                    self._cache['last_update'] = now
                    
                    return stats
                    
            except Exception as e:
                logger.error(f"통계 조회 실패: {e}")
                return {
                    'total_trades': 0,
                    'win_count': 0,
                    'loss_count': 0,
                    'winrate': 50,
                    'avg_profit': 0,
                    'avg_confidence': 0.5,
                    'total_profit': 0,
                    'lookback_days': lookback_days,
                }
    
    def get_today_stats(self) -> Dict:
        """
        오늘 매매 통계 조회
        
        Returns:
            오늘 통계 딕셔너리
        """
        with self._lock:
            if not self._today_results:
                return {
                    'total_trades': 0,
                    'win_count': 0,
                    'winrate': 0,
                    'total_profit': 0,
                }
            
            wins = sum(1 for r in self._today_results if r['win'])
            total = len(self._today_results)
            total_profit = sum(r['profit'] for r in self._today_results)
            
            return {
                'total_trades': total,
                'win_count': wins,
                'loss_count': total - wins,
                'winrate': (wins / total * 100) if total > 0 else 0,
                'total_profit': total_profit,
                'results': list(self._today_results),  # 복사본 반환
            }
    
    def get_stock_stats(self, stock_code: str) -> Dict:
        """
        특정 종목 통계 조회
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            종목별 통계
        """
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT 
                            COUNT(*) as total,
                            SUM(win) as wins,
                            AVG(profit) as avg_profit,
                            MAX(profit) as max_profit,
                            MIN(profit) as min_profit
                        FROM trade_results
                        WHERE stock_code = ?
                    """, (stock_code,))
                    
                    row = cursor.fetchone()
                    total = row[0] or 0
                    wins = row[1] or 0
                    
                    return {
                        'stock_code': stock_code,
                        'total_trades': total,
                        'win_count': wins,
                        'winrate': (wins / total * 100) if total > 0 else 50,
                        'avg_profit': row[2] or 0,
                        'max_profit': row[3] or 0,
                        'min_profit': row[4] or 0,
                    }
                    
            except Exception as e:
                logger.error(f"종목 통계 조회 실패: {e}")
                return {'stock_code': stock_code, 'total_trades': 0, 'winrate': 50}
    
    # =========================================================================
    # 패턴별 성과 분석
    # =========================================================================
    
    def get_pattern_stats(self) -> Dict:
        """
        패턴별 성과 분석
        
        CCI 구간, 점수 구간, 시장 모드별 승률을 분석합니다.
        
        Returns:
            패턴별 통계 딕셔너리
        """
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    # CCI 구간별 승률
                    cursor.execute("""
                        SELECT 
                            CASE 
                                WHEN cci < -100 THEN 'oversold'
                                WHEN cci > 100 THEN 'overbought'
                                ELSE 'neutral'
                            END as cci_zone,
                            COUNT(*) as total,
                            SUM(win) as wins,
                            AVG(profit) as avg_profit
                        FROM trade_results
                        WHERE cci IS NOT NULL
                        GROUP BY cci_zone
                    """)
                    
                    cci_stats = {}
                    for row in cursor.fetchall():
                        zone, total, wins, avg_profit = row
                        cci_stats[zone] = {
                            'total': total,
                            'wins': wins or 0,
                            'winrate': (wins / total * 100) if total > 0 else 50,
                            'avg_profit': avg_profit or 0,
                        }
                    
                    # 점수 구간별 승률
                    cursor.execute("""
                        SELECT 
                            CASE 
                                WHEN rule_score >= 80 THEN 'high'
                                WHEN rule_score >= 70 THEN 'medium'
                                ELSE 'low'
                            END as score_zone,
                            COUNT(*) as total,
                            SUM(win) as wins,
                            AVG(profit) as avg_profit
                        FROM trade_results
                        WHERE rule_score IS NOT NULL
                        GROUP BY score_zone
                    """)
                    
                    score_stats = {}
                    for row in cursor.fetchall():
                        zone, total, wins, avg_profit = row
                        score_stats[zone] = {
                            'total': total,
                            'wins': wins or 0,
                            'winrate': (wins / total * 100) if total > 0 else 50,
                            'avg_profit': avg_profit or 0,
                        }
                    
                    # 시장 모드별 승률
                    cursor.execute("""
                        SELECT 
                            market_mode,
                            COUNT(*) as total,
                            SUM(win) as wins,
                            AVG(profit) as avg_profit
                        FROM trade_results
                        WHERE market_mode IS NOT NULL
                        GROUP BY market_mode
                    """)
                    
                    market_stats = {}
                    for row in cursor.fetchall():
                        mode, total, wins, avg_profit = row
                        market_stats[mode] = {
                            'total': total,
                            'wins': wins or 0,
                            'winrate': (wins / total * 100) if total > 0 else 50,
                            'avg_profit': avg_profit or 0,
                        }
                    
                    return {
                        'cci_stats': cci_stats,
                        'score_stats': score_stats,
                        'market_stats': market_stats,
                    }
                    
            except Exception as e:
                logger.error(f"패턴 통계 조회 실패: {e}")
                return {
                    'cci_stats': {},
                    'score_stats': {},
                    'market_stats': {},
                }
    
    # =========================================================================
    # 일별 집계
    # =========================================================================
    
    def update_daily_summary(self, trade_date: datetime.date = None):
        """
        일별 집계 업데이트
        
        매일 마감 시 호출하여 일별 요약을 저장합니다.
        
        Args:
            trade_date: 집계 날짜 (기본값: 오늘)
        """
        if trade_date is None:
            trade_date = datetime.now().date()
        
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    
                    # 해당 날짜 집계
                    cursor.execute("""
                        SELECT 
                            COUNT(*) as total,
                            SUM(win) as wins,
                            SUM(profit) as total_profit,
                            AVG(confidence) as avg_conf
                        FROM trade_results
                        WHERE trade_date = ?
                    """, (trade_date,))
                    
                    row = cursor.fetchone()
                    total = row[0] or 0
                    wins = row[1] or 0
                    total_profit = row[2] or 0
                    avg_conf = row[3] or 0
                    
                    # UPSERT
                    cursor.execute("""
                        INSERT INTO daily_summary 
                        (trade_date, total_trades, win_count, loss_count, 
                         total_profit, avg_confidence)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(trade_date) DO UPDATE SET
                            total_trades = excluded.total_trades,
                            win_count = excluded.win_count,
                            loss_count = excluded.loss_count,
                            total_profit = excluded.total_profit,
                            avg_confidence = excluded.avg_confidence
                    """, (
                        trade_date, total, wins, total - wins,
                        total_profit, avg_conf
                    ))
                    
                    conn.commit()
                    logger.info(f"일별 집계 업데이트: {trade_date} ({total}건)")
                    
            except Exception as e:
                logger.error(f"일별 집계 업데이트 실패: {e}")
    
    def get_daily_summaries(self, days: int = 30) -> List[Dict]:
        """
        일별 요약 조회
        
        Args:
            days: 조회 기간 (일)
        
        Returns:
            일별 요약 리스트
        """
        with self._lock:
            try:
                cutoff_date = (datetime.now() - timedelta(days=days)).date()
                
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT *
                        FROM daily_summary
                        WHERE trade_date >= ?
                        ORDER BY trade_date DESC
                    """, (cutoff_date,))
                    
                    return [dict(row) for row in cursor.fetchall()]
                    
            except Exception as e:
                logger.error(f"일별 요약 조회 실패: {e}")
                return []
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def clear_today_results(self):
        """오늘 메모리 기록 초기화"""
        with self._lock:
            self._today_results.clear()
            logger.info("오늘 매매 기록 메모리 초기화")
    
    def export_to_csv(self, filepath: str, days: int = 30):
        """
        CSV로 내보내기
        
        Args:
            filepath: 저장 경로
            days: 내보낼 기간 (일)
        """
        import csv
        
        with self._lock:
            try:
                cutoff_date = (datetime.now() - timedelta(days=days)).date()
                
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT *
                        FROM trade_results
                        WHERE trade_date >= ?
                        ORDER BY created_at DESC
                    """, (cutoff_date,))
                    
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    
                    with open(filepath, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.writer(f)
                        writer.writerow(columns)
                        writer.writerows(rows)
                    
                    logger.info(f"CSV 내보내기 완료: {filepath} ({len(rows)}건)")
                    
            except Exception as e:
                logger.error(f"CSV 내보내기 실패: {e}")
    
    def get_record_count(self) -> int:
        """전체 기록 수 조회"""
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM trade_results")
                    return cursor.fetchone()[0]
            except Exception:
                return 0


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    # 로깅 설정
    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("Learning Store 테스트")
    print("=" * 60)
    
    # 테스트용 DB
    import tempfile
    test_db = Path(tempfile.gettempdir()) / 'test_learning.db'
    
    # 저장소 생성
    store = LearningStore(test_db)
    
    # 테스트 데이터 추가
    print("\n1. 테스트 데이터 추가...")
    test_data = [
        ("005930", "BUY", 0.85, 1.5, True, 78, -50, "NORMAL"),
        ("000660", "BUY", 0.72, -0.8, False, 72, -80, "NORMAL"),
        ("035720", "BUY", 0.90, 2.1, True, 82, -30, "NORMAL"),
        ("051910", "BUY", 0.65, -1.2, False, 68, -120, "CONSERVATIVE"),
        ("005930", "BUY", 0.78, 0.9, True, 75, -60, "NORMAL"),
    ]
    
    for code, dec, conf, profit, win, score, cci, mode in test_data:
        store.add_result(
            stock_code=code,
            decision=dec,
            confidence=conf,
            profit=profit,
            win=win,
            rule_score=score,
            cci=cci,
            market_mode=mode,
        )
    
    # 통계 조회
    print("\n2. 전체 통계:")
    stats = store.get_stats()
    for key, value in stats.items():
        if isinstance(value, float):
            print(f"   {key}: {value:.2f}")
        else:
            print(f"   {key}: {value}")
    
    # 오늘 통계
    print("\n3. 오늘 통계:")
    today_stats = store.get_today_stats()
    for key, value in today_stats.items():
        if key != 'results':
            if isinstance(value, float):
                print(f"   {key}: {value:.2f}")
            else:
                print(f"   {key}: {value}")
    
    # 종목별 통계
    print("\n4. 종목별 통계 (005930):")
    stock_stats = store.get_stock_stats("005930")
    for key, value in stock_stats.items():
        if isinstance(value, float):
            print(f"   {key}: {value:.2f}")
        else:
            print(f"   {key}: {value}")
    
    # 패턴별 통계
    print("\n5. 패턴별 통계:")
    pattern_stats = store.get_pattern_stats()
    print(f"   CCI 구간: {list(pattern_stats['cci_stats'].keys())}")
    print(f"   점수 구간: {list(pattern_stats['score_stats'].keys())}")
    print(f"   시장 모드: {list(pattern_stats['market_stats'].keys())}")
    
    # 기록 수
    print(f"\n6. 총 기록 수: {store.get_record_count()}")
    
    # 정리
    print("\n7. 테스트 DB 삭제...")
    test_db.unlink(missing_ok=True)
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
