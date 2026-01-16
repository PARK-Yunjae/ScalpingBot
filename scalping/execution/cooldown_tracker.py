#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Cooldown Tracker (쿨타임 관리)
============================================================================
종목별 재매수 쿨타임을 관리하여 급한 재진입 방지

핵심 기능:
- 종목별 쿨타임 설정/확인
- 매도 후 일정 시간 동안 재매수 금지
- 손절 후 더 긴 쿨타임 적용
- 연속 손절 시 추가 페널티

쿨타임 규칙:
- 기본 쿨타임: 10분
- 손절 후: 20분
- 연속 손절 시: +10분씩 추가 (최대 60분)

사용법:
    tracker = CooldownTracker()
    
    # 쿨타임 설정
    tracker.set_cooldown("005930", is_loss=True)
    
    # 매수 가능 여부 확인
    if tracker.can_buy("005930"):
        # 매수 진행
============================================================================
"""

import logging
import threading
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

# 로거 설정
logger = logging.getLogger('ScalpingBot.Cooldown')


# =============================================================================
# 상수 설정
# =============================================================================

# 기본 쿨타임 (분)
DEFAULT_COOLDOWN_MINUTES = 10

# 손절 후 쿨타임 (분)
LOSS_COOLDOWN_MINUTES = 20

# 연속 손절 추가 쿨타임 (분)
CONSECUTIVE_LOSS_PENALTY = 10

# 최대 쿨타임 (분)
MAX_COOLDOWN_MINUTES = 60


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class CooldownInfo:
    """쿨타임 정보"""
    stock_code: str
    cooldown_until: datetime
    reason: str
    consecutive_losses: int = 0
    
    def is_active(self) -> bool:
        """쿨타임 활성 여부"""
        return datetime.now() < self.cooldown_until
    
    def remaining_seconds(self) -> float:
        """남은 시간 (초)"""
        delta = self.cooldown_until - datetime.now()
        return max(0, delta.total_seconds())
    
    def remaining_minutes(self) -> float:
        """남은 시간 (분)"""
        return self.remaining_seconds() / 60


# =============================================================================
# 쿨다운 트래커 클래스
# =============================================================================

class CooldownTracker:
    """
    종목별 쿨타임 관리자
    
    매도 후 일정 시간 동안 재매수를 방지합니다.
    손절 시에는 더 긴 쿨타임을 적용합니다.
    """
    
    def __init__(
        self,
        default_cooldown: int = DEFAULT_COOLDOWN_MINUTES,
        loss_cooldown: int = LOSS_COOLDOWN_MINUTES,
        max_cooldown: int = MAX_COOLDOWN_MINUTES,
    ):
        """
        초기화
        
        Args:
            default_cooldown: 기본 쿨타임 (분)
            loss_cooldown: 손절 쿨타임 (분)
            max_cooldown: 최대 쿨타임 (분)
        """
        self.default_cooldown = default_cooldown
        self.loss_cooldown = loss_cooldown
        self.max_cooldown = max_cooldown
        
        # 쿨타임 저장소 (stock_code -> CooldownInfo)
        self._cooldowns: Dict[str, CooldownInfo] = {}
        
        # 연속 손절 카운터
        self._consecutive_losses: Dict[str, int] = {}
        
        # 전역 쿨타임 (전체 매수 금지)
        self._global_cooldown: Optional[datetime] = None
        self._global_reason: str = ""
        
        # 스레드 안전
        self._lock = threading.Lock()
        
        logger.info(
            f"CooldownTracker 초기화 "
            f"(기본: {default_cooldown}분, 손절: {loss_cooldown}분)"
        )
    
    # =========================================================================
    # 쿨타임 설정
    # =========================================================================
    
    def set_cooldown(
        self,
        stock_code: str,
        is_loss: bool = False,
        custom_minutes: int = None,
        reason: str = "",
    ):
        """
        쿨타임 설정
        
        Args:
            stock_code: 종목 코드
            is_loss: 손절 여부
            custom_minutes: 커스텀 쿨타임 (분)
            reason: 사유
        """
        with self._lock:
            # 연속 손절 카운터 업데이트
            if is_loss:
                self._consecutive_losses[stock_code] = \
                    self._consecutive_losses.get(stock_code, 0) + 1
            else:
                self._consecutive_losses[stock_code] = 0
            
            consecutive = self._consecutive_losses.get(stock_code, 0)
            
            # 쿨타임 계산
            if custom_minutes is not None:
                cooldown_minutes = custom_minutes
            elif is_loss:
                # 손절: 기본 손절 쿨타임 + 연속 손절 페널티
                penalty = (consecutive - 1) * CONSECUTIVE_LOSS_PENALTY if consecutive > 1 else 0
                cooldown_minutes = min(
                    self.loss_cooldown + penalty,
                    self.max_cooldown
                )
            else:
                cooldown_minutes = self.default_cooldown
            
            cooldown_until = datetime.now() + timedelta(minutes=cooldown_minutes)
            
            # 사유 생성
            if not reason:
                if is_loss:
                    if consecutive > 1:
                        reason = f"손절 (연속 {consecutive}회)"
                    else:
                        reason = "손절"
                else:
                    reason = "익절/청산"
            
            # 쿨타임 저장
            self._cooldowns[stock_code] = CooldownInfo(
                stock_code=stock_code,
                cooldown_until=cooldown_until,
                reason=reason,
                consecutive_losses=consecutive,
            )
            
            logger.info(
                f"쿨타임 설정: {stock_code} → {cooldown_minutes}분 "
                f"({reason}, 연속손절: {consecutive}회)"
            )
    
    def clear_cooldown(self, stock_code: str):
        """
        쿨타임 해제
        
        Args:
            stock_code: 종목 코드
        """
        with self._lock:
            if stock_code in self._cooldowns:
                del self._cooldowns[stock_code]
                logger.info(f"쿨타임 해제: {stock_code}")
    
    def clear_all(self):
        """모든 쿨타임 해제"""
        with self._lock:
            self._cooldowns.clear()
            self._consecutive_losses.clear()
            logger.info("모든 쿨타임 해제")
    
    # =========================================================================
    # 전역 쿨타임
    # =========================================================================
    
    def set_global_cooldown(self, minutes: int, reason: str = "전역 쿨타임"):
        """
        전역 쿨타임 설정 (모든 매수 금지)
        
        연속 손절 등 상황에서 전체 매수를 일시 중지합니다.
        
        Args:
            minutes: 쿨타임 (분)
            reason: 사유
        """
        with self._lock:
            self._global_cooldown = datetime.now() + timedelta(minutes=minutes)
            self._global_reason = reason
            
            logger.warning(f"⚠️ 전역 쿨타임 설정: {minutes}분 ({reason})")
    
    def clear_global_cooldown(self):
        """전역 쿨타임 해제"""
        with self._lock:
            self._global_cooldown = None
            self._global_reason = ""
            logger.info("전역 쿨타임 해제")
    
    def is_global_cooldown_active(self) -> bool:
        """전역 쿨타임 활성 여부"""
        with self._lock:
            if self._global_cooldown is None:
                return False
            return datetime.now() < self._global_cooldown
    
    # =========================================================================
    # 쿨타임 확인
    # =========================================================================
    
    def can_buy(self, stock_code: str) -> bool:
        """
        매수 가능 여부 확인
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            True: 매수 가능, False: 쿨타임 중
        """
        with self._lock:
            # 전역 쿨타임 체크
            if self._global_cooldown and datetime.now() < self._global_cooldown:
                return False
            
            # 종목별 쿨타임 체크
            if stock_code not in self._cooldowns:
                return True
            
            return not self._cooldowns[stock_code].is_active()
    
    def get_cooldown_info(self, stock_code: str) -> Optional[CooldownInfo]:
        """
        쿨타임 정보 조회
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            CooldownInfo 또는 None
        """
        with self._lock:
            info = self._cooldowns.get(stock_code)
            
            if info and info.is_active():
                return info
            
            return None
    
    def get_remaining_time(self, stock_code: str) -> float:
        """
        남은 쿨타임 (분)
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            남은 시간 (분), 없으면 0
        """
        info = self.get_cooldown_info(stock_code)
        
        if info:
            return info.remaining_minutes()
        
        return 0
    
    def get_blocked_reason(self, stock_code: str) -> str:
        """
        매수 차단 사유
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            사유 문자열 (차단 아니면 빈 문자열)
        """
        with self._lock:
            # 전역 쿨타임
            if self._global_cooldown and datetime.now() < self._global_cooldown:
                remaining = (self._global_cooldown - datetime.now()).total_seconds() / 60
                return f"전역 쿨타임 ({self._global_reason}, {remaining:.1f}분 남음)"
            
            # 종목별 쿨타임
            info = self._cooldowns.get(stock_code)
            if info and info.is_active():
                return f"{info.reason} ({info.remaining_minutes():.1f}분 남음)"
            
            return ""
    
    # =========================================================================
    # 조회
    # =========================================================================
    
    def get_active_cooldowns(self) -> List[CooldownInfo]:
        """활성 쿨타임 목록"""
        with self._lock:
            now = datetime.now()
            return [
                info for info in self._cooldowns.values()
                if info.cooldown_until > now
            ]
    
    def get_consecutive_losses(self, stock_code: str) -> int:
        """연속 손절 횟수"""
        with self._lock:
            return self._consecutive_losses.get(stock_code, 0)
    
    def reset_consecutive_losses(self, stock_code: str):
        """연속 손절 카운터 리셋"""
        with self._lock:
            self._consecutive_losses[stock_code] = 0
    
    # =========================================================================
    # 유지보수
    # =========================================================================
    
    def cleanup_expired(self):
        """만료된 쿨타임 정리"""
        with self._lock:
            now = datetime.now()
            expired = [
                code for code, info in self._cooldowns.items()
                if info.cooldown_until <= now
            ]
            
            for code in expired:
                del self._cooldowns[code]
            
            if expired:
                logger.debug(f"만료된 쿨타임 {len(expired)}개 정리")
    
    def get_summary(self) -> str:
        """쿨타임 요약"""
        active = self.get_active_cooldowns()
        
        if not active and not self.is_global_cooldown_active():
            return "⏰ 활성 쿨타임 없음"
        
        lines = ["⏰ 쿨타임 현황"]
        
        if self.is_global_cooldown_active():
            remaining = (self._global_cooldown - datetime.now()).total_seconds() / 60
            lines.append(f"  🌐 전역: {self._global_reason} ({remaining:.1f}분 남음)")
        
        for info in active:
            lines.append(
                f"  • {info.stock_code}: {info.reason} "
                f"({info.remaining_minutes():.1f}분 남음)"
            )
        
        return "\n".join(lines)


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    import time
    
    # 로깅 설정
    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("CooldownTracker 테스트")
    print("=" * 60)
    
    tracker = CooldownTracker(
        default_cooldown=1,   # 테스트용 1분
        loss_cooldown=2,      # 테스트용 2분
    )
    
    # 1. 기본 쿨타임 테스트
    print("\n1. 기본 쿨타임 테스트:")
    print(f"   005930 매수 가능? {tracker.can_buy('005930')}")
    
    tracker.set_cooldown("005930", is_loss=False, reason="익절")
    print(f"   쿨타임 설정 후: {tracker.can_buy('005930')}")
    print(f"   남은 시간: {tracker.get_remaining_time('005930'):.1f}분")
    
    # 2. 손절 쿨타임 테스트
    print("\n2. 손절 쿨타임 테스트:")
    tracker.set_cooldown("000660", is_loss=True)
    print(f"   000660 남은 시간: {tracker.get_remaining_time('000660'):.1f}분")
    
    # 3. 연속 손절 테스트
    print("\n3. 연속 손절 테스트:")
    tracker.set_cooldown("035720", is_loss=True)
    print(f"   1회차: {tracker.get_remaining_time('035720'):.1f}분")
    
    tracker.set_cooldown("035720", is_loss=True)
    print(f"   2회차 (연속): {tracker.get_remaining_time('035720'):.1f}분")
    
    tracker.set_cooldown("035720", is_loss=True)
    print(f"   3회차 (연속): {tracker.get_remaining_time('035720'):.1f}분")
    print(f"   연속 손절 횟수: {tracker.get_consecutive_losses('035720')}")
    
    # 4. 전역 쿨타임 테스트
    print("\n4. 전역 쿨타임 테스트:")
    tracker.set_global_cooldown(1, "연속 손절 5회")
    print(f"   전역 쿨타임 활성: {tracker.is_global_cooldown_active()}")
    print(f"   005930 매수 가능? {tracker.can_buy('005930')}")
    print(f"   신규종목 매수 가능? {tracker.can_buy('123456')}")
    
    # 5. 차단 사유
    print("\n5. 차단 사유:")
    print(f"   005930: {tracker.get_blocked_reason('005930')}")
    print(f"   000660: {tracker.get_blocked_reason('000660')}")
    
    # 6. 요약
    print("\n6. 쿨타임 요약:")
    print(tracker.get_summary())
    
    # 7. 해제 테스트
    print("\n7. 해제 테스트:")
    tracker.clear_global_cooldown()
    tracker.clear_cooldown("005930")
    print(f"   005930 매수 가능? {tracker.can_buy('005930')}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
