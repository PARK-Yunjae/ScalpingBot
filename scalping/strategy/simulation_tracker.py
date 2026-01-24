#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v3.3 - Simulation Tracker (페이퍼 트레이딩)
============================================================================
실제 매매 없이 신호만 추적하여 전략 검증용 데이터 수집

핵심 기능:
- 매수 신호 발생 시 가상 진입 기록
- 실시간 가격 추적
- 익절/손절 중 먼저 도달하는 것 기록
- 결과 DB 저장 + CSV 내보내기
- 통계 분석

사용법:
    tracker = SimulationTracker(db_path='db/simulation.db')
    
    # 신호 발생 시 가상 진입
    tracker.enter_virtual(
        stock_code="005930",
        stock_name="삼성전자", 
        entry_price=72000,
        signal_score=85,
        signal_type="breakout",
        take_profit_pct=2.5,
        stop_loss_pct=-0.8
    )
    
    # 가격 업데이트 (매 틱/분봉마다)
    results = tracker.update_prices(price_dict)
    
    # 일일 통계
    stats = tracker.get_daily_stats()
============================================================================
"""

import sqlite3
import logging
import csv
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger('ScalpingBot.SimTracker')


# =============================================================================
# 상수
# =============================================================================

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / 'db' / 'simulation.db'


class SimulationResult(Enum):
    """시뮬레이션 결과"""
    PENDING = "pending"           # 아직 미결
    TAKE_PROFIT = "take_profit"   # 익절 도달
    STOP_LOSS = "stop_loss"       # 손절 도달
    TIME_STOP = "time_stop"       # 시간 초과
    EXPIRED = "expired"           # 장 마감


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class VirtualPosition:
    """가상 포지션"""
    id: int = 0
    stock_code: str = ""
    stock_name: str = ""
    
    # 진입 정보
    entry_price: float = 0.0
    entry_time: datetime = None
    signal_score: float = 0.0
    signal_type: str = ""  # breakout, pullback, gap_play, vwap_bounce
    
    # 목표가
    take_profit_pct: float = 2.5
    stop_loss_pct: float = -0.8
    take_profit_price: float = 0.0
    stop_loss_price: float = 0.0
    
    # 추적
    current_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    current_pct: float = 0.0
    high_pct: float = 0.0
    low_pct: float = 0.0
    
    # 🆕 가격 히스토리 (타임라인 분석용)
    price_history: List[Tuple[int, float, float]] = field(default_factory=list)
    # [(경과초, 가격, 수익률%), ...]
    
    high_time_seconds: int = 0   # 고점 도달 시간 (초)
    low_time_seconds: int = 0    # 저점 도달 시간 (초)
    
    # 결과
    result: SimulationResult = SimulationResult.PENDING
    exit_price: float = 0.0
    exit_time: datetime = None
    exit_pct: float = 0.0
    hold_seconds: int = 0
    
    # 🆕 패턴 분석
    pattern: str = ""  # 패턴 유형 (early_peak, late_peak, steady_rise, steady_fall, volatile)
    
    # 메타
    date: str = ""
    created_at: datetime = None
    updated_at: datetime = None


# =============================================================================
# 시뮬레이션 트래커
# =============================================================================

class SimulationTracker:
    """
    페이퍼 트레이딩 트래커
    
    실제 매매 없이 신호의 유효성을 검증합니다.
    """
    
    def __init__(
        self,
        db_path: str = None,
        max_hold_minutes: int = 30,  # 최대 추적 시간
        max_concurrent: int = 10,     # 동시 추적 최대 수
    ):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.max_hold_minutes = max_hold_minutes
        self.max_concurrent = max_concurrent
        
        # 활성 포지션 (메모리)
        self._positions: Dict[str, VirtualPosition] = {}
        
        # DB 초기화
        self._init_db()
        
        # 오늘 날짜
        self._today = datetime.now().strftime('%Y-%m-%d')
        
        # 통계
        self._stats = {
            'total_signals': 0,
            'take_profit': 0,
            'stop_loss': 0,
            'time_stop': 0,
            'pending': 0,
        }
        
        logger.info(f"SimulationTracker 초기화 (DB: {self.db_path})")
    
    # =========================================================================
    # DB 관리
    # =========================================================================
    
    def _init_db(self):
        """DB 테이블 생성"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS virtual_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    
                    -- 진입 정보
                    entry_price REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    signal_score REAL,
                    signal_type TEXT,
                    
                    -- 목표가
                    take_profit_pct REAL,
                    stop_loss_pct REAL,
                    take_profit_price REAL,
                    stop_loss_price REAL,
                    
                    -- 추적
                    high_price REAL,
                    low_price REAL,
                    high_pct REAL,
                    low_pct REAL,
                    
                    -- 🆕 타임라인 분석
                    price_history TEXT,
                    high_time_seconds INTEGER,
                    low_time_seconds INTEGER,
                    pattern TEXT,
                    
                    -- 결과
                    result TEXT DEFAULT 'pending',
                    exit_price REAL,
                    exit_time TEXT,
                    exit_pct REAL,
                    hold_seconds INTEGER,
                    
                    -- 메타
                    date TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            ''')
            
            # 인덱스
            conn.execute('CREATE INDEX IF NOT EXISTS idx_date ON virtual_positions(date)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_result ON virtual_positions(result)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_signal_type ON virtual_positions(signal_type)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_score ON virtual_positions(signal_score)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pattern ON virtual_positions(pattern)')
            
            conn.commit()
    
    def _save_position(self, pos: VirtualPosition) -> int:
        """포지션 DB 저장"""
        import json
        
        with sqlite3.connect(self.db_path) as conn:
            # 가격 히스토리를 JSON으로 직렬화
            price_history_json = json.dumps(pos.price_history) if pos.price_history else '[]'
            
            if pos.id == 0:
                # INSERT
                cursor = conn.execute('''
                    INSERT INTO virtual_positions (
                        stock_code, stock_name, entry_price, entry_time,
                        signal_score, signal_type, take_profit_pct, stop_loss_pct,
                        take_profit_price, stop_loss_price, high_price, low_price,
                        high_pct, low_pct, price_history, high_time_seconds, low_time_seconds,
                        pattern, result, exit_price, exit_time, exit_pct,
                        hold_seconds, date, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    pos.stock_code, pos.stock_name, pos.entry_price,
                    pos.entry_time.isoformat() if pos.entry_time else None,
                    pos.signal_score, pos.signal_type, pos.take_profit_pct, pos.stop_loss_pct,
                    pos.take_profit_price, pos.stop_loss_price, pos.high_price, pos.low_price,
                    pos.high_pct, pos.low_pct, price_history_json, pos.high_time_seconds, pos.low_time_seconds,
                    pos.pattern, pos.result.value,
                    pos.exit_price, pos.exit_time.isoformat() if pos.exit_time else None,
                    pos.exit_pct, pos.hold_seconds, pos.date,
                    datetime.now().isoformat(), datetime.now().isoformat()
                ))
                pos.id = cursor.lastrowid
            else:
                # UPDATE
                conn.execute('''
                    UPDATE virtual_positions SET
                        high_price = ?, low_price = ?, high_pct = ?, low_pct = ?,
                        price_history = ?, high_time_seconds = ?, low_time_seconds = ?,
                        pattern = ?, result = ?, exit_price = ?, exit_time = ?, exit_pct = ?,
                        hold_seconds = ?, updated_at = ?
                    WHERE id = ?
                ''', (
                    pos.high_price, pos.low_price, pos.high_pct, pos.low_pct,
                    price_history_json, pos.high_time_seconds, pos.low_time_seconds,
                    pos.pattern, pos.result.value, pos.exit_price,
                    pos.exit_time.isoformat() if pos.exit_time else None,
                    pos.exit_pct, pos.hold_seconds, datetime.now().isoformat(),
                    pos.id
                ))
            conn.commit()
        return pos.id
    
    # =========================================================================
    # 가상 진입/청산
    # =========================================================================
    
    def enter_virtual(
        self,
        stock_code: str,
        stock_name: str,
        entry_price: float,
        signal_score: float,
        signal_type: str,
        take_profit_pct: float = 2.5,
        stop_loss_pct: float = -0.8,
    ) -> Optional[VirtualPosition]:
        """
        가상 진입 (매수 신호 기록)
        
        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            entry_price: 진입가 (신호 발생 시점 가격)
            signal_score: 신호 점수
            signal_type: 신호 타입 (breakout, pullback 등)
            take_profit_pct: 익절 목표 (%)
            stop_loss_pct: 손절선 (%, 음수)
        
        Returns:
            VirtualPosition or None (동시 추적 초과 시)
        """
        # 동시 추적 제한
        if len(self._positions) >= self.max_concurrent:
            logger.warning(f"동시 추적 한도 초과 ({self.max_concurrent}개)")
            return None
        
        # 이미 추적 중인 종목
        if stock_code in self._positions:
            logger.debug(f"이미 추적 중: {stock_name}")
            return None
        
        now = datetime.now()
        
        # 목표가 계산
        take_profit_price = entry_price * (1 + take_profit_pct / 100)
        stop_loss_price = entry_price * (1 + stop_loss_pct / 100)
        
        pos = VirtualPosition(
            stock_code=stock_code,
            stock_name=stock_name,
            entry_price=entry_price,
            entry_time=now,
            signal_score=signal_score,
            signal_type=signal_type,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
            current_price=entry_price,
            high_price=entry_price,
            low_price=entry_price,
            date=now.strftime('%Y-%m-%d'),
            created_at=now,
            updated_at=now,
        )
        
        # DB 저장
        self._save_position(pos)
        
        # 메모리 등록
        self._positions[stock_code] = pos
        self._stats['total_signals'] += 1
        self._stats['pending'] += 1
        
        logger.info(
            f"📝 가상진입: {stock_name}({stock_code}) "
            f"@ {entry_price:,.0f}원 | 점수:{signal_score:.0f} | {signal_type} | "
            f"익절:{take_profit_price:,.0f} 손절:{stop_loss_price:,.0f}"
        )
        
        return pos
    
    def update_prices(self, price_dict: Dict[str, float]) -> List[VirtualPosition]:
        """
        가격 업데이트 및 결과 확인
        
        Args:
            price_dict: {종목코드: 현재가} 딕셔너리
        
        Returns:
            청산된 포지션 리스트
        """
        closed = []
        now = datetime.now()
        
        for code, pos in list(self._positions.items()):
            if code not in price_dict:
                continue
            
            current_price = price_dict[code]
            pos.current_price = current_price
            pos.updated_at = now
            
            # 보유 시간
            hold_seconds = int((now - pos.entry_time).total_seconds())
            pos.hold_seconds = hold_seconds
            
            # 수익률 계산
            pos.current_pct = (current_price / pos.entry_price - 1) * 100
            
            # 🆕 가격 히스토리 기록 (10초마다)
            if not pos.price_history or (hold_seconds - pos.price_history[-1][0]) >= 10:
                pos.price_history.append((hold_seconds, current_price, round(pos.current_pct, 2)))
            
            # 고가/저가 갱신 및 시간 기록
            if current_price > pos.high_price:
                pos.high_price = current_price
                pos.high_time_seconds = hold_seconds
            if current_price < pos.low_price:
                pos.low_price = current_price
                pos.low_time_seconds = hold_seconds
            
            pos.high_pct = (pos.high_price / pos.entry_price - 1) * 100
            pos.low_pct = (pos.low_price / pos.entry_price - 1) * 100
            
            # 결과 판정
            result = None
            exit_pct = 0
            
            # 1. 익절 체크
            if current_price >= pos.take_profit_price:
                result = SimulationResult.TAKE_PROFIT
                exit_pct = pos.take_profit_pct
                
            # 2. 손절 체크
            elif current_price <= pos.stop_loss_price:
                result = SimulationResult.STOP_LOSS
                exit_pct = pos.stop_loss_pct
                
            # 3. 시간 초과 체크
            elif hold_seconds >= self.max_hold_minutes * 60:
                result = SimulationResult.TIME_STOP
                exit_pct = pos.current_pct
            
            # 결과 기록
            if result:
                pos.result = result
                pos.exit_price = current_price
                pos.exit_time = now
                pos.exit_pct = exit_pct
                
                # 🆕 패턴 분석
                pos.pattern = self._analyze_pattern(pos)
                
                # DB 저장
                self._save_position(pos)
                
                # 메모리에서 제거
                del self._positions[code]
                closed.append(pos)
                
                # 통계 업데이트
                self._stats['pending'] -= 1
                if result == SimulationResult.TAKE_PROFIT:
                    self._stats['take_profit'] += 1
                elif result == SimulationResult.STOP_LOSS:
                    self._stats['stop_loss'] += 1
                elif result == SimulationResult.TIME_STOP:
                    self._stats['time_stop'] += 1
                
                emoji = "✅" if result == SimulationResult.TAKE_PROFIT else "❌"
                logger.info(
                    f"{emoji} 가상청산: {pos.stock_name} | "
                    f"{result.value} | {exit_pct:+.2f}% | "
                    f"{hold_seconds//60}분{hold_seconds%60}초 | "
                    f"고점:{pos.high_pct:+.2f}%({pos.high_time_seconds}초) | "
                    f"패턴:{pos.pattern}"
                )
        
        return closed
    
    def _analyze_pattern(self, pos: VirtualPosition) -> str:
        """
        🆕 가격 패턴 분석
        
        패턴 유형:
        - early_peak: 초반 고점 후 하락 (2분 이내 고점)
        - late_peak: 후반 상승 (고점이 후반부)
        - steady_rise: 꾸준한 상승
        - steady_fall: 꾸준한 하락
        - volatile: 등락 반복
        - quick_win: 빠른 익절 (1분 이내)
        - quick_loss: 빠른 손절 (1분 이내)
        """
        hold_seconds = pos.hold_seconds
        high_time = pos.high_time_seconds
        low_time = pos.low_time_seconds
        
        # 빠른 결과
        if hold_seconds <= 60:
            if pos.result == SimulationResult.TAKE_PROFIT:
                return "quick_win"
            elif pos.result == SimulationResult.STOP_LOSS:
                return "quick_loss"
        
        # 고점 시점 분석
        if hold_seconds > 0:
            high_ratio = high_time / hold_seconds  # 고점이 전체 보유 시간의 어디에 있나
            low_ratio = low_time / hold_seconds
            
            # 초반 고점 후 하락 (고점이 앞 30% 구간)
            if high_ratio < 0.3 and pos.result == SimulationResult.STOP_LOSS:
                return "early_peak_then_fall"
            
            # 초반 고점인데 시간초과 (익절 못함)
            if high_ratio < 0.3 and pos.result == SimulationResult.TIME_STOP:
                return "early_peak_missed"
            
            # 후반 상승 (고점이 뒤 30% 구간)
            if high_ratio > 0.7:
                if pos.result == SimulationResult.TAKE_PROFIT:
                    return "late_rally_win"
                else:
                    return "late_rally"
            
            # 초반 급락 (저점이 앞 30% 구간)
            if low_ratio < 0.3 and pos.result == SimulationResult.STOP_LOSS:
                return "quick_drop"
        
        # 히스토리 기반 분석
        if len(pos.price_history) >= 3:
            pcts = [h[2] for h in pos.price_history]  # 수익률 리스트
            
            # 방향 전환 횟수 계산
            direction_changes = 0
            for i in range(1, len(pcts)):
                if (pcts[i] > pcts[i-1]) != (pcts[i-1] > pcts[i-2] if i >= 2 else True):
                    direction_changes += 1
            
            # 변동성 판단 (전환 많으면 volatile)
            if direction_changes >= len(pcts) * 0.4:
                return "volatile"
            
            # 꾸준한 상승/하락
            if all(pcts[i] >= pcts[i-1] for i in range(1, len(pcts))):
                return "steady_rise"
            if all(pcts[i] <= pcts[i-1] for i in range(1, len(pcts))):
                return "steady_fall"
        
        return "normal"
    
    def close_all(self, reason: SimulationResult = SimulationResult.EXPIRED):
        """모든 포지션 강제 청산 (장 마감 등)"""
        now = datetime.now()
        
        for code, pos in list(self._positions.items()):
            pos.result = reason
            pos.exit_price = pos.current_price
            pos.exit_time = now
            pos.exit_pct = pos.current_pct
            pos.hold_seconds = int((now - pos.entry_time).total_seconds())
            
            self._save_position(pos)
            
            self._stats['pending'] -= 1
            
            logger.info(f"📤 강제청산: {pos.stock_name} | {pos.exit_pct:+.2f}%")
        
        self._positions.clear()
    
    # =========================================================================
    # 조회 및 통계
    # =========================================================================
    
    def get_active_positions(self) -> List[VirtualPosition]:
        """현재 추적 중인 포지션"""
        return list(self._positions.values())
    
    def get_stats(self) -> Dict[str, Any]:
        """실시간 통계"""
        return self._stats.copy()
    
    def get_daily_stats(self, date: str = None) -> Dict[str, Any]:
        """일일 통계 조회"""
        date = date or datetime.now().strftime('%Y-%m-%d')
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # 전체 통계
            row = conn.execute('''
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN result = 'take_profit' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN result = 'stop_loss' THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN result = 'time_stop' THEN 1 ELSE 0 END) as time_stops,
                    AVG(exit_pct) as avg_pct,
                    AVG(CASE WHEN result = 'take_profit' THEN exit_pct END) as avg_win,
                    AVG(CASE WHEN result = 'stop_loss' THEN exit_pct END) as avg_loss,
                    AVG(hold_seconds) as avg_hold,
                    AVG(high_pct) as avg_high,
                    AVG(low_pct) as avg_low,
                    AVG(high_time_seconds) as avg_high_time
                FROM virtual_positions
                WHERE date = ? AND result != 'pending'
            ''', (date,)).fetchone()
            
            total = row['total'] or 0
            wins = row['wins'] or 0
            losses = row['losses'] or 0
            
            # 점수대별 통계
            score_stats = conn.execute('''
                SELECT 
                    CASE 
                        WHEN signal_score >= 90 THEN '90+'
                        WHEN signal_score >= 85 THEN '85-89'
                        WHEN signal_score >= 80 THEN '80-84'
                        WHEN signal_score >= 75 THEN '75-79'
                        ELSE '~74'
                    END as score_range,
                    COUNT(*) as count,
                    SUM(CASE WHEN result = 'take_profit' THEN 1 ELSE 0 END) as wins,
                    AVG(exit_pct) as avg_pct,
                    AVG(high_pct) as avg_high,
                    AVG(high_time_seconds) as avg_high_time
                FROM virtual_positions
                WHERE date = ? AND result != 'pending'
                GROUP BY score_range
                ORDER BY score_range DESC
            ''', (date,)).fetchall()
            
            # 전략별 통계
            type_stats = conn.execute('''
                SELECT 
                    signal_type,
                    COUNT(*) as count,
                    SUM(CASE WHEN result = 'take_profit' THEN 1 ELSE 0 END) as wins,
                    AVG(exit_pct) as avg_pct
                FROM virtual_positions
                WHERE date = ? AND result != 'pending'
                GROUP BY signal_type
            ''', (date,)).fetchall()
            
            # 🆕 패턴별 통계
            pattern_stats = conn.execute('''
                SELECT 
                    pattern,
                    COUNT(*) as count,
                    SUM(CASE WHEN result = 'take_profit' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN result = 'stop_loss' THEN 1 ELSE 0 END) as losses,
                    AVG(exit_pct) as avg_pct,
                    AVG(high_pct) as avg_high
                FROM virtual_positions
                WHERE date = ? AND result != 'pending' AND pattern IS NOT NULL AND pattern != ''
                GROUP BY pattern
                ORDER BY count DESC
            ''', (date,)).fetchall()
            
            # 🆕 손절 케이스 상세 (고점 분석)
            loss_analysis = conn.execute('''
                SELECT 
                    stock_name, signal_score, signal_type,
                    high_pct, high_time_seconds, hold_seconds, pattern
                FROM virtual_positions
                WHERE date = ? AND result = 'stop_loss'
                ORDER BY high_pct DESC
                LIMIT 10
            ''', (date,)).fetchall()
        
        return {
            'date': date,
            'total': total,
            'wins': wins,
            'losses': losses,
            'time_stops': row['time_stops'] or 0,
            'win_rate': (wins / total * 100) if total > 0 else 0,
            'avg_pct': row['avg_pct'] or 0,
            'avg_win': row['avg_win'] or 0,
            'avg_loss': row['avg_loss'] or 0,
            'avg_hold_minutes': (row['avg_hold'] or 0) / 60,
            'avg_high_pct': row['avg_high'] or 0,
            'avg_low_pct': row['avg_low'] or 0,
            'avg_high_time_seconds': row['avg_high_time'] or 0,
            'score_breakdown': [dict(r) for r in score_stats],
            'type_breakdown': [dict(r) for r in type_stats],
            'pattern_breakdown': [dict(r) for r in pattern_stats],
            'loss_analysis': [dict(r) for r in loss_analysis],
        }
    
    def get_period_stats(self, days: int = 30) -> Dict[str, Any]:
        """기간 통계 조회"""
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            row = conn.execute('''
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN result = 'take_profit' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN result = 'stop_loss' THEN 1 ELSE 0 END) as losses,
                    AVG(exit_pct) as avg_pct,
                    SUM(exit_pct) as total_pct
                FROM virtual_positions
                WHERE date >= ? AND result != 'pending'
            ''', (start_date,)).fetchone()
            
            # 점수대별 상세
            score_detail = conn.execute('''
                SELECT 
                    CASE 
                        WHEN signal_score >= 90 THEN '90+'
                        WHEN signal_score >= 85 THEN '85-89'
                        WHEN signal_score >= 80 THEN '80-84'
                        WHEN signal_score >= 75 THEN '75-79'
                        ELSE '~74'
                    END as score_range,
                    COUNT(*) as count,
                    SUM(CASE WHEN result = 'take_profit' THEN 1 ELSE 0 END) as wins,
                    ROUND(AVG(exit_pct), 2) as avg_pct,
                    ROUND(SUM(exit_pct), 2) as total_pct
                FROM virtual_positions
                WHERE date >= ? AND result != 'pending'
                GROUP BY score_range
                ORDER BY score_range DESC
            ''', (start_date,)).fetchall()
        
        total = row['total'] or 0
        wins = row['wins'] or 0
        
        return {
            'period_days': days,
            'start_date': start_date,
            'total': total,
            'wins': wins,
            'losses': row['losses'] or 0,
            'win_rate': (wins / total * 100) if total > 0 else 0,
            'avg_pct': row['avg_pct'] or 0,
            'total_pct': row['total_pct'] or 0,
            'score_breakdown': [dict(r) for r in score_detail],
        }
    
    def export_csv(self, filepath: str = None, days: int = 30):
        """CSV 내보내기"""
        filepath = filepath or f"simulation_results_{datetime.now().strftime('%Y%m%d')}.csv"
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('''
                SELECT * FROM virtual_positions
                WHERE date >= ?
                ORDER BY entry_time DESC
            ''', (start_date,)).fetchall()
        
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows([dict(r) for r in rows])
        
        logger.info(f"CSV 내보내기 완료: {filepath} ({len(rows)}건)")
        return filepath
    
    def get_trade_timeline(self, trade_id: int = None, stock_code: str = None, date: str = None) -> Optional[Dict]:
        """
        🆕 개별 거래의 타임라인 상세 조회
        
        Args:
            trade_id: 거래 ID (우선)
            stock_code: 종목 코드 (오늘 해당 종목)
            date: 날짜 (기본: 오늘)
        """
        import json
        date = date or datetime.now().strftime('%Y-%m-%d')
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if trade_id:
                row = conn.execute('SELECT * FROM virtual_positions WHERE id = ?', (trade_id,)).fetchone()
            elif stock_code:
                row = conn.execute('''
                    SELECT * FROM virtual_positions 
                    WHERE stock_code = ? AND date = ?
                    ORDER BY entry_time DESC LIMIT 1
                ''', (stock_code, date)).fetchone()
            else:
                return None
            
            if not row:
                return None
            
            result = dict(row)
            
            # 가격 히스토리 파싱
            try:
                result['price_history'] = json.loads(row['price_history']) if row['price_history'] else []
            except:
                result['price_history'] = []
            
            return result
    
    def print_trade_timeline(self, trade_id: int = None, stock_code: str = None, date: str = None):
        """
        🆕 개별 거래의 타임라인 상세 출력
        
        예시:
        [삼성전자 005930] 점수: 82 (breakout)
        진입: 72,000원 @ 09:15:30 | 목표: 73,800원(+2.5%) 손절: 71,424원(-0.8%)
        
        타임라인:
          +0:10  72,150원  +0.21%  
          +0:20  72,300원  +0.42%  ← 고점
          +0:30  72,100원  +0.14%  
          +1:00  71,800원  -0.28%  
          +1:30  71,400원  -0.83%  ← 손절
        
        결과: 손절 (-0.8%) | 보유 1분 30초 | 패턴: early_peak_then_fall
        """
        trade = self.get_trade_timeline(trade_id, stock_code, date)
        
        if not trade:
            print("거래를 찾을 수 없습니다.")
            return
        
        print("\n" + "=" * 70)
        print(f"[{trade['stock_name']} {trade['stock_code']}] 점수: {trade['signal_score']:.0f} ({trade['signal_type']})")
        print(f"진입: {trade['entry_price']:,.0f}원 @ {trade['entry_time']}")
        print(f"목표: {trade['take_profit_price']:,.0f}원 ({trade['take_profit_pct']:+.1f}%) | "
              f"손절: {trade['stop_loss_price']:,.0f}원 ({trade['stop_loss_pct']:.1f}%)")
        print("=" * 70)
        
        # 타임라인 출력
        history = trade.get('price_history', [])
        if history:
            print("\n타임라인:")
            high_time = trade.get('high_time_seconds', 0)
            low_time = trade.get('low_time_seconds', 0)
            
            for seconds, price, pct in history:
                minutes = seconds // 60
                secs = seconds % 60
                
                # 마커
                marker = ""
                if abs(seconds - high_time) < 15:  # 고점 근처
                    marker = " ← 고점"
                elif abs(seconds - low_time) < 15:  # 저점 근처
                    marker = " ← 저점"
                
                # 색상 표시 (터미널에서)
                if pct >= trade['take_profit_pct']:
                    marker += " ✅"
                elif pct <= trade['stop_loss_pct']:
                    marker += " ❌"
                
                print(f"  +{minutes:2d}:{secs:02d}  {price:>10,.0f}원  {pct:>+6.2f}%{marker}")
        else:
            print("\n(타임라인 데이터 없음)")
        
        # 결과
        result_emoji = {"take_profit": "✅ 익절", "stop_loss": "❌ 손절", "time_stop": "⏰ 시간초과", "expired": "📤 강제청산"}
        result_str = result_emoji.get(trade['result'], trade['result'])
        
        hold_min = (trade['hold_seconds'] or 0) // 60
        hold_sec = (trade['hold_seconds'] or 0) % 60
        
        print(f"\n결과: {result_str} ({trade['exit_pct']:+.2f}%) | "
              f"보유 {hold_min}분 {hold_sec}초 | "
              f"패턴: {self._get_pattern_description(trade.get('pattern', 'unknown'))}")
        print(f"고점: {trade['high_pct']:+.2f}% ({trade.get('high_time_seconds', 0)}초 후) | "
              f"저점: {trade['low_pct']:+.2f}%")
        print("=" * 70)
    
    def print_loss_timelines(self, date: str = None, limit: int = 5):
        """
        🆕 손절 케이스들의 타임라인 일괄 출력
        
        손절된 거래들이 어떤 흐름이었는지 한눈에 파악
        """
        date = date or datetime.now().strftime('%Y-%m-%d')
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('''
                SELECT id FROM virtual_positions
                WHERE date = ? AND result = 'stop_loss'
                ORDER BY high_pct DESC
                LIMIT ?
            ''', (date, limit)).fetchall()
        
        if not rows:
            print(f"{date} 손절 거래 없음")
            return
        
        print(f"\n{'='*70}")
        print(f"📉 손절 케이스 타임라인 분석 ({date}) - 상위 {limit}개")
        print(f"{'='*70}")
        
        for row in rows:
            self.print_trade_timeline(trade_id=row['id'])
    
    def print_daily_report(self, date: str = None):
        """일일 리포트 출력"""
        stats = self.get_daily_stats(date)
        
        print("\n" + "=" * 70)
        print(f"📊 시뮬레이션 일일 리포트 ({stats['date']})")
        print("=" * 70)
        
        print(f"\n[전체 성과]")
        print(f"  총 신호: {stats['total']}회")
        print(f"  익절: {stats['wins']}회 | 손절: {stats['losses']}회 | 시간초과: {stats['time_stops']}회")
        print(f"  승률: {stats['win_rate']:.1f}%")
        print(f"  평균 수익률: {stats['avg_pct']:+.2f}%")
        print(f"  평균 보유: {stats['avg_hold_minutes']:.1f}분")
        print(f"  평균 고점: {stats['avg_high_pct']:+.2f}% (평균 {stats['avg_high_time_seconds']:.0f}초 후)")
        print(f"  평균 저점: {stats['avg_low_pct']:+.2f}%")
        
        if stats['score_breakdown']:
            print(f"\n[점수대별 성과]")
            print(f"  {'점수':>8} | {'횟수':>4} | {'익절':>4} | {'승률':>6} | {'평균':>7} | {'고점':>6} | {'고점시간':>7}")
            print(f"  {'-'*8}-+-{'-'*4}-+-{'-'*4}-+-{'-'*6}-+-{'-'*7}-+-{'-'*6}-+-{'-'*7}")
            for s in stats['score_breakdown']:
                win_rate = (s['wins'] / s['count'] * 100) if s['count'] > 0 else 0
                avg_high_time = s.get('avg_high_time', 0) or 0
                print(f"  {s['score_range']:>8} | {s['count']:>4} | {s['wins']:>4} | {win_rate:>5.1f}% | {s['avg_pct']:>+6.2f}% | {s.get('avg_high', 0) or 0:>+5.2f}% | {avg_high_time:>5.0f}초")
        
        if stats['type_breakdown']:
            print(f"\n[전략별 성과]")
            for t in stats['type_breakdown']:
                win_rate = (t['wins'] / t['count'] * 100) if t['count'] > 0 else 0
                print(f"  {t['signal_type']}: {t['count']}회, 승률 {win_rate:.1f}%, 평균 {t['avg_pct']:+.2f}%")
        
        # 🆕 패턴별 분석
        if stats.get('pattern_breakdown'):
            print(f"\n[패턴별 분석] - 어떻게 끝났나?")
            print(f"  {'패턴':<20} | {'횟수':>4} | {'익절':>4} | {'손절':>4} | {'평균고점':>7}")
            print(f"  {'-'*20}-+-{'-'*4}-+-{'-'*4}-+-{'-'*4}-+-{'-'*7}")
            for p in stats['pattern_breakdown']:
                pattern_name = self._get_pattern_description(p['pattern'])
                avg_high = p.get('avg_high', 0) or 0
                print(f"  {pattern_name:<20} | {p['count']:>4} | {p['wins']:>4} | {p['losses']:>4} | {avg_high:>+6.2f}%")
        
        # 🆕 손절 케이스 분석 (고점 대비)
        if stats.get('loss_analysis'):
            print(f"\n[손절 케이스 분석] - 고점까지 갔는데 왜 손절?")
            print(f"  {'종목':<12} | {'점수':>4} | {'전략':<10} | {'고점':>6} | {'고점시간':>7} | {'보유':>6} | {'패턴':<15}")
            print(f"  {'-'*12}-+-{'-'*4}-+-{'-'*10}-+-{'-'*6}-+-{'-'*7}-+-{'-'*6}-+-{'-'*15}")
            for loss in stats['loss_analysis']:
                hold_min = (loss['hold_seconds'] or 0) // 60
                hold_sec = (loss['hold_seconds'] or 0) % 60
                high_time = loss.get('high_time_seconds', 0) or 0
                pattern_short = (loss.get('pattern') or 'unknown')[:15]
                print(f"  {loss['stock_name'][:12]:<12} | {loss['signal_score']:>4.0f} | {loss['signal_type']:<10} | {loss['high_pct']:>+5.2f}% | {high_time:>5.0f}초 | {hold_min:>2}:{hold_sec:02d} | {pattern_short:<15}")
            
            # 인사이트
            high_pcts = [l['high_pct'] for l in stats['loss_analysis'] if l['high_pct']]
            if high_pcts:
                avg_missed = sum(high_pcts) / len(high_pcts)
                if avg_missed > 0.5:
                    print(f"\n  💡 인사이트: 손절 전 평균 {avg_missed:+.2f}%까지 상승했다가 하락")
                    print(f"     → 트레일링 스탑 또는 빠른 부분 익절 고려 필요")
        
        print("\n" + "=" * 70)
    
    def _get_pattern_description(self, pattern: str) -> str:
        """패턴 설명"""
        descriptions = {
            'early_peak_then_fall': '초반고점→하락',
            'early_peak_missed': '초반고점(익절못함)',
            'late_rally_win': '후반상승→익절',
            'late_rally': '후반상승',
            'quick_drop': '급락',
            'quick_win': '빠른익절',
            'quick_loss': '빠른손절',
            'steady_rise': '꾸준한상승',
            'steady_fall': '꾸준한하락',
            'volatile': '등락반복',
            'normal': '일반',
        }
        return descriptions.get(pattern, pattern)


