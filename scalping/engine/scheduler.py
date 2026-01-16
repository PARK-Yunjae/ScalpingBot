#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Scheduler (시간 스케줄러)
============================================================================
장 시간 및 이벤트 스케줄 관리

핵심 기능:
- 장 시간 체크 (09:00~15:30)
- 시간대별 이벤트 스케줄
- 청산 시간 알림
- 휴장일 체크

사용법:
    scheduler = TradingScheduler()
    
    if scheduler.is_market_open():
        # 매매
    
    scheduler.schedule_at("14:50", liquidate_all)
============================================================================
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Callable, Tuple
from datetime import datetime, date, timedelta
from dataclasses import dataclass
from enum import Enum

# 로거 설정
logger = logging.getLogger('ScalpingBot.Scheduler')


# =============================================================================
# 상수 설정
# =============================================================================

# 장 시간
MARKET_PREP_TIME = "08:55"      # 준비 시작
MARKET_OPEN_TIME = "09:00"      # 장 시작
MARKET_CLOSE_WARNING = "14:50"  # 청산 경고
MARKET_CLOSE_TIME = "15:20"     # 청산 시작
MARKET_END_TIME = "15:30"       # 장 종료

# 2026년 한국 휴장일 (예시)
HOLIDAYS_2026 = [
    "2026-01-01",  # 신정
    "2026-01-27",  # 설날 연휴
    "2026-01-28",  # 설날
    "2026-01-29",  # 설날 연휴
    "2026-03-01",  # 삼일절
    "2026-05-05",  # 어린이날
    "2026-05-24",  # 부처님오신날
    "2026-06-06",  # 현충일
    "2026-08-15",  # 광복절
    "2026-09-26",  # 추석 연휴
    "2026-09-27",  # 추석
    "2026-09-28",  # 추석 연휴
    "2026-10-03",  # 개천절
    "2026-10-09",  # 한글날
    "2026-12-25",  # 크리스마스
]


# =============================================================================
# 시간대 열거형
# =============================================================================

class MarketPhase(Enum):
    """장 시간대"""
    PRE_MARKET = "장전"       # ~09:00
    OPENING = "동시호가"      # 09:00~09:05
    MORNING = "오전장"        # 09:05~12:00
    LUNCH = "점심"           # 12:00~13:00
    AFTERNOON = "오후장"      # 13:00~15:20
    CLOSING = "청산시간"      # 15:20~15:30
    AFTER_MARKET = "장후"     # 15:30~


@dataclass
class ScheduledTask:
    """예약된 작업"""
    time_str: str
    callback: Callable
    name: str = ""
    repeat_daily: bool = True
    last_run: date = None


# =============================================================================
# 트레이딩 스케줄러 클래스
# =============================================================================

