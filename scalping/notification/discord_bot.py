#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Discord Notifier (Discord 알림)
============================================================================
Discord 웹훅을 통한 실시간 알림 및 리포트 전송

핵심 기능:
- 매수/매도 알림
- 손절/익절 알림
- 일일 리포트 전송
- 비상 상황 경고
- 시스템 상태 알림

사용법:
    notifier = DiscordNotifier(webhook_url)
    
    # 매수 알림
    notifier.send_buy_signal("005930", "삼성전자", 70000, 10, score=85)
    
    # 일일 리포트
    notifier.send_daily_report(stats)
============================================================================
"""

import logging
import threading
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass
from queue import Queue, Empty
from enum import Enum

# 로거 설정
logger = logging.getLogger('ScalpingBot.Discord')


# =============================================================================
# 상수 및 열거형
# =============================================================================

class AlertLevel(Enum):
    """알림 레벨"""
    INFO = 0x3498db      # 파랑
    SUCCESS = 0x2ecc71   # 초록
    WARNING = 0xf39c12   # 노랑
    ERROR = 0xe74c3c     # 빨강
    CRITICAL = 0x9b59b6  # 보라


# =============================================================================
# Discord 알림 클래스
# =============================================================================

class DiscordNotifier:
    """
    Discord 웹훅 알림
    
    비동기 큐 방식으로 알림을 전송합니다.
    """
    
    def __init__(
        self,
        webhook_url: str,
        bot_name: str = "ScalpingBot v2.4",
        enabled: bool = True,
        rate_limit_seconds: float = 1.0,
    ):
        """
        초기화
        
        Args:
            webhook_url: Discord 웹훅 URL
            bot_name: 봇 이름
            enabled: 알림 활성화 여부
            rate_limit_seconds: 알림 간격 (초)
        """
        self.webhook_url = webhook_url
        self.bot_name = bot_name
        self.enabled = enabled
        self.rate_limit = rate_limit_seconds
        
        # 알림 큐 (비동기 전송)
        self._queue: Queue = Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # 통계
        self._stats = {
            'total_sent': 0,
            'errors': 0,
        }
        
        # 마지막 전송 시간 (rate limit용)
        self._last_sent = 0
        
        logger.info(f"DiscordNotifier 초기화 (활성: {enabled})")
    
    # =========================================================================
    # 시작/중지
    # =========================================================================
    
    def start(self):
        """알림 전송 스레드 시작"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(
            target=self._send_loop,
            name="Discord-Notifier",
            daemon=True
        )
        self._thread.start()
        
        logger.info("📢 Discord 알림 스레드 시작")
    
    def stop(self):
        """알림 전송 스레드 중지"""
        self._running = False
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        
        logger.info("🛑 Discord 알림 스레드 중지")
    
    def _send_loop(self):
        """알림 전송 루프"""
        import time
        
        while self._running:
            try:
                # 큐에서 알림 가져오기 (1초 타임아웃)
                payload = self._queue.get(timeout=1.0)
                
                # Rate limit 적용
                elapsed = time.time() - self._last_sent
                if elapsed < self.rate_limit:
                    time.sleep(self.rate_limit - elapsed)
                
                # 전송
                self._send_webhook(payload)
                self._last_sent = time.time()
                
            except Empty:
                continue
            except Exception as e:
                logger.error(f"Discord 전송 에러: {e}")
                self._stats['errors'] += 1
    
    def _send_webhook(self, payload: Dict):
        """웹훅 전송"""
        if not self.enabled or not self.webhook_url:
            return
        
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code in (200, 204):
                self._stats['total_sent'] += 1
            else:
                logger.warning(f"Discord 응답 에러: {response.status_code}")
                self._stats['errors'] += 1
        
        except Exception as e:
            logger.error(f"Discord 전송 실패: {e}")
            self._stats['errors'] += 1
    
    # =========================================================================
    # 임베드 빌더
    # =========================================================================
    
    def _build_embed(
        self,
        title: str,
        description: str = "",
        color: int = AlertLevel.INFO.value,
        fields: List[Dict] = None,
        footer: str = None,
    ) -> Dict:
        """임베드 메시지 빌드"""
        embed = {
            "title": title,
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        if description:
            embed["description"] = description
        
        if fields:
            embed["fields"] = fields
        
        if footer:
            embed["footer"] = {"text": footer}
        
        return {
            "username": self.bot_name,
            "embeds": [embed]
        }
    
    def _queue_message(self, payload: Dict):
        """큐에 메시지 추가"""
        if self.enabled:
            self._queue.put(payload)
    
    # =========================================================================
    # 매매 알림
    # =========================================================================
    
    def send_buy_signal(
        self,
        stock_code: str,
        stock_name: str,
        price: float,
        quantity: int,
        score: float = 0,
        ai_confidence: float = 0,
        grade: str = "C",
    ):
        """
        매수 신호 알림
        """
        amount = price * quantity
        
        fields = [
            {"name": "종목코드", "value": stock_code, "inline": True},
            {"name": "매수가", "value": f"{price:,.0f}원", "inline": True},
            {"name": "수량", "value": f"{quantity}주", "inline": True},
            {"name": "금액", "value": f"{amount:,.0f}원", "inline": True},
            {"name": "점수", "value": f"{score:.1f}점 ({grade}등급)", "inline": True},
            {"name": "AI 신뢰도", "value": f"{ai_confidence*100:.0f}%", "inline": True},
        ]
        
        payload = self._build_embed(
            title=f"📈 매수 | {stock_name}",
            color=AlertLevel.SUCCESS.value,
            fields=fields,
        )
        
        self._queue_message(payload)
        logger.info(f"📢 매수 알림: {stock_code} {stock_name}")
    
    def send_sell_signal(
        self,
        stock_code: str,
        stock_name: str,
        price: float,
        quantity: int,
        profit_pct: float,
        reason: str,
    ):
        """
        매도 신호 알림
        """
        amount = price * quantity
        
        # 수익/손실에 따른 색상
        if profit_pct >= 0:
            color = AlertLevel.SUCCESS.value
            emoji = "🟢"
        else:
            color = AlertLevel.ERROR.value
            emoji = "🔴"
        
        fields = [
            {"name": "종목코드", "value": stock_code, "inline": True},
            {"name": "매도가", "value": f"{price:,.0f}원", "inline": True},
            {"name": "수량", "value": f"{quantity}주", "inline": True},
            {"name": "금액", "value": f"{amount:,.0f}원", "inline": True},
            {"name": "수익률", "value": f"{emoji} {profit_pct:+.2f}%", "inline": True},
            {"name": "사유", "value": reason, "inline": True},
        ]
        
        payload = self._build_embed(
            title=f"📉 매도 | {stock_name}",
            color=color,
            fields=fields,
        )
        
        self._queue_message(payload)
        logger.info(f"📢 매도 알림: {stock_code} {stock_name} ({profit_pct:+.2f}%)")
    
    # =========================================================================
    # 시스템 알림
    # =========================================================================
    
    def send_system_start(self, config_summary: str = ""):
        """시스템 시작 알림"""
        payload = self._build_embed(
            title="🚀 ScalpingBot 시작",
            description=f"트레이딩 시스템이 시작되었습니다.\n\n{config_summary}",
            color=AlertLevel.INFO.value,
            footer=f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        )
        
        self._queue_message(payload)
    
    def send_system_stop(self, reason: str = "정상 종료"):
        """시스템 종료 알림"""
        payload = self._build_embed(
            title="🛑 ScalpingBot 종료",
            description=f"사유: {reason}",
            color=AlertLevel.WARNING.value,
            footer=f"종료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        )
        
        self._queue_message(payload)
    
    def send_emergency_alert(self, message: str, details: str = ""):
        """비상 상황 알림"""
        payload = self._build_embed(
            title="🚨 비상 경고",
            description=f"**{message}**\n\n{details}",
            color=AlertLevel.CRITICAL.value,
        )
        
        self._queue_message(payload)
        logger.warning(f"🚨 비상 알림: {message}")
    
    def send_market_mode_change(self, old_mode: str, new_mode: str, reason: str):
        """시장 모드 변경 알림"""
        # 모드별 색상
        mode_colors = {
            "NORMAL": AlertLevel.SUCCESS.value,
            "CONSERVATIVE": AlertLevel.WARNING.value,
            "EMERGENCY": AlertLevel.CRITICAL.value,
        }
        
        payload = self._build_embed(
            title="⚠️ 시장 모드 변경",
            description=f"{old_mode} → **{new_mode}**\n\n사유: {reason}",
            color=mode_colors.get(new_mode, AlertLevel.INFO.value),
        )
        
        self._queue_message(payload)
    
    # =========================================================================
    # 일일 리포트
    # =========================================================================
    
    def send_daily_report(
        self,
        date: str,
        total_trades: int,
        wins: int,
        losses: int,
        total_profit: float,
        total_profit_pct: float,
        best_trade: Dict = None,
        worst_trade: Dict = None,
        ai_stats: Dict = None,
    ):
        """일일 리포트 전송"""
        winrate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        # 성과에 따른 색상
        if total_profit_pct >= 1.0:
            color = AlertLevel.SUCCESS.value
            emoji = "🎉"
        elif total_profit_pct >= 0:
            color = AlertLevel.INFO.value
            emoji = "✅"
        else:
            color = AlertLevel.ERROR.value
            emoji = "😢"
        
        fields = [
            {"name": "총 매매", "value": f"{total_trades}건", "inline": True},
            {"name": "승/패", "value": f"{wins}승 {losses}패", "inline": True},
            {"name": "승률", "value": f"{winrate:.1f}%", "inline": True},
            {"name": "총 수익", "value": f"{total_profit:+,.0f}원", "inline": True},
            {"name": "수익률", "value": f"{total_profit_pct:+.2f}%", "inline": True},
        ]
        
        # 최고/최저 매매
        if best_trade:
            fields.append({
                "name": "🏆 최고 매매",
                "value": f"{best_trade.get('name', '')} ({best_trade.get('profit', 0):+.2f}%)",
                "inline": True
            })
        
        if worst_trade:
            fields.append({
                "name": "💔 최저 매매",
                "value": f"{worst_trade.get('name', '')} ({worst_trade.get('profit', 0):+.2f}%)",
                "inline": True
            })
        
        # AI 통계
        if ai_stats:
            fields.append({
                "name": "🤖 AI 분석",
                "value": f"{ai_stats.get('total', 0)}건 (평균 신뢰도: {ai_stats.get('avg_confidence', 0)*100:.0f}%)",
                "inline": False
            })
        
        payload = self._build_embed(
            title=f"{emoji} 일일 리포트 | {date}",
            color=color,
            fields=fields,
            footer="ScalpingBot v2.4",
        )
        
        self._queue_message(payload)
        logger.info(f"📢 일일 리포트 전송: {date}")
    
    # =========================================================================
    # 포지션 현황 알림
    # =========================================================================
    
    def send_position_status(
        self,
        positions: List[Dict],
        total_profit_pct: float = 0.0,
    ):
        """
        포지션 현황 알림 (주기적 모니터링용)
        
        Args:
            positions: 포지션 리스트 [{stock_code, stock_name, profit_pct, quantity, current_price}, ...]
            total_profit_pct: 전체 수익률
        """
        if not positions:
            # 포지션이 없으면 알림 안보냄
            return
        
        # 색상 결정
        if total_profit_pct >= 0.5:
            color = AlertLevel.SUCCESS.value
            emoji = "📈"
        elif total_profit_pct >= 0:
            color = AlertLevel.INFO.value
            emoji = "📊"
        else:
            color = AlertLevel.WARNING.value
            emoji = "📉"
        
        # 포지션 목록 문자열
        pos_lines = []
        for pos in positions:
            profit_emoji = "🟢" if pos.get('profit_pct', 0) >= 0 else "🔴"
            pos_lines.append(
                f"{profit_emoji} **{pos.get('stock_name', '')}** ({pos.get('stock_code', '')})\n"
                f"   {pos.get('profit_pct', 0):+.2f}% | {pos.get('quantity', 0)}주 @ {pos.get('current_price', 0):,.0f}원"
            )
        
        description = "\n".join(pos_lines)
        
        fields = [
            {"name": "보유 종목", "value": f"{len(positions)}개", "inline": True},
            {"name": "전체 수익률", "value": f"{total_profit_pct:+.2f}%", "inline": True},
        ]
        
        payload = self._build_embed(
            title=f"{emoji} 포지션 현황",
            description=description,
            color=color,
            fields=fields,
            footer=f"업데이트: {datetime.now().strftime('%H:%M:%S')}",
        )
        
        self._queue_message(payload)
        logger.info(f"📢 포지션 현황 알림: {len(positions)}개 종목")
    
    # =========================================================================
    # 간단한 메시지
    # =========================================================================
    
    def send_info(self, message: str):
        """정보 메시지"""
        payload = self._build_embed(
            title="ℹ️ 정보",
            description=message,
            color=AlertLevel.INFO.value,
        )
        self._queue_message(payload)
    
    def send_message(self, title: str, message: str = ""):
        """
        일반 메시지 전송
        
        Args:
            title: 메시지 제목
            message: 메시지 내용 (선택)
        """
        payload = self._build_embed(
            title=title,
            description=message,
            color=AlertLevel.INFO.value,
        )
        self._queue_message(payload)
    
    def send_warning(self, message: str):
        """경고 메시지"""
        payload = self._build_embed(
            title="⚠️ 경고",
            description=message,
            color=AlertLevel.WARNING.value,
        )
        self._queue_message(payload)
    
    def send_error(self, message: str):
        """에러 메시지"""
        payload = self._build_embed(
            title="❌ 에러",
            description=message,
            color=AlertLevel.ERROR.value,
        )
        self._queue_message(payload)
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def test_webhook(self) -> bool:
        """웹훅 연결 테스트"""
        try:
            payload = self._build_embed(
                title="🔔 웹훅 테스트",
                description="Discord 연결이 정상입니다.",
                color=AlertLevel.INFO.value,
            )
            
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            
            success = response.status_code in (200, 204)
            
            if success:
                logger.info("✅ Discord 웹훅 테스트 성공")
            else:
                logger.error(f"❌ Discord 웹훅 테스트 실패: {response.status_code}")
            
            return success
        
        except Exception as e:
            logger.error(f"❌ Discord 웹훅 테스트 에러: {e}")
            return False
    
    def get_stats(self) -> Dict:
        """통계 조회"""
        return {
            **self._stats,
            'queue_size': self._queue.qsize(),
            'enabled': self.enabled,
        }


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
    print("DiscordNotifier 테스트 (웹훅 없이)")
    print("=" * 60)
    
    # 가짜 URL로 테스트 (실제 전송 안됨)
    notifier = DiscordNotifier(
        webhook_url="https://discord.com/api/webhooks/test/test",
        enabled=False  # 실제 전송 비활성화
    )
    
    # 알림 스레드 시작
    notifier.start()
    
    # 1. 매수 알림 테스트
    print("\n1. 매수 알림 테스트...")
    notifier.send_buy_signal(
        stock_code="005930",
        stock_name="삼성전자",
        price=70000,
        quantity=10,
        score=85,
        ai_confidence=0.78,
        grade="A"
    )
    print("   ✅ 매수 알림 큐 추가")
    
    # 2. 매도 알림 테스트
    print("\n2. 매도 알림 테스트...")
    notifier.send_sell_signal(
        stock_code="005930",
        stock_name="삼성전자",
        price=70700,
        quantity=10,
        profit_pct=1.0,
        reason="익절"
    )
    print("   ✅ 매도 알림 큐 추가")
    
    # 3. 시스템 알림 테스트
    print("\n3. 시스템 알림 테스트...")
    notifier.send_system_start("dry_run=True, max_positions=5")
    notifier.send_market_mode_change("NORMAL", "CONSERVATIVE", "코스피 MA20 하회")
    notifier.send_emergency_alert("연속 손절 5회 도달", "매매 일시 중지")
    print("   ✅ 시스템 알림 큐 추가")
    
    # 4. 일일 리포트 테스트
    print("\n4. 일일 리포트 테스트...")
    notifier.send_daily_report(
        date="2025-01-16",
        total_trades=15,
        wins=10,
        losses=5,
        total_profit=150000,
        total_profit_pct=1.5,
        best_trade={"name": "삼성전자", "profit": 2.5},
        worst_trade={"name": "SK하이닉스", "profit": -1.2},
        ai_stats={"total": 30, "avg_confidence": 0.75}
    )
    print("   ✅ 일일 리포트 큐 추가")
    
    # 5. 통계 확인
    print("\n5. 통계:")
    stats = notifier.get_stats()
    for key, value in stats.items():
        print(f"   {key}: {value}")
    
    # 정리
    notifier.stop()
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)