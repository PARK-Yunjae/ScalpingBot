#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Position Manager (포지션 관리자)
============================================================================
보유 포지션을 추적하고 손절/익절/트레일링 스탑을 관리

핵심 기능:
- 포지션 등록/수정/삭제
- 고점 갱신 추적 (트레일링 스탑용)
- 손절/익절/트레일링 판단
- 등급별 익절 목표 관리
- SQLite 영구 저장

익절 등급:
- S등급 (90점+): 익절 1.5%, 트레일링 0.5%
- A등급 (80점+): 익절 1.2%, 트레일링 0.4%
- B등급 (70점+): 익절 1.0%, 트레일링 0.3%
- C등급 (60점+): 익절 0.8%, 트레일링 0.3%

사용법:
    pm = PositionManager()
    
    # 포지션 등록
    pm.add_position("005930", "삼성전자", 70000, 10, score=85)
    
    # 가격 업데이트 및 판단
    action = pm.update_price("005930", 71200)
    if action['action'] == 'SELL':
        print(f"매도 신호: {action['reason']}")
============================================================================
"""

import sqlite3
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

# 로거 설정
logger = logging.getLogger('ScalpingBot.Position')


# =============================================================================
# 상수 및 열거형
# =============================================================================

# 데이터베이스 경로
DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / 'db' / 'positions.db'

# ============================================================================
# 스캘핑용 설정 (v3.0)
# ============================================================================
# 거래비용: 0.54% (슬리피지 0.33% + 수수료 0.03% + 세금 0.18%)
# 손절: -0.7% → 순손 -1.24%
# 익절: +1.5% → 순익 +0.96%
# 필요 승률: 56% 이상
# ============================================================================

# 익절 목표 설정 (스캘핑용 - 등급 단순화)
PROFIT_TARGETS = {
    'S': {'min_score': 75, 'target_profit': 2.0, 'trailing_stop': 0.4},
    'A': {'min_score': 65, 'target_profit': 1.5, 'trailing_stop': 0.4},
    'B': {'min_score': 55, 'target_profit': 1.5, 'trailing_stop': 0.4},
    'C': {'min_score': 0,  'target_profit': 1.5, 'trailing_stop': 0.4},
}

# 손절 설정 (스캘핑용)
DEFAULT_STOP_LOSS = -0.7  # -0.7% (순손 -1.24%)

# 시간 손절 설정
DEFAULT_TIME_STOP_MINUTES = 3     # N분 내 수익 없으면 청산
DEFAULT_TIME_STOP_THRESHOLD = 0.3  # 최소 기대 수익률 (%)
DEFAULT_MAX_HOLD_MINUTES = 10      # 최대 보유 시간 (분)


class SellReason(Enum):
    """매도 사유"""
    TAKE_PROFIT = "익절"
    STOP_LOSS = "손절"
    TRAILING_STOP = "트레일링"
    TIME_STOP = "시간손절"         # 🆕 시간 손절
    TIME_LIMIT = "시간청산"        # 장 마감
    VWAP_BREAK = "VWAP이탈"       # 🆕 VWAP 이탈
    LUNCH_BREAK = "점심청산"
    EMERGENCY = "비상청산"
    MANUAL = "수동청산"


class PositionGrade(Enum):
    """포지션 등급"""
    S = "S"
    A = "A"
    B = "B"
    C = "C"


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class PositionInfo:
    """포지션 정보"""
    stock_code: str                    # 종목 코드
    stock_name: str                    # 종목명
    entry_price: float                 # 진입가
    quantity: int                      # 수량
    entry_time: datetime               # 진입 시간
    
    # 점수 관련
    score: float = 0.0                 # 규칙 점수
    ai_confidence: float = 0.0         # AI 신뢰도
    grade: str = "C"                   # 등급 (S/A/B/C)
    
    # 가격 추적
    current_price: float = 0.0         # 현재가
    high_price: float = 0.0            # 최고가 (트레일링용)
    
    # 목표/손절
    target_profit: float = 1.0         # 익절 목표 (%)
    trailing_stop: float = 0.3         # 트레일링 스탑 (%)
    stop_loss: float = -1.5            # 손절선 (%)
    
    # 상태
    profit_pct: float = 0.0            # 현재 수익률 (%)
    high_profit_pct: float = 0.0       # 최고 수익률 (%)
    
    # 🆕 지표
    entry_cci: float = 0.0             # 매수 시점 CCI
    
    # 메타
    id: int = 0
    updated_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            'id': self.id,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'entry_price': self.entry_price,
            'quantity': self.quantity,
            'entry_time': self.entry_time.isoformat() if self.entry_time else None,
            'score': self.score,
            'ai_confidence': self.ai_confidence,
            'grade': self.grade,
            'current_price': self.current_price,
            'high_price': self.high_price,
            'target_profit': self.target_profit,
            'trailing_stop': self.trailing_stop,
            'stop_loss': self.stop_loss,
            'profit_pct': self.profit_pct,
            'high_profit_pct': self.high_profit_pct,
            'entry_cci': self.entry_cci,  # 🆕
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class SellSignal:
    """매도 신호"""
    stock_code: str
    action: str                        # HOLD / SELL
    reason: SellReason = None
    current_price: float = 0.0
    profit_pct: float = 0.0
    message: str = ""


# =============================================================================
# 포지션 관리자 클래스
# =============================================================================

class PositionManager:
    """
    포지션 관리자
    
    보유 포지션을 메모리와 DB에 동시 관리하며,
    가격 업데이트 시 매도 신호를 생성합니다.
    """
    
    def __init__(
        self,
        db_path: Path = None,
        stop_loss: float = DEFAULT_STOP_LOSS,
    ):
        """
        초기화
        
        Args:
            db_path: SQLite 데이터베이스 경로
            stop_loss: 기본 손절선 (%)
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self.stop_loss = stop_loss
        
        # DB 디렉토리 생성
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 메모리 캐시 (stock_code -> PositionInfo)
        self._positions: Dict[str, PositionInfo] = {}
        self._lock = threading.Lock()
        
        # DB 초기화
        self._init_database()
        
        # DB에서 로드
        self._load_from_db()
        
        logger.info(
            f"PositionManager 초기화 완료 "
            f"(포지션: {len(self._positions)}개, 손절선: {stop_loss}%)"
        )
    
    # =========================================================================
    # 데이터베이스 초기화
    # =========================================================================
    
    def _init_database(self):
        """데이터베이스 테이블 생성"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
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
                    entry_cci REAL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 🆕 기존 DB에 entry_cci 컬럼이 없으면 추가
            try:
                cursor.execute("ALTER TABLE positions ADD COLUMN entry_cci REAL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # 이미 존재
            
            # 인덱스 생성
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_positions_code
                ON positions(stock_code)
            """)
            
            conn.commit()
    
    def _load_from_db(self):
        """DB에서 포지션 로드"""
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM positions")
                    
                    for row in cursor.fetchall():
                        # 🆕 entry_cci 안전하게 읽기 (기존 DB 호환)
                        entry_cci = 0.0
                        try:
                            entry_cci = row['entry_cci'] or 0.0
                        except (IndexError, KeyError):
                            pass
                        
                        position = PositionInfo(
                            id=row['id'],
                            stock_code=row['stock_code'],
                            stock_name=row['stock_name'] or '',
                            entry_price=row['entry_price'],
                            quantity=row['quantity'],
                            entry_time=datetime.fromisoformat(row['entry_time']),
                            score=row['score'] or 0,
                            ai_confidence=row['ai_confidence'] or 0,
                            grade=row['grade'] or 'C',
                            high_price=row['high_price'] or row['entry_price'],
                            target_profit=row['target_profit'] or 1.0,
                            trailing_stop=row['trailing_stop'] or 0.3,
                            stop_loss=row['stop_loss'] or self.stop_loss,
                            entry_cci=entry_cci,  # 🆕
                        )
                        
                        self._positions[position.stock_code] = position
                
                logger.info(f"DB에서 포지션 {len(self._positions)}개 로드")
            
            except Exception as e:
                logger.error(f"포지션 로드 실패: {e}")
    
    # =========================================================================
    # 포지션 추가/수정/삭제
    # =========================================================================
    
    def add_position(
        self,
        stock_code: str,
        stock_name: str,
        entry_price: float,
        quantity: int,
        score: float = 0,
        ai_confidence: float = 0,
        entry_cci: float = 0,  # 🆕 CCI 추가
    ) -> PositionInfo:
        """
        포지션 추가
        
        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            entry_price: 진입가
            quantity: 수량
            score: 규칙 점수 (0~100)
            ai_confidence: AI 신뢰도 (0~1)
            entry_cci: 매수 시점 CCI
        
        Returns:
            생성된 PositionInfo
        """
        # 등급 및 목표 결정
        grade = self._determine_grade(score)
        targets = PROFIT_TARGETS[grade]
        
        position = PositionInfo(
            stock_code=stock_code,
            stock_name=stock_name,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.now(),
            score=score,
            ai_confidence=ai_confidence,
            grade=grade,
            current_price=entry_price,
            high_price=entry_price,
            target_profit=targets['target_profit'],
            trailing_stop=targets['trailing_stop'],
            stop_loss=self.stop_loss,
            entry_cci=entry_cci,  # 🆕
        )
        
        with self._lock:
            # 이미 있으면 수량 추가
            if stock_code in self._positions:
                existing = self._positions[stock_code]
                total_qty = existing.quantity + quantity
                avg_price = (
                    (existing.entry_price * existing.quantity + entry_price * quantity) 
                    / total_qty
                )
                existing.entry_price = avg_price
                existing.quantity = total_qty
                existing.high_price = max(existing.high_price, entry_price)
                existing.updated_at = datetime.now()
                
                self._save_to_db(existing)
                
                logger.info(
                    f"포지션 추가 매수: {stock_code} {stock_name} "
                    f"+{quantity}주 @ {entry_price:,.0f}원 (총 {total_qty}주)"
                )
                
                return existing
            else:
                self._positions[stock_code] = position
                self._save_to_db(position)
                
                logger.info(
                    f"포지션 등록: {stock_code} {stock_name} "
                    f"{quantity}주 @ {entry_price:,.0f}원 ({grade}등급)"
                )
                
                return position
    
    def remove_position(self, stock_code: str) -> Optional[PositionInfo]:
        """
        포지션 삭제
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            삭제된 PositionInfo (없으면 None)
        """
        with self._lock:
            if stock_code not in self._positions:
                return None
            
            position = self._positions.pop(stock_code)
            self._delete_from_db(stock_code)
            
            logger.info(f"포지션 삭제: {stock_code} {position.stock_name}")
            
            return position
    
    def reduce_position(
        self,
        stock_code: str,
        quantity: int,
    ) -> Optional[PositionInfo]:
        """
        포지션 부분 청산
        
        Args:
            stock_code: 종목 코드
            quantity: 청산 수량
        
        Returns:
            업데이트된 PositionInfo (전량 청산 시 None)
        """
        with self._lock:
            if stock_code not in self._positions:
                return None
            
            position = self._positions[stock_code]
            
            if quantity >= position.quantity:
                # 전량 청산
                return self.remove_position(stock_code)
            else:
                # 부분 청산
                position.quantity -= quantity
                position.updated_at = datetime.now()
                self._save_to_db(position)
                
                logger.info(
                    f"포지션 부분 청산: {stock_code} -{quantity}주 "
                    f"(잔여: {position.quantity}주)"
                )
                
                return position
    
    # =========================================================================
    # 가격 업데이트 및 매도 신호
    # =========================================================================
    
    def update_price(
        self,
        stock_code: str,
        current_price: float,
    ) -> SellSignal:
        """
        가격 업데이트 및 매도 신호 확인
        
        Args:
            stock_code: 종목 코드
            current_price: 현재가
        
        Returns:
            SellSignal 객체
        """
        with self._lock:
            if stock_code not in self._positions:
                return SellSignal(
                    stock_code=stock_code,
                    action='HOLD',
                    message='포지션 없음'
                )
            
            position = self._positions[stock_code]
            
            # 수익률 계산
            profit_pct = (current_price - position.entry_price) / position.entry_price * 100
            
            # 상태 업데이트
            position.current_price = current_price
            position.profit_pct = profit_pct
            
            # 고점 갱신
            if current_price > position.high_price:
                position.high_price = current_price
                position.high_profit_pct = profit_pct
            
            position.updated_at = datetime.now()
            
            # 매도 신호 체크
            signal = self._check_sell_signal(position)
            
            return signal
    
    def _check_sell_signal(self, position: PositionInfo) -> SellSignal:
        """
        매도 신호 체크 (스캘핑용 v3.0)
        
        우선순위:
        1. 손절 (-0.7%)
        2. 익절 (등급별 1.5~2.0%)
        3. 트레일링 스탑 (고점 대비 -0.4%)
        4. 시간 손절 (3분 내 +0.3% 미달 / 10분 경과)
        
        Args:
            position: 포지션 정보
        
        Returns:
            SellSignal 객체
        """
        profit_pct = position.profit_pct
        
        # 1. 손절 체크 (-0.7%)
        if profit_pct <= position.stop_loss:
            return SellSignal(
                stock_code=position.stock_code,
                action='SELL',
                reason=SellReason.STOP_LOSS,
                current_price=position.current_price,
                profit_pct=profit_pct,
                message=f"손절 도달 ({profit_pct:.2f}% ≤ {position.stop_loss}%)"
            )
        
        # 2. 익절 체크
        if profit_pct >= position.target_profit:
            return SellSignal(
                stock_code=position.stock_code,
                action='SELL',
                reason=SellReason.TAKE_PROFIT,
                current_price=position.current_price,
                profit_pct=profit_pct,
                message=f"익절 도달 ({profit_pct:.2f}% ≥ {position.target_profit}%)"
            )
        
        # 3. 트레일링 스탑 체크 (수익 구간에서만)
        if position.high_profit_pct >= 0.5:  # 0.5% 이상 수익 경험 시 활성화
            drop_from_high = position.high_profit_pct - profit_pct
            
            if drop_from_high >= position.trailing_stop:
                return SellSignal(
                    stock_code=position.stock_code,
                    action='SELL',
                    reason=SellReason.TRAILING_STOP,
                    current_price=position.current_price,
                    profit_pct=profit_pct,
                    message=f"트레일링 스탑 (고점 {position.high_profit_pct:.2f}% → 현재 {profit_pct:.2f}%)"
                )
        
        # 4. 시간 손절 체크 (스캘핑 핵심!)
        hold_minutes = (datetime.now() - position.entry_time).total_seconds() / 60
        
        # 4-1. 3분 내 +0.3% 미달 시 청산
        if hold_minutes >= DEFAULT_TIME_STOP_MINUTES:
            if profit_pct < DEFAULT_TIME_STOP_THRESHOLD:
                return SellSignal(
                    stock_code=position.stock_code,
                    action='SELL',
                    reason=SellReason.TIME_STOP,
                    current_price=position.current_price,
                    profit_pct=profit_pct,
                    message=f"시간손절 ({hold_minutes:.1f}분 경과, 수익 {profit_pct:.2f}% < {DEFAULT_TIME_STOP_THRESHOLD}%)"
                )
        
        # 4-2. 10분 경과 + 손익 근처 시 청산
        if hold_minutes >= DEFAULT_MAX_HOLD_MINUTES:
            if -0.3 <= profit_pct <= 0.5:  # 손익분기 근처
                return SellSignal(
                    stock_code=position.stock_code,
                    action='SELL',
                    reason=SellReason.TIME_STOP,
                    current_price=position.current_price,
                    profit_pct=profit_pct,
                    message=f"최대보유시간 ({hold_minutes:.1f}분 > {DEFAULT_MAX_HOLD_MINUTES}분, 수익 {profit_pct:.2f}%)"
                )
        
        # 홀드
        return SellSignal(
            stock_code=position.stock_code,
            action='HOLD',
            current_price=position.current_price,
            profit_pct=profit_pct,
            message=f"보유 중 ({profit_pct:+.2f}%, {hold_minutes:.1f}분)"
        )
    
    def update_all_prices(
        self,
        prices: Dict[str, float],
    ) -> List[SellSignal]:
        """
        모든 포지션 가격 업데이트
        
        Args:
            prices: {종목코드: 현재가} 딕셔너리
        
        Returns:
            매도 신호 리스트 (SELL인 것만)
        """
        sell_signals = []
        
        for stock_code, price in prices.items():
            signal = self.update_price(stock_code, price)
            
            if signal.action == 'SELL':
                sell_signals.append(signal)
        
        return sell_signals
    
    # =========================================================================
    # 조회
    # =========================================================================
    
    def get_position(self, stock_code: str) -> Optional[PositionInfo]:
        """포지션 조회"""
        with self._lock:
            return self._positions.get(stock_code)
    
    def get_all_positions(self) -> List[PositionInfo]:
        """모든 포지션 조회"""
        with self._lock:
            return list(self._positions.values())
    
    def get_position_codes(self) -> List[str]:
        """보유 종목 코드 목록"""
        with self._lock:
            return list(self._positions.keys())
    
    def has_position(self, stock_code: str) -> bool:
        """포지션 보유 여부"""
        with self._lock:
            return stock_code in self._positions
    
    def get_position_count(self) -> int:
        """포지션 수"""
        with self._lock:
            return len(self._positions)
    
    def get_total_invested(self) -> float:
        """총 투자금액"""
        with self._lock:
            return sum(
                p.entry_price * p.quantity 
                for p in self._positions.values()
            )
    
    def get_total_profit_pct(self) -> float:
        """평균 수익률"""
        with self._lock:
            if not self._positions:
                return 0.0
            
            total_invested = sum(p.entry_price * p.quantity for p in self._positions.values())
            total_current = sum(p.current_price * p.quantity for p in self._positions.values())
            
            if total_invested == 0:
                return 0.0
            
            return (total_current - total_invested) / total_invested * 100
    
    # =========================================================================
    # 특수 기능
    # =========================================================================
    
    def tighten_stop_loss(self, ratio: float = 0.5):
        """
        손절선 타이트하게 조정
        
        연속 손절 시 손절선을 더 가깝게 설정합니다.
        
        Args:
            ratio: 조정 비율 (예: 0.5면 -1.5% → -0.75%)
        """
        with self._lock:
            for position in self._positions.values():
                position.stop_loss = position.stop_loss * ratio
                self._save_to_db(position)
            
            logger.warning(f"손절선 타이트 조정: {ratio*100:.0f}%")
    
    def mark_for_emergency_exit(self):
        """
        비상 청산 마킹
        
        모든 포지션의 손절선을 0%로 설정합니다.
        """
        with self._lock:
            for position in self._positions.values():
                position.stop_loss = 0  # 현재가 이하면 즉시 청산
            
            logger.warning("🚨 전 포지션 비상 청산 마킹")
    
    def check_time_limit(self, time_limit: datetime) -> List[PositionInfo]:
        """
        시간 제한 체크
        
        Args:
            time_limit: 마감 시간
        
        Returns:
            시간 초과 포지션 리스트
        """
        with self._lock:
            overtime = []
            
            for position in self._positions.values():
                if position.entry_time < time_limit:
                    overtime.append(position)
            
            return overtime
    
    # =========================================================================
    # 등급 관련
    # =========================================================================
    
    def _determine_grade(self, score: float) -> str:
        """점수에 따른 등급 결정"""
        if score >= 90:
            return 'S'
        elif score >= 80:
            return 'A'
        elif score >= 70:
            return 'B'
        else:
            return 'C'
    
    def update_grade(self, stock_code: str, new_score: float):
        """
        등급 업데이트
        
        AI 분석 결과로 등급을 재조정합니다.
        """
        with self._lock:
            if stock_code not in self._positions:
                return
            
            position = self._positions[stock_code]
            new_grade = self._determine_grade(new_score)
            targets = PROFIT_TARGETS[new_grade]
            
            position.score = new_score
            position.grade = new_grade
            position.target_profit = targets['target_profit']
            position.trailing_stop = targets['trailing_stop']
            position.updated_at = datetime.now()
            
            self._save_to_db(position)
            
            logger.info(f"등급 업데이트: {stock_code} → {new_grade}등급 (점수: {new_score:.1f})")
    
    # =========================================================================
    # DB 저장/삭제
    # =========================================================================
    
    def _save_to_db(self, position: PositionInfo):
        """DB에 저장"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO positions (
                        stock_code, stock_name, entry_price, quantity,
                        entry_time, score, ai_confidence, grade,
                        high_price, target_profit, trailing_stop, stop_loss,
                        entry_cci, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    position.stock_code,
                    position.stock_name,
                    position.entry_price,
                    position.quantity,
                    position.entry_time.isoformat(),
                    position.score,
                    position.ai_confidence,
                    position.grade,
                    position.high_price,
                    position.target_profit,
                    position.trailing_stop,
                    position.stop_loss,
                    position.entry_cci,  # 🆕
                    datetime.now().isoformat(),
                ))
                
                conn.commit()
        
        except Exception as e:
            logger.error(f"포지션 저장 실패: {e}")
    
    def _delete_from_db(self, stock_code: str):
        """DB에서 삭제"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM positions WHERE stock_code = ?",
                    (stock_code,)
                )
                conn.commit()
        
        except Exception as e:
            logger.error(f"포지션 삭제 실패: {e}")
    
    def clear_all(self):
        """모든 포지션 삭제"""
        with self._lock:
            self._positions.clear()
            
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM positions")
                    conn.commit()
            except Exception as e:
                logger.error(f"포지션 전체 삭제 실패: {e}")
            
            logger.warning("모든 포지션 삭제됨")
    
    # =========================================================================
    # 동기화
    # =========================================================================
    
    def sync_with_broker(self, broker_positions: List[Dict]):
        """
        브로커와 포지션 동기화
        
        실제 보유 종목과 메모리/DB를 동기화합니다.
        
        Args:
            broker_positions: 브로커에서 조회한 보유 종목 리스트
        """
        with self._lock:
            broker_codes = {p['stock_code'] for p in broker_positions}
            local_codes = set(self._positions.keys())
            
            # 브로커에만 있는 종목 (수동 매수) → 추가
            for bp in broker_positions:
                if bp['stock_code'] not in local_codes:
                    position = PositionInfo(
                        stock_code=bp['stock_code'],
                        stock_name=bp.get('stock_name', ''),
                        entry_price=bp.get('avg_price', 0),
                        quantity=bp.get('quantity', 0),
                        entry_time=datetime.now(),
                        score=0,  # 알 수 없음
                        grade='C',
                    )
                    self._positions[bp['stock_code']] = position
                    self._save_to_db(position)
                    
                    logger.info(f"동기화 추가: {bp['stock_code']} (수동 매수 추정)")
            
            # 로컬에만 있는 종목 (수동 매도) → 삭제
            for code in local_codes - broker_codes:
                del self._positions[code]
                self._delete_from_db(code)
                
                logger.info(f"동기화 삭제: {code} (수동 매도 추정)")
            
            logger.info(f"포지션 동기화 완료 (현재: {len(self._positions)}개)")
    
    # =========================================================================
    # 통계
    # =========================================================================
    
    def get_summary(self) -> str:
        """포지션 요약"""
        positions = self.get_all_positions()
        
        if not positions:
            return "📊 보유 포지션 없음"
        
        lines = ["📊 보유 포지션 요약", "-" * 40]
        
        for p in positions:
            status = "🟢" if p.profit_pct >= 0 else "🔴"
            lines.append(
                f"{status} {p.stock_code} {p.stock_name}: "
                f"{p.quantity}주 @ {p.entry_price:,.0f}원 "
                f"({p.profit_pct:+.2f}%) [{p.grade}등급]"
            )
        
        lines.append("-" * 40)
        lines.append(f"총 투자금: {self.get_total_invested():,.0f}원")
        lines.append(f"평균 수익률: {self.get_total_profit_pct():+.2f}%")
        
        return "\n".join(lines)


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
    print("PositionManager 테스트")
    print("=" * 60)
    
    # 테스트용 임시 DB
    import tempfile
    test_db = Path(tempfile.gettempdir()) / 'test_positions.db'
    
    # 관리자 생성
    pm = PositionManager(db_path=test_db)
    
    # 1. 포지션 추가
    print("\n1. 포지션 추가 테스트:")
    pm.add_position("005930", "삼성전자", 70000, 10, score=85)
    pm.add_position("000660", "SK하이닉스", 120000, 5, score=72)
    pm.add_position("035720", "카카오", 50000, 20, score=65)
    
    print(f"   포지션 수: {pm.get_position_count()}")
    
    # 2. 가격 업데이트 및 신호 확인
    print("\n2. 가격 업데이트 테스트:")
    
    # 삼성전자: 익절 도달 (85점 → A등급 → 1.2% 익절)
    signal = pm.update_price("005930", 70900)  # +1.29%
    print(f"   삼성전자: {signal.action} - {signal.message}")
    
    # SK하이닉스: 손절 도달
    signal = pm.update_price("000660", 118000)  # -1.67%
    print(f"   SK하이닉스: {signal.action} - {signal.message}")
    
    # 카카오: 트레일링 테스트
    pm.update_price("035720", 50500)  # 고점 갱신
    pm.update_price("035720", 50300)  # 하락
    signal = pm.update_price("035720", 50100)  # 트레일링 발동?
    print(f"   카카오: {signal.action} - {signal.message}")
    
    # 3. 포지션 조회
    print("\n3. 포지션 조회:")
    for pos in pm.get_all_positions():
        print(
            f"   {pos.stock_code} {pos.stock_name}: "
            f"{pos.quantity}주 @ {pos.entry_price:,.0f}원 "
            f"({pos.profit_pct:+.2f}%) [{pos.grade}등급]"
        )
    
    # 4. 등급별 목표
    print("\n4. 등급별 목표:")
    for grade, targets in PROFIT_TARGETS.items():
        print(
            f"   {grade}등급: 익절 {targets['target_profit']}%, "
            f"트레일링 {targets['trailing_stop']}%"
        )
    
    # 5. 요약
    print("\n5. 포지션 요약:")
    print(pm.get_summary())
    
    # 6. 포지션 삭제
    print("\n6. 포지션 삭제 테스트:")
    pm.remove_position("005930")
    print(f"   삭제 후 포지션 수: {pm.get_position_count()}")
    
    # 정리
    test_db.unlink(missing_ok=True)
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