class TradingScheduler:
    """
    트레이딩 시간 스케줄러
    
    장 시간을 관리하고 예약된 작업을 실행합니다.
    """
    
    def __init__(
        self,
        holidays: List[str] = None,
        on_phase_change: Callable[[MarketPhase], None] = None,
    ):
        """
        초기화
        
        Args:
            holidays: 휴장일 리스트 (YYYY-MM-DD)
            on_phase_change: 시간대 변경 콜백
        """
        self.holidays = set(holidays or HOLIDAYS_2026)
        self.on_phase_change = on_phase_change
        
        # 예약 작업
        self._tasks: List[ScheduledTask] = []
        
        # 스케줄러 스레드
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # 현재 시간대
        self._current_phase = self._get_current_phase()
        
        logger.info("TradingScheduler 초기화")
    
    # =========================================================================
    # 장 시간 체크
    # =========================================================================
    
    def is_market_open(self) -> bool:
        """장이 열려있는가"""
        if self.is_holiday():
            return False
        
        now = datetime.now()
        
        # 주말 체크
        if now.weekday() >= 5:
            return False
        
        # 시간 체크
        open_time = self._parse_time(MARKET_OPEN_TIME)
        close_time = self._parse_time(MARKET_END_TIME)
        current_time = now.time()
        
        return open_time <= current_time <= close_time
    
    def is_trading_time(self) -> bool:
        """매매 가능 시간인가 (청산 시간 전)"""
        if not self.is_market_open():
            return False
        
        now = datetime.now()
        close_warning = self._parse_time(MARKET_CLOSE_WARNING)
        
        return now.time() < close_warning
    
    def is_closing_time(self) -> bool:
        """청산 시간인가"""
        if not self.is_market_open():
            return False
        
        now = datetime.now()
        close_start = self._parse_time(MARKET_CLOSE_TIME)
        close_end = self._parse_time(MARKET_END_TIME)
        
        return close_start <= now.time() <= close_end
    
    def is_holiday(self, check_date: date = None) -> bool:
        """휴장일인가"""
        check_date = check_date or date.today()
        
        # 주말 체크
        if check_date.weekday() >= 5:
            return True
        
        # 휴장일 체크
        return str(check_date) in self.holidays
    
    def is_weekend(self) -> bool:
        """주말인가"""
        return datetime.now().weekday() >= 5
    
    # =========================================================================
    # 시간대 관리
    # =========================================================================
    
    def get_current_phase(self) -> MarketPhase:
        """현재 시간대"""
        return self._get_current_phase()
    
    def _get_current_phase(self) -> MarketPhase:
        """현재 시간대 계산"""
        if self.is_holiday() or self.is_weekend():
            return MarketPhase.AFTER_MARKET
        
        now = datetime.now().time()
        
        open_time = self._parse_time(MARKET_OPEN_TIME)
        opening_end = self._parse_time("09:05")
        lunch_start = self._parse_time("12:00")
        lunch_end = self._parse_time("13:00")
        close_start = self._parse_time(MARKET_CLOSE_TIME)
        close_end = self._parse_time(MARKET_END_TIME)
        
        if now < open_time:
            return MarketPhase.PRE_MARKET
        elif now < opening_end:
            return MarketPhase.OPENING
        elif now < lunch_start:
            return MarketPhase.MORNING
        elif now < lunch_end:
            return MarketPhase.LUNCH
        elif now < close_start:
            return MarketPhase.AFTERNOON
        elif now < close_end:
            return MarketPhase.CLOSING
        else:
            return MarketPhase.AFTER_MARKET
    
    # =========================================================================
    # 시간 계산
    # =========================================================================
    
    def time_to_open(self) -> timedelta:
        """장 시작까지 남은 시간"""
        now = datetime.now()
        
        # 오늘 장 시간
        open_dt = now.replace(
            hour=int(MARKET_OPEN_TIME.split(':')[0]),
            minute=int(MARKET_OPEN_TIME.split(':')[1]),
            second=0,
            microsecond=0
        )
        
        if now >= open_dt:
            # 내일 장 시작
            open_dt += timedelta(days=1)
            
            # 주말 스킵
            while open_dt.weekday() >= 5 or str(open_dt.date()) in self.holidays:
                open_dt += timedelta(days=1)
        
        return open_dt - now
    
    def time_to_close(self) -> timedelta:
        """장 마감까지 남은 시간"""
        if not self.is_market_open():
            return timedelta(0)
        
        now = datetime.now()
        close_dt = now.replace(
            hour=int(MARKET_END_TIME.split(':')[0]),
            minute=int(MARKET_END_TIME.split(':')[1]),
            second=0,
            microsecond=0
        )
        
        return max(close_dt - now, timedelta(0))
    
    # =========================================================================
    # 작업 스케줄
    # =========================================================================
    
    def schedule_at(
        self,
        time_str: str,
        callback: Callable,
        name: str = "",
        repeat_daily: bool = True,
    ):
        """
        특정 시간에 작업 예약
        
        Args:
            time_str: 시간 (HH:MM)
            callback: 콜백 함수
            name: 작업 이름
            repeat_daily: 매일 반복
        """
        task = ScheduledTask(
            time_str=time_str,
            callback=callback,
            name=name or f"task_{len(self._tasks)}",
            repeat_daily=repeat_daily,
        )
        self._tasks.append(task)
        logger.info(f"작업 예약: {name} @ {time_str}")
    
    def start(self):
        """스케줄러 시작"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            name="Scheduler",
            daemon=True
        )
        self._thread.start()
        logger.info("스케줄러 시작")
    
    def stop(self):
        """스케줄러 중지"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("스케줄러 중지")
    
    def _scheduler_loop(self):
        """스케줄러 루프"""
        while self._running:
            try:
                now = datetime.now()
                today = date.today()
                current_time = now.strftime("%H:%M")
                
                # 시간대 변경 체크
                new_phase = self._get_current_phase()
                if new_phase != self._current_phase:
                    self._current_phase = new_phase
                    logger.info(f"시간대 변경: {new_phase.value}")
                    
                    if self.on_phase_change:
                        try:
                            self.on_phase_change(new_phase)
                        except Exception as e:
                            logger.error(f"시간대 변경 콜백 에러: {e}")
                
                # 예약 작업 실행
                for task in self._tasks:
                    if task.time_str == current_time:
                        # 오늘 이미 실행했는지 체크
                        if task.last_run == today:
                            continue
                        
                        # 실행
                        logger.info(f"예약 작업 실행: {task.name}")
                        try:
                            task.callback()
                            task.last_run = today
                        except Exception as e:
                            logger.error(f"예약 작업 에러: {task.name} - {e}")
                
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"스케줄러 에러: {e}")
                time.sleep(5)
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def _parse_time(self, time_str: str):
        """시간 문자열 파싱"""
        h, m = map(int, time_str.split(':'))
        return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0).time()
    
    def add_holiday(self, date_str: str):
        """휴장일 추가"""
        self.holidays.add(date_str)
    
    def get_next_trading_day(self) -> date:
        """다음 거래일"""
        check_date = date.today() + timedelta(days=1)
        
        while self.is_holiday(check_date):
            check_date += timedelta(days=1)
        
        return check_date
    
    def get_status(self) -> Dict:
        """상태 조회"""
        return {
            'is_market_open': self.is_market_open(),
            'is_trading_time': self.is_trading_time(),
            'is_closing_time': self.is_closing_time(),
            'current_phase': self.get_current_phase().value,
            'time_to_close': str(self.time_to_close()),
            'scheduled_tasks': len(self._tasks),
        }


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("TradingScheduler 테스트")
    print("=" * 60)
    
    def on_phase(phase: MarketPhase):
        print(f"   [콜백] 시간대: {phase.value}")
    
    scheduler = TradingScheduler(on_phase_change=on_phase)
    
    print("\n1. 현재 상태:")
    print(f"   장 오픈: {scheduler.is_market_open()}")
    print(f"   매매 시간: {scheduler.is_trading_time()}")
    print(f"   청산 시간: {scheduler.is_closing_time()}")
    print(f"   시간대: {scheduler.get_current_phase().value}")
    
    print("\n2. 시간 계산:")
    print(f"   장 마감까지: {scheduler.time_to_close()}")
    print(f"   장 시작까지: {scheduler.time_to_open()}")
    print(f"   다음 거래일: {scheduler.get_next_trading_day()}")
    
    print("\n3. 휴장일 체크:")
    print(f"   오늘: {scheduler.is_holiday()}")
    print(f"   2026-01-01: {scheduler.is_holiday(date(2026, 1, 1))}")
    
    print("\n4. 작업 예약:")
    scheduler.schedule_at("14:50", lambda: print("청산 시작!"), "청산 알림")
    scheduler.schedule_at("15:20", lambda: print("청산 완료!"), "청산 완료")
    print(f"   예약된 작업: {len(scheduler._tasks)}개")
    
    print("\n5. 상태 요약:")
    status = scheduler.get_status()
    for key, value in status.items():
        print(f"   {key}: {value}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
