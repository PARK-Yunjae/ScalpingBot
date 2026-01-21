#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Kill Switch (비상 정지)
============================================================================
시스템 안전 장치 및 비상 정지 관리

핵심 기능:
- 비상 정지 트리거 (수동/자동)
- 연속 손절 감지 및 대응
- 일일 손실 한도 관리
- 시스템 상태 모니터링
- 자동 청산 실행

비상 정지 조건:
1. 코스피 -2% 이상 급락
2. 연속 손절 5회
3. 일일 손실 -3% 도달
4. 수동 킬스위치 활성화
5. API 연결 실패 3회 연속

사용법:
    kill_switch = KillSwitch(broker, notifier)
    
    # 상태 체크
    if kill_switch.should_stop():
        # 매매 중지
    
    # 손절 기록
    kill_switch.record_loss("005930")
    
    # 강제 정지
    kill_switch.trigger("수동 정지")
============================================================================
"""

import logging
import threading
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime, date
from dataclasses import dataclass, field
from enum import Enum

# 로거 설정
logger = logging.getLogger('ScalpingBot.Safety')


# =============================================================================
# 상수 및 열거형
# =============================================================================

# 기본 안전 한도
DEFAULT_MAX_CONSECUTIVE_LOSSES = 5     # 연속 손절 한도
DEFAULT_MAX_DAILY_LOSS_PCT = -3.0      # 일일 손실 한도 (%)
DEFAULT_MAX_API_ERRORS = 3             # API 에러 한도
DEFAULT_EMERGENCY_KOSPI_CHANGE = -2.0  # 코스피 급락 임계값 (%)


class StopReason(Enum):
    """정지 사유"""
    MANUAL = "수동 정지"
    CONSECUTIVE_LOSSES = "연속 손절"
    DAILY_LOSS_LIMIT = "일일 손실 한도"
    MARKET_CRASH = "시장 급락"
    API_ERROR = "API 에러"
    SYSTEM_ERROR = "시스템 에러"
    SCHEDULED = "예정된 종료"


class SystemState(Enum):
    """시스템 상태"""
    RUNNING = "운영 중"
    PAUSED = "일시 정지"
    STOPPED = "정지됨"
    EMERGENCY = "비상 정지"


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class SafetyStatus:
    """안전 상태 정보"""
    state: SystemState = SystemState.RUNNING
    stop_reason: Optional[StopReason] = None
    stop_message: str = ""
    stop_time: Optional[datetime] = None
    
    # 손실 추적
    consecutive_losses: int = 0
    daily_loss_pct: float = 0.0
    total_trades_today: int = 0
    losses_today: int = 0
    
    # 에러 추적
    api_errors: int = 0
    system_errors: int = 0
    
    # 시장 상태
    market_mode: str = "NORMAL"
    kospi_change: float = 0.0
    
    def to_dict(self) -> Dict:
        """딕셔너리로 변환"""
        return {
            'state': self.state.value,
            'stop_reason': self.stop_reason.value if self.stop_reason else None,
            'stop_message': self.stop_message,
            'stop_time': self.stop_time.isoformat() if self.stop_time else None,
            'consecutive_losses': self.consecutive_losses,
            'daily_loss_pct': self.daily_loss_pct,
            'total_trades_today': self.total_trades_today,
            'losses_today': self.losses_today,
            'api_errors': self.api_errors,
            'system_errors': self.system_errors,
            'market_mode': self.market_mode,
            'kospi_change': self.kospi_change,
        }


# =============================================================================
# Kill Switch 클래스
# =============================================================================

class KillSwitch:
    """
    비상 정지 및 안전 장치
    
    시스템의 안전한 운영을 위한 각종 제한과
    비상 상황 대응을 관리합니다.
    """
    
    def __init__(
        self,
        broker=None,
        notifier=None,
        position_manager=None,
        max_consecutive_losses: int = DEFAULT_MAX_CONSECUTIVE_LOSSES,
        max_daily_loss_pct: float = DEFAULT_MAX_DAILY_LOSS_PCT,
        max_api_errors: int = DEFAULT_MAX_API_ERRORS,
        emergency_kospi_change: float = DEFAULT_EMERGENCY_KOSPI_CHANGE,
        on_emergency: Callable[[StopReason, str], None] = None,
    ):
        """
        초기화
        
        Args:
            broker: KISBroker 인스턴스
            notifier: DiscordNotifier 인스턴스
            position_manager: PositionManager 인스턴스
            max_consecutive_losses: 연속 손절 한도
            max_daily_loss_pct: 일일 손실 한도 (%)
            max_api_errors: API 에러 한도
            emergency_kospi_change: 코스피 급락 임계값 (%)
            on_emergency: 비상 상황 콜백
        """
        self.broker = broker
        self.notifier = notifier
        self.position_manager = position_manager
        
        # 한도 설정
        self.max_consecutive_losses = max_consecutive_losses
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_api_errors = max_api_errors
        self.emergency_kospi_change = emergency_kospi_change
        
        # 콜백
        self.on_emergency = on_emergency
        
        # 상태
        self._status = SafetyStatus()
        self._lock = threading.Lock()
        
        # 종목별 연속 손절 추적
        self._stock_losses: Dict[str, int] = {}
        
        # 일자 추적 (일일 리셋용)
        self._current_date = date.today()
        
        # 초기 자본 (손실률 계산용)
        self._initial_capital: float = 0.0
        
        logger.info(
            f"KillSwitch 초기화 "
            f"(연속손절 한도: {max_consecutive_losses}회, "
            f"일일손실 한도: {max_daily_loss_pct}%)"
        )
    
    # =========================================================================
    # 상태 체크
    # =========================================================================
    
    def should_stop(self) -> bool:
        """
        매매 중지 여부 확인
        
        Returns:
            True: 매매 중지 필요
        """
        with self._lock:
            return self._status.state in (SystemState.STOPPED, SystemState.EMERGENCY)
    
    def should_pause(self) -> bool:
        """
        일시 정지 여부 확인
        
        Returns:
            True: 일시 정지 상태
        """
        with self._lock:
            return self._status.state != SystemState.RUNNING
    
    def get_status(self) -> SafetyStatus:
        """상태 조회"""
        with self._lock:
            return SafetyStatus(
                state=self._status.state,
                stop_reason=self._status.stop_reason,
                stop_message=self._status.stop_message,
                stop_time=self._status.stop_time,
                consecutive_losses=self._status.consecutive_losses,
                daily_loss_pct=self._status.daily_loss_pct,
                total_trades_today=self._status.total_trades_today,
                losses_today=self._status.losses_today,
                api_errors=self._status.api_errors,
                system_errors=self._status.system_errors,
                market_mode=self._status.market_mode,
                kospi_change=self._status.kospi_change,
            )
    
    def get_state(self) -> SystemState:
        """현재 상태"""
        with self._lock:
            return self._status.state
    
    # =========================================================================
    # 비상 정지 트리거
    # =========================================================================
    
    def trigger(
        self,
        reason: StopReason = StopReason.MANUAL,
        message: str = "",
        execute_liquidation: bool = True,
    ):
        """
        비상 정지 트리거
        
        Args:
            reason: 정지 사유
            message: 추가 메시지
            execute_liquidation: 청산 실행 여부
        """
        with self._lock:
            if self._status.state == SystemState.EMERGENCY:
                logger.warning("이미 비상 정지 상태입니다.")
                return
            
            self._status.state = SystemState.EMERGENCY
            self._status.stop_reason = reason
            self._status.stop_message = message or reason.value
            self._status.stop_time = datetime.now()
            
            logger.critical(f"🚨 비상 정지: {reason.value} - {message}")
        
        # 알림 전송
        if self.notifier:
            self.notifier.send_emergency_alert(
                message=f"비상 정지: {reason.value}",
                details=message
            )
        
        # 청산 실행
        if execute_liquidation:
            self._execute_liquidation()
        
        # 콜백 호출
        if self.on_emergency:
            try:
                self.on_emergency(reason, message)
            except Exception as e:
                logger.error(f"비상 콜백 에러: {e}")
    
    def pause(self, reason: str = "일시 정지"):
        """일시 정지"""
        with self._lock:
            if self._status.state == SystemState.RUNNING:
                self._status.state = SystemState.PAUSED
                self._status.stop_message = reason
                logger.warning(f"⏸️ 매매 일시 정지: {reason}")
    
    def resume(self):
        """재개"""
        with self._lock:
            if self._status.state == SystemState.PAUSED:
                self._status.state = SystemState.RUNNING
                self._status.stop_message = ""
                logger.info("▶️ 매매 재개")
    
    def reset(self):
        """상태 리셋 (비상 해제)"""
        with self._lock:
            self._status = SafetyStatus()
            self._stock_losses.clear()
            logger.info("🔄 KillSwitch 상태 리셋")
    
    # =========================================================================
    # 손실 추적
    # =========================================================================
    
    def record_trade(self, is_win: bool, stock_code: str = ""):
        """
        매매 결과 기록
        
        Args:
            is_win: 승리 여부
            stock_code: 종목 코드
        """
        self._check_date_reset()
        
        with self._lock:
            self._status.total_trades_today += 1
            
            if is_win:
                # 승리 시 연속 손절 리셋
                self._status.consecutive_losses = 0
                if stock_code:
                    self._stock_losses[stock_code] = 0
            else:
                # 패배 시 카운트 증가
                self._status.losses_today += 1
                self._status.consecutive_losses += 1
                
                if stock_code:
                    self._stock_losses[stock_code] = \
                        self._stock_losses.get(stock_code, 0) + 1
                
                # 연속 손절 체크
                if self._status.consecutive_losses >= self.max_consecutive_losses:
                    logger.warning(
                        f"연속 손절 {self._status.consecutive_losses}회 도달!"
                    )
                    self._trigger_consecutive_loss()
    
    def record_loss(self, stock_code: str):
        """손절 기록"""
        self.record_trade(is_win=False, stock_code=stock_code)
    
    def record_win(self, stock_code: str):
        """익절 기록"""
        self.record_trade(is_win=True, stock_code=stock_code)
    
    def _trigger_consecutive_loss(self):
        """연속 손절 트리거 → 프로그램 종료"""
        import sys
        import os
        from pathlib import Path
        
        consecutive = self._status.consecutive_losses
        
        # 상세 로그 저장
        self._save_stop_log(
            reason=f"연속 손절 {consecutive}회",
            details=self._get_stop_details()
        )
        
        # 비상 정지 트리거
        self.trigger(
            reason=StopReason.CONSECUTIVE_LOSSES,
            message=f"연속 손절 {consecutive}회 → 프로그램 종료",
            execute_liquidation=False
        )
        
        # Discord 알림
        if self.notifier:
            self.notifier.send_emergency_alert(
                message=f"🛑 연속 손절 {consecutive}회 - 프로그램 종료",
                details=self._get_stop_details()
            )
        
        logger.critical("=" * 60)
        logger.critical(f"🛑 연속 손절 {consecutive}회 - 프로그램 종료")
        logger.critical("=" * 60)
        
        # 프로그램 종료
        sys.exit(1)
    
    def _save_stop_log(self, reason: str, details: str):
        """
        정지 사유 로그 파일 저장
        
        logs/stop_YYYYMMDD_HHMMSS.log 형식으로 저장
        """
        from pathlib import Path
        
        try:
            log_dir = Path("logs")
            log_dir.mkdir(exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = log_dir / f"stop_{timestamp}.log"
            
            content = [
                "=" * 60,
                f"ScalpingBot 정지 로그",
                f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "=" * 60,
                "",
                f"정지 사유: {reason}",
                "",
                "상세 내용:",
                "-" * 40,
                details,
                "",
                "=" * 60,
            ]
            
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write("\n".join(content))
            
            logger.info(f"정지 로그 저장: {log_file}")
            
        except Exception as e:
            logger.error(f"정지 로그 저장 실패: {e}")
    
    def _get_stop_details(self) -> str:
        """정지 상세 정보"""
        status = self._status
        
        lines = [
            f"연속 손절: {status.consecutive_losses}회",
            f"오늘 총 거래: {status.total_trades_today}회",
            f"오늘 손실 횟수: {status.losses_today}회",
            f"일일 손익률: {status.daily_loss_pct:+.2f}%",
            f"코스피 등락률: {status.kospi_change:+.2f}%",
            f"시장 모드: {status.market_mode}",
            "",
            "종목별 손절 횟수:",
        ]
        
        for code, count in self._stock_losses.items():
            lines.append(f"  - {code}: {count}회")
        
        return "\n".join(lines)
    
    # =========================================================================
    # 일일 손실 추적
    # =========================================================================
    
    def set_initial_capital(self, capital: float):
        """초기 자본 설정"""
        self._initial_capital = capital
        logger.info(f"초기 자본 설정: {capital:,.0f}원")
    
    def update_daily_pnl(self, current_capital: float):
        """
        일일 손익 업데이트
        
        Args:
            current_capital: 현재 자본
        """
        if self._initial_capital <= 0:
            return
        
        with self._lock:
            self._status.daily_loss_pct = (
                (current_capital - self._initial_capital) 
                / self._initial_capital * 100
            )
            
            # 일일 손실 한도 체크
            if self._status.daily_loss_pct <= self.max_daily_loss_pct:
                logger.warning(
                    f"일일 손실 한도 도달: {self._status.daily_loss_pct:.2f}%"
                )
                self.trigger(
                    reason=StopReason.DAILY_LOSS_LIMIT,
                    message=f"일일 손실 {self._status.daily_loss_pct:.2f}%",
                    execute_liquidation=True
                )
    
    # =========================================================================
    # 시장 상태 체크
    # =========================================================================
    
    def check_market_condition(self, kospi_change: float, mode: str):
        """
        시장 상태 체크
        
        Args:
            kospi_change: 코스피 등락률 (%)
            mode: 시장 모드
        """
        with self._lock:
            self._status.kospi_change = kospi_change
            self._status.market_mode = mode
            
            # 급락 체크
            if kospi_change <= self.emergency_kospi_change:
                if self._status.state != SystemState.EMERGENCY:
                    logger.warning(f"코스피 급락 감지: {kospi_change:.2f}%")
                    
                    # 비상 정지는 트리거하지 않고 일시 정지만
                    # (급락 후 반등 가능성)
                    self.pause(f"코스피 급락 ({kospi_change:.2f}%)")
    
    # =========================================================================
    # 에러 추적
    # =========================================================================
    
    def record_api_error(self):
        """API 에러 기록"""
        with self._lock:
            self._status.api_errors += 1
            
            if self._status.api_errors >= self.max_api_errors:
                logger.error(f"API 에러 {self._status.api_errors}회 연속!")
                self.trigger(
                    reason=StopReason.API_ERROR,
                    message=f"API 에러 {self._status.api_errors}회",
                    execute_liquidation=False
                )
    
    def clear_api_errors(self):
        """API 에러 카운터 초기화"""
        with self._lock:
            self._status.api_errors = 0
    
    def record_system_error(self, error: str):
        """시스템 에러 기록"""
        with self._lock:
            self._status.system_errors += 1
            logger.error(f"시스템 에러 #{self._status.system_errors}: {error}")
    
    # =========================================================================
    # 청산 실행
    # =========================================================================
    
    def _execute_liquidation(self):
        """전량 청산 실행"""
        logger.warning("🔥 전량 청산 시작...")
        
        if not self.broker:
            logger.error("브로커가 설정되지 않음, 청산 불가")
            return
        
        try:
            # 미체결 주문 취소
            cancelled = self.broker.cancel_all_pending_orders()
            logger.info(f"미체결 주문 {cancelled}건 취소")
            
            # 보유 종목 시장가 매도
            positions = self.broker.get_positions()
            
            for pos in positions:
                result = self.broker.sell_market(pos.stock_code, pos.quantity)
                
                if result.success:
                    logger.info(f"✅ 청산 완료: {pos.stock_code} {pos.quantity}주")
                else:
                    logger.error(f"❌ 청산 실패: {pos.stock_code} - {result.error}")
            
            logger.warning("🔥 전량 청산 완료")
            
            # 알림
            if self.notifier:
                self.notifier.send_warning(
                    f"전량 청산 완료: {len(positions)}개 종목"
                )
        
        except Exception as e:
            logger.exception(f"청산 중 에러: {e}")
    
    def liquidate_all(self):
        """전량 청산 (외부 호출용)"""
        self._execute_liquidation()
    
    # =========================================================================
    # 날짜 리셋
    # =========================================================================
    
    def _check_date_reset(self):
        """날짜 변경 시 일일 통계 리셋"""
        today = date.today()
        
        if today != self._current_date:
            with self._lock:
                self._current_date = today
                self._status.total_trades_today = 0
                self._status.losses_today = 0
                self._status.daily_loss_pct = 0.0
                self._status.consecutive_losses = 0
                self._stock_losses.clear()
                
                # 비상 상태가 아니면 정상으로
                if self._status.state != SystemState.EMERGENCY:
                    self._status.state = SystemState.RUNNING
                
                logger.info("📅 새로운 거래일 - 일일 통계 리셋")
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def get_summary(self) -> str:
        """상태 요약"""
        status = self.get_status()
        
        lines = [
            "🛡️ 안전 장치 상태",
            "-" * 40,
            f"상태: {status.state.value}",
            f"연속 손절: {status.consecutive_losses}/{self.max_consecutive_losses}",
            f"일일 손실: {status.daily_loss_pct:.2f}%/{self.max_daily_loss_pct}%",
            f"오늘 매매: {status.total_trades_today}건 (손실: {status.losses_today}건)",
            f"API 에러: {status.api_errors}",
            f"코스피: {status.kospi_change:+.2f}%",
        ]
        
        if status.stop_reason:
            lines.append(f"정지 사유: {status.stop_reason.value}")
            lines.append(f"메시지: {status.stop_message}")
        
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
    print("KillSwitch 테스트")
    print("=" * 60)
    
    # 콜백 함수
    def on_emergency(reason, message):
        print(f"🚨 콜백 호출: {reason.value} - {message}")
    
    # KillSwitch 생성
    ks = KillSwitch(
        max_consecutive_losses=3,  # 테스트용 3회
        max_daily_loss_pct=-2.0,   # 테스트용 -2%
        on_emergency=on_emergency
    )
    
    # 1. 기본 상태 체크
    print("\n1. 기본 상태:")
    print(f"   매매 중지: {ks.should_stop()}")
    print(f"   상태: {ks.get_state().value}")
    
    # 2. 손실 기록 테스트
    print("\n2. 연속 손절 테스트:")
    ks.record_loss("005930")
    print(f"   1회 손절 후: 연속 {ks.get_status().consecutive_losses}회")
    
    ks.record_loss("000660")
    print(f"   2회 손절 후: 연속 {ks.get_status().consecutive_losses}회")
    
    ks.record_loss("035720")
    print(f"   3회 손절 후: 연속 {ks.get_status().consecutive_losses}회")
    print(f"   상태: {ks.get_state().value}")
    
    # 3. 리셋
    print("\n3. 리셋:")
    ks.reset()
    print(f"   리셋 후 상태: {ks.get_state().value}")
    
    # 4. 일시 정지 테스트
    print("\n4. 일시 정지 테스트:")
    ks.pause("테스트 정지")
    print(f"   정지 후: {ks.should_pause()}")
    
    ks.resume()
    print(f"   재개 후: {ks.should_pause()}")
    
    # 5. 시장 상태 체크
    print("\n5. 시장 상태 체크:")
    ks.check_market_condition(-1.5, "CONSERVATIVE")
    print(f"   코스피 -1.5%: {ks.get_state().value}")
    
    ks.reset()
    ks.check_market_condition(-2.5, "EMERGENCY")
    print(f"   코스피 -2.5%: {ks.get_state().value}")
    
    # 6. 요약
    print("\n6. 상태 요약:")
    print(ks.get_summary())
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