# =============================================================================
# 테스트
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("SimulationTracker 테스트")
    print("=" * 60)
    
    # 트래커 생성
    tracker = SimulationTracker(db_path='db/simulation_test.db')
    
    # 가상 진입
    tracker.enter_virtual(
        stock_code="005930",
        stock_name="삼성전자",
        entry_price=72000,
        signal_score=85,
        signal_type="breakout",
        take_profit_pct=2.5,
        stop_loss_pct=-0.8
    )
    
    tracker.enter_virtual(
        stock_code="035720",
        stock_name="카카오",
        entry_price=50000,
        signal_score=78,
        signal_type="pullback",
        take_profit_pct=2.5,
        stop_loss_pct=-0.8
    )
    
    # 가격 업데이트 시뮬레이션
    import time
    
    prices = {
        "005930": 72000,
        "035720": 50000,
    }
    
    # 삼성 익절 시나리오
    for i in range(10):
        prices["005930"] += 200  # 점점 상승
        prices["035720"] -= 100  # 점점 하락
        
        results = tracker.update_prices(prices)
        
        if results:
            for r in results:
                print(f"결과: {r.stock_name} - {r.result.value}")
        
        time.sleep(0.1)
    
    # 통계 출력
    print("\n현재 통계:", tracker.get_stats())
    
    # 일일 리포트
    tracker.print_daily_report()
    
    print("\n테스트 완료")
