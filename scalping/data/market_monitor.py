#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Market Monitor (시장 지수 모니터)
============================================================================
코스피/코스닥 지수를 실시간으로 감시하고 시장 상태를 판단

핵심 기능:
- 코스피/코스닥 지수 실시간 조회
- MA5, MA20, MA60 이동평균 계산
- 시장 모드 결정 (NORMAL / CONSERVATIVE / EMERGENCY)
- 5일 추세 분석

시장 모드:
- NORMAL: 정상 시장 (코스피 MA20 위, 등락률 > -2%)
- CONSERVATIVE: 보수적 시장 (코스피 MA20 아래)
- EMERGENCY: 비상 시장 (코스피 -2% 이상 급락)

사용법:
    monitor = MarketMonitor(broker)
    monitor.start()
    
    state = monitor.get_state()
    print(f"모드: {state.mode}, 코스피: {state.kospi_change:+.2f}%")
============================================================================
"""

import time
import logging
import threading
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
from enum import Enum

# 로거 설정
logger = logging.getLogger('ScalpingBot.Market')


# =============================================================================
# 시장 모드 열거형
# =============================================================================

class MarketMode(Enum):
    """시장 모드"""
    NORMAL = "NORMAL"              # 정상 시장
    CONSERVATIVE = "CONSERVATIVE"   # 보수적 시장
    EMERGENCY = "EMERGENCY"         # 비상 (급락)


# =============================================================================
# 시장 상태 데이터 클래스
# =============================================================================

@dataclass
class MarketState:
    """시장 상태 데이터"""
    # 코스피 지수
    kospi_price: float = 0.0
    kospi_change: float = 0.0       # 전일 대비 등락률 (%)
    kospi_ma5: float = 0.0
    kospi_ma20: float = 0.0
    kospi_ma60: float = 0.0
    
    # 코스닥 지수
    kosdaq_price: float = 0.0
    kosdaq_change: float = 0.0
    
    # 이동평균 대비
    above_ma5: bool = True
    above_ma20: bool = True
    above_ma60: bool = True
    
    # 추세
    trend_5day: float = 0.0         # 5일간 추세 (%)
    trend_direction: str = "FLAT"   # UP / DOWN / FLAT
    
    # 시장 모드
    mode: MarketMode = MarketMode.NORMAL
    mode_reason: str = ""
    
    # 메타 정보
    last_update: datetime = field(default_factory=datetime.now)
    is_market_open: bool = False
    
    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            'kospi_price': self.kospi_price,
            'kospi_change': self.kospi_change,
            'kospi_ma5': self.kospi_ma5,
            'kospi_ma20': self.kospi_ma20,
            'kospi_ma60': self.kospi_ma60,
            'kosdaq_price': self.kosdaq_price,
            'kosdaq_change': self.kosdaq_change,
            'above_ma5': self.above_ma5,
            'above_ma20': self.above_ma20,
            'above_ma60': self.above_ma60,
            'trend_5day': self.trend_5day,
            'trend_direction': self.trend_direction,
            'mode': self.mode.value,
            'mode_reason': self.mode_reason,
            'last_update': self.last_update.isoformat(),
            'is_market_open': self.is_market_open,
        }


# =============================================================================
# 시장 모니터 클래스
# =============================================================================

class MarketMonitor:
    """
    시장 지수 모니터
    
    별도 스레드에서 지수를 주기적으로 조회하고
    시장 상태를 업데이트합니다.
    """
    
    def __init__(
        self,
        broker,
        update_interval: int = 10,
        emergency_threshold: float = -2.0,
        on_mode_change: Callable[[MarketMode, MarketMode], None] = None,
    ):
        """
        초기화
        
        Args:
            broker: KISBroker 인스턴스
            update_interval: 갱신 주기 (초)
            emergency_threshold: 비상 모드 임계값 (%)
            on_mode_change: 모드 변경 시 콜백 함수
        """
        self.broker = broker
        self.update_interval = update_interval
        self.emergency_threshold = emergency_threshold
        self.on_mode_change = on_mode_change
        
        # 상태
        self.state = MarketState()
        self._lock = threading.Lock()
        
        # 히스토리 (MA 계산용)
        self._kospi_history: deque = deque(maxlen=60)  # 60일치
        self._kosdaq_history: deque = deque(maxlen=60)
        
        # 스레드 관리
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # 통계
        self._stats = {
            'total_updates': 0,
            'errors': 0,
            'mode_changes': 0,
        }
        
        logger.info(
            f"MarketMonitor 초기화 (갱신 주기: {update_interval}초, "
            f"비상 임계값: {emergency_threshold}%)"
        )
    
    # =========================================================================
    # 시작/중지
    # =========================================================================
    
    def start(self):
        """
        모니터링 시작
        
        별도 스레드에서 지수를 주기적으로 조회합니다.
        """
        if self._running:
            logger.warning("MarketMonitor가 이미 실행 중입니다.")
            return
        
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="Market-Monitor",
            daemon=True
        )
        self._thread.start()
        
        logger.info("📊 MarketMonitor 시작")
    
    def stop(self):
        """모니터링 중지"""
        if not self._running:
            return
        
        self._running = False
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        
        logger.info("🛑 MarketMonitor 중지")
    
    def is_running(self) -> bool:
        """실행 상태 확인"""
        return self._running
    
    # =========================================================================
    # 상태 조회
    # =========================================================================
    
    def get_state(self) -> MarketState:
        """
        현재 시장 상태 조회
        
        Returns:
            MarketState 객체 (복사본)
        """
        with self._lock:
            # 얕은 복사로 반환
            return MarketState(
                kospi_price=self.state.kospi_price,
                kospi_change=self.state.kospi_change,
                kospi_ma5=self.state.kospi_ma5,
                kospi_ma20=self.state.kospi_ma20,
                kospi_ma60=self.state.kospi_ma60,
                kosdaq_price=self.state.kosdaq_price,
                kosdaq_change=self.state.kosdaq_change,
                above_ma5=self.state.above_ma5,
                above_ma20=self.state.above_ma20,
                above_ma60=self.state.above_ma60,
                trend_5day=self.state.trend_5day,
                trend_direction=self.state.trend_direction,
                mode=self.state.mode,
                mode_reason=self.state.mode_reason,
                last_update=self.state.last_update,
                is_market_open=self.state.is_market_open,
            )
    
    def get_mode(self) -> MarketMode:
        """현재 시장 모드 조회"""
        with self._lock:
            return self.state.mode
    
    def is_emergency(self) -> bool:
        """비상 모드 여부"""
        return self.get_mode() == MarketMode.EMERGENCY
    
    def is_conservative(self) -> bool:
        """보수적 모드 여부"""
        return self.get_mode() in (MarketMode.CONSERVATIVE, MarketMode.EMERGENCY)
    
    def is_normal(self) -> bool:
        """정상 모드 여부"""
        return self.get_mode() == MarketMode.NORMAL
    
    # =========================================================================
    # 모니터링 루프 (내부)
    # =========================================================================
    
    def _monitor_loop(self):
        """모니터링 메인 루프"""
        logger.info("MarketMonitor 루프 시작")
        
        # 시작 시 즉시 한 번 업데이트
        self._update_market_data()
        
        while self._running:
            try:
                time.sleep(self.update_interval)
                
                if not self._running:
                    break
                
                self._update_market_data()
                
            except Exception as e:
                self._stats['errors'] += 1
                logger.exception(f"MarketMonitor 에러: {e}")
                time.sleep(30)  # 에러 시 30초 대기
        
        logger.info("MarketMonitor 루프 종료")
    
    def _update_market_data(self):
        """시장 데이터 업데이트"""
        try:
            # 코스피 지수 조회
            kospi = self.broker.get_index_price('0001')
            kosdaq = self.broker.get_index_price('1001')
            
            with self._lock:
                # 코스피 업데이트 (현재가만 갱신, 일봉 히스토리는 유지)
                if kospi and kospi.get('price', 0) > 0:
                    self.state.kospi_price = kospi['price']
                    self.state.kospi_change = kospi.get('change_pct', 0)
                    # 주의: 일봉 히스토리에는 추가하지 않음 (initialize_history에서 로드한 값 유지)
                    # 당일 종가는 현재가로 대체하여 MA 계산
                
                # 코스닥 업데이트 (현재가만 갱신)
                if kosdaq and kosdaq.get('price', 0) > 0:
                    self.state.kosdaq_price = kosdaq['price']
                    self.state.kosdaq_change = kosdaq.get('change_pct', 0)
                
                # 이동평균 계산 (일봉 히스토리 + 현재가)
                self._calculate_moving_averages()
                
                # 추세 계산
                self._calculate_trend()
                
                # 시장 모드 결정
                old_mode = self.state.mode
                self._determine_market_mode()
                new_mode = self.state.mode
                
                # 장 운영 시간 체크
                self.state.is_market_open = self._is_market_open()
                
                # 업데이트 시간
                self.state.last_update = datetime.now()
                self._stats['total_updates'] += 1
                
                # 모드 변경 콜백
                if old_mode != new_mode:
                    self._stats['mode_changes'] += 1
                    logger.warning(
                        f"⚠️ 시장 모드 변경: {old_mode.value} → {new_mode.value} "
                        f"({self.state.mode_reason})"
                    )
                    
                    if self.on_mode_change:
                        try:
                            self.on_mode_change(old_mode, new_mode)
                        except Exception as e:
                            logger.error(f"모드 변경 콜백 에러: {e}")
            
            logger.debug(
                f"시장 업데이트: 코스피 {self.state.kospi_price:,.2f} "
                f"({self.state.kospi_change:+.2f}%), 모드: {self.state.mode.value}"
            )
        
        except Exception as e:
            self._stats['errors'] += 1
            logger.error(f"시장 데이터 업데이트 실패: {e}")
    
    def _calculate_moving_averages(self):
        """
        이동평균 계산
        
        일봉 히스토리 + 현재가를 이용하여 MA를 계산합니다.
        히스토리가 N-1개이면, 현재가를 당일 종가로 대체하여 N일 MA를 계산합니다.
        """
        history = list(self._kospi_history)
        current_price = self.state.kospi_price
        
        # 현재가가 있으면 히스토리 끝에 추가하여 계산 (당일 종가 대체)
        if current_price > 0:
            calc_history = history + [current_price]
        else:
            calc_history = history
        
        # MA5 계산
        if len(calc_history) >= 5:
            self.state.kospi_ma5 = sum(calc_history[-5:]) / 5
            self.state.above_ma5 = current_price >= self.state.kospi_ma5 if current_price > 0 else True
        
        # MA20 계산 (핵심: 시장 모드 결정에 사용)
        if len(calc_history) >= 20:
            self.state.kospi_ma20 = sum(calc_history[-20:]) / 20
            self.state.above_ma20 = current_price >= self.state.kospi_ma20 if current_price > 0 else True
        
        # MA60 계산
        if len(calc_history) >= 60:
            self.state.kospi_ma60 = sum(calc_history[-60:]) / 60
            self.state.above_ma60 = current_price >= self.state.kospi_ma60 if current_price > 0 else True
    
    def _calculate_trend(self):
        """5일 추세 계산"""
        history = list(self._kospi_history)
        
        if len(history) >= 5:
            old_price = history[-5]
            new_price = history[-1]
            
            if old_price > 0:
                self.state.trend_5day = (new_price - old_price) / old_price * 100
                
                if self.state.trend_5day > 1.0:
                    self.state.trend_direction = "UP"
                elif self.state.trend_5day < -1.0:
                    self.state.trend_direction = "DOWN"
                else:
                    self.state.trend_direction = "FLAT"
    
    def _determine_market_mode(self):
        """
        시장 모드 결정
        
        우선순위:
        1. 코스피 -2% 이상 급락 → EMERGENCY
        2. 코스피 MA20 아래 → CONSERVATIVE
        3. 그 외 → NORMAL
        """
        # 1. 비상 모드 체크 (급락)
        if self.state.kospi_change <= self.emergency_threshold:
            self.state.mode = MarketMode.EMERGENCY
            self.state.mode_reason = f"코스피 급락 ({self.state.kospi_change:+.2f}%)"
            return
        
        # 2. 보수적 모드 체크 (MA20 아래)
        if not self.state.above_ma20 and self.state.kospi_ma20 > 0:
            self.state.mode = MarketMode.CONSERVATIVE
            self.state.mode_reason = "코스피 MA20 하회"
            return
        
        # 3. 정상 모드
        self.state.mode = MarketMode.NORMAL
        self.state.mode_reason = "정상"
    
    def _is_market_open(self) -> bool:
        """장 운영 시간 확인"""
        now = datetime.now()
        
        # 주말 체크
        if now.weekday() >= 5:  # 토요일(5), 일요일(6)
            return False
        
        # 시간 체크 (09:00 ~ 15:30)
        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        
        return market_open <= now <= market_close
    
    # =========================================================================
    # 수동 업데이트
    # =========================================================================
    
    def force_update(self):
        """강제 업데이트 (수동)"""
        self._update_market_data()
    
    def set_emergency_mode(self, reason: str = "수동 설정"):
        """
        비상 모드 강제 설정
        
        Args:
            reason: 설정 사유
        """
        with self._lock:
            old_mode = self.state.mode
            self.state.mode = MarketMode.EMERGENCY
            self.state.mode_reason = reason
            
            logger.warning(f"🚨 비상 모드 강제 설정: {reason}")
            
            if self.on_mode_change and old_mode != MarketMode.EMERGENCY:
                self.on_mode_change(old_mode, MarketMode.EMERGENCY)
    
    def reset_mode(self):
        """모드 리셋 (재계산)"""
        with self._lock:
            self._determine_market_mode()
    
    # =========================================================================
    # 히스토리 초기화 (일봉 데이터로)
    # =========================================================================
    
    def initialize_history(self, kospi_prices: List[float], kosdaq_prices: List[float] = None):
        """
        히스토리 초기화 (일봉 데이터로)
        
        MA 계산을 위해 과거 데이터로 히스토리를 초기화합니다.
        
        Args:
            kospi_prices: 코스피 종가 리스트 (오래된 순)
            kosdaq_prices: 코스닥 종가 리스트 (선택)
        """
        with self._lock:
            self._kospi_history.clear()
            self._kospi_history.extend(kospi_prices[-60:])  # 최근 60일
            
            if kosdaq_prices:
                self._kosdaq_history.clear()
                self._kosdaq_history.extend(kosdaq_prices[-60:])
            
            # 이동평균 재계산
            self._calculate_moving_averages()
            self._calculate_trend()
            
            logger.info(f"히스토리 초기화 완료 (코스피: {len(self._kospi_history)}일)")
    
    # =========================================================================
    # 통계 및 유틸리티
    # =========================================================================
    
    def get_stats(self) -> Dict:
        """통계 조회"""
        return {
            **self._stats,
            'is_running': self._running,
            'update_interval': self.update_interval,
            'history_length': len(self._kospi_history),
        }
    
    def get_summary(self) -> str:
        """상태 요약 문자열"""
        state = self.get_state()
        
        return (
            f"📊 시장 상태: {state.mode.value}\n"
            f"코스피: {state.kospi_price:,.2f} ({state.kospi_change:+.2f}%)\n"
            f"코스닥: {state.kosdaq_price:,.2f} ({state.kosdaq_change:+.2f}%)\n"
            f"MA20 위: {'예' if state.above_ma20 else '아니오'}\n"
            f"5일 추세: {state.trend_5day:+.2f}% ({state.trend_direction})\n"
            f"마지막 업데이트: {state.last_update.strftime('%H:%M:%S')}"
        )


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
    print("MarketMonitor 테스트")
    print("=" * 60)
    
    # 가짜 브로커 (테스트용)
    class MockBroker:
        def __init__(self):
            self.kospi_change = 0.5
        
        def get_index_price(self, code):
            if code == '0001':
                return {
                    'price': 2650.0,
                    'change': 12.5,
                    'change_pct': self.kospi_change,
                }
            else:
                return {
                    'price': 850.0,
                    'change': 3.2,
                    'change_pct': 0.4,
                }
    
    mock_broker = MockBroker()
    
    # 모드 변경 콜백
    def on_mode_change(old, new):
        print(f"🔔 모드 변경 콜백: {old.value} → {new.value}")
    
    # 모니터 생성
    monitor = MarketMonitor(
        mock_broker,
        update_interval=3,
        emergency_threshold=-2.0,
        on_mode_change=on_mode_change
    )
    
    # 히스토리 초기화 (테스트용)
    fake_history = [2600 + i * 2 for i in range(60)]
    monitor.initialize_history(fake_history)
    
    # 시작
    print("\n1. 모니터 시작...")
    monitor.start()
    time.sleep(1)
    
    # 상태 조회
    print("\n2. 현재 상태:")
    state = monitor.get_state()
    print(f"   모드: {state.mode.value}")
    print(f"   코스피: {state.kospi_price:,.2f} ({state.kospi_change:+.2f}%)")
    print(f"   MA20 위: {state.above_ma20}")
    
    # 시뮬레이션: 급락
    print("\n3. 급락 시뮬레이션 (코스피 -2.5%)...")
    mock_broker.kospi_change = -2.5
    time.sleep(4)  # 업데이트 대기
    
    state = monitor.get_state()
    print(f"   모드: {state.mode.value}")
    print(f"   사유: {state.mode_reason}")
    
    # 시뮬레이션: 회복
    print("\n4. 회복 시뮬레이션 (코스피 -0.5%)...")
    mock_broker.kospi_change = -0.5
    time.sleep(4)
    
    state = monitor.get_state()
    print(f"   모드: {state.mode.value}")
    
    # 요약
    print("\n5. 상태 요약:")
    print(monitor.get_summary())
    
    # 통계
    print("\n6. 통계:")
    stats = monitor.get_stats()
    for key, value in stats.items():
        print(f"   {key}: {value}")
    
    # 중지
    print("\n7. 모니터 중지...")
    monitor.stop()
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
