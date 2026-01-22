#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v3.0 - Scalp Engine (스캘핑 전용 엔진)
============================================================================
분봉 기반 규칙 매매 엔진

핵심 특징:
1. AI 진입 판단 제거 → 규칙 기반 시그널 (scalp_signals.py)
2. 분봉 지표 사용 → minute_indicators.py
3. 빠른 손절/익절 → 시간 손절 포함
4. 프리마켓 분석 결과 활용

사용법:
    engine = ScalpEngine(config_path='config/config.yaml')
    engine.run()

타임라인:
    08:00 - 프리마켓 분석 시작
    08:30 - AI 유니버스 선정
    09:05 - 스캘핑 매매 시작
    14:50 - 전량 청산
    15:35 - 일일 리포트
============================================================================
"""

import os
import sys
import time
import signal
import logging
import threading
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import deque
import yaml

# 상위 디렉토리 import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# 내부 모듈
from scalping.config.config_loader import ConfigLoader
from scalping.execution.broker import KISBroker
from scalping.execution.position_manager import (
    PositionManager, SellReason, PROFIT_TARGETS
)
from scalping.data.market_monitor import MarketMonitor, MarketMode
from scalping.data.stock_mapper import StockMapper
from scalping.data.premarket_analyzer import PreMarketAnalyzer, PreMarketResult
from scalping.strategy.minute_indicators import MinuteIndicators, OHLCV
from scalping.strategy.scalp_signals import (
    ScalpSignalGenerator, ScalpSignal, SignalType, MarketContext
)
from scalping.safety.kill_switch import KillSwitch
from scalping.execution.cooldown_tracker import CooldownTracker
from scalping.notification.discord_bot import DiscordNotifier
from scalping.ai.ai_engine import AIEngine

logger = logging.getLogger('ScalpingBot.ScalpEngine')


# =============================================================================
# 상수
# =============================================================================

CANDLE_INTERVAL = 5           # 봉 간격 (분)
SCAN_INTERVAL = 5             # 스캔 간격 (초)
POSITION_CHECK_INTERVAL = 1   # 포지션 체크 간격 (초)


# =============================================================================
# 종목별 분봉 트래커
# =============================================================================

@dataclass
class StockTracker:
    """종목별 분봉 추적"""
    code: str
    name: str
    prev_close: float = 0.0
    
    # 분봉 지표 계산기
    indicators: MinuteIndicators = None
    
    # AI 분석 결과 (프리마켓)
    ai_score: float = 0.0
    scenarios: Dict[str, str] = field(default_factory=dict)
    
    # 마지막 시그널
    last_signal: Optional[ScalpSignal] = None
    last_signal_time: datetime = None
    
    def __post_init__(self):
        if self.indicators is None:
            self.indicators = MinuteIndicators(prev_close=self.prev_close)


# =============================================================================
# 스캘핑 엔진
# =============================================================================

class ScalpEngine:
    """
    스캘핑 전용 매매 엔진
    
    프리마켓 분석 → 규칙 기반 매매 → 빠른 손절/익절
    """
    
    def __init__(
        self,
        config_path: str = 'config/config.yaml',
        secrets_path: str = 'config/secrets.yaml',
    ):
        """
        Args:
            config_path: 설정 파일 경로
            secrets_path: 시크릿 파일 경로
        """
        # 설정 로드
        self.config_loader = ConfigLoader(config_path)
        self.config = self.config_loader.load()
        
        # 시크릿 로드
        self.secrets = self._load_secrets(secrets_path)
        
        # 운영 모드
        self.mode = self.config.get('mode', 'LIVE_DATA_ONLY')
        
        # 핵심 컴포넌트
        self.broker: Optional[KISBroker] = None
        self.position_manager: Optional[PositionManager] = None
        self.market_monitor: Optional[MarketMonitor] = None
        self.kill_switch: Optional[KillSwitch] = None
        self.cooldown_tracker: Optional[CooldownTracker] = None
        self.notifier: Optional[DiscordNotifier] = None
        self.stock_mapper: Optional[StockMapper] = None
        
        # 스캘핑 컴포넌트
        self.premarket_analyzer: Optional[PreMarketAnalyzer] = None
        self.signal_generator: Optional[ScalpSignalGenerator] = None
        self.premarket_result: Optional[PreMarketResult] = None
        self.ai_engine: Optional[AIEngine] = None
        
        # 종목 트래커 (유니버스)
        self._trackers: Dict[str, StockTracker] = {}
        
        # 회피 종목 캐시 (당일 한정, 프리마켓 AI가 지정한 종목)
        self._avoid_codes: set = set()
        self._avoid_names: set = set()  # 종목명으로도 체크
        self._avoid_date: Optional[str] = None  # 회피 목록 생성 날짜
        
        # 설정값 로드
        trading_config = self.config.get('trading', {})
        safety_config = self.config.get('safety', {})
        
        self.stop_loss = trading_config.get('stop_loss', -0.7)
        self.take_profit_1 = trading_config.get('take_profit_1', 1.5)
        self.min_score = trading_config.get('min_score', 55)
        self.min_score_conservative = trading_config.get('min_score_conservative', 65)
        self.max_positions = safety_config.get('max_positions', 3)
        self.max_position_size = safety_config.get('max_position_size', 300000)
        
        # 시간 설정
        self.market_open = self._parse_time(trading_config.get('market_open', '09:05'))
        self.market_close = self._parse_time(trading_config.get('market_close', '14:50'))
        
        # 상태
        self._running = False
        self._today_trades: List[Dict] = []
        self._stats = {
            'scans': 0,
            'signals': 0,
            'buys': 0,
            'sells': 0,
            'wins': 0,
            'losses': 0,
        }
        
        logger.info(f"ScalpEngine 초기화 (모드: {self.mode})")
    
    def _load_secrets(self, path: str) -> Dict:
        """시크릿 파일 로드"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"시크릿 로드 실패: {e}")
            return {}
    
    def _parse_time(self, time_str: str) -> dt_time:
        """시간 문자열 파싱"""
        parts = time_str.split(':')
        return dt_time(int(parts[0]), int(parts[1]))
    
    # =========================================================================
    # 초기화
    # =========================================================================
    
    def initialize(self) -> bool:
        """컴포넌트 초기화"""
        logger.info("=" * 60)
        logger.info("🚀 ScalpEngine 초기화 시작")
        logger.info("=" * 60)
        
        try:
            # 1. 브로커 초기화
            logger.info("\n[1/7] 브로커 초기화...")
            kis_config = self.secrets.get('kis', {})
            self.broker = KISBroker(config=kis_config, dry_run=(self.mode == 'LIVE_DATA_ONLY'))
            
            # 연결 테스트 (잔고 조회)
            try:
                balance = self.broker.get_balance()
                if balance:
                    logger.info(f"   ✅ 브로커 연결 성공 (예수금: {balance.get('available_cash', 0):,.0f}원)")
                else:
                    logger.warning("   ⚠️ 잔고 조회 실패 - 계속 진행")
            except Exception as e:
                logger.warning(f"   ⚠️ 브로커 연결 테스트 실패: {e}")
            logger.info("   ✅ 브로커 초기화 완료")
            
            # 2. 포지션 매니저
            logger.info("\n[2/7] 포지션 매니저 초기화...")
            self.position_manager = PositionManager(
                stop_loss=self.stop_loss
            )
            logger.info("   ✅ 포지션 매니저 초기화 완료")
            
            # 3. 시장 모니터
            logger.info("\n[3/7] 시장 모니터 초기화...")
            self.market_monitor = MarketMonitor(broker=self.broker)
            logger.info("   ✅ 시장 모니터 초기화 완료")
            
            # 4. 안전장치
            logger.info("\n[4/7] 안전장치 초기화...")
            safety_config = self.config.get('safety', {})
            self.kill_switch = KillSwitch(
                max_daily_loss_pct=safety_config.get('max_daily_loss_pct', -3.0),
                max_consecutive_losses=safety_config.get('consecutive_loss_stop', 7),
                rest_after_losses=safety_config.get('consecutive_loss_rest', 3),
                rest_minutes=safety_config.get('rest_minutes', 10),
            )
            self.cooldown_tracker = CooldownTracker()
            logger.info("   ✅ 안전장치 초기화 완료")
            
            # 5. 종목 매퍼
            logger.info("\n[5/7] 종목 매퍼 초기화...")
            self.stock_mapper = StockMapper()
            logger.info("   ✅ 종목 매퍼 초기화 완료")
            
            # 6. 시그널 생성기
            logger.info("\n[6/8] 시그널 생성기 초기화...")
            self.signal_generator = ScalpSignalGenerator(self.config)
            logger.info("   ✅ 시그널 생성기 초기화 완료")
            
            # 7. AI 엔진 (Gemini)
            logger.info("\n[7/8] AI 엔진 초기화...")
            ai_config = self.config.get('ai', {})
            if ai_config.get('use_for_universe', False):
                try:
                    self.ai_engine = AIEngine(
                        config=ai_config,
                        secrets=self.secrets,
                    )
                    logger.info(f"   ✅ AI 엔진 초기화 완료 ({ai_config.get('provider', 'gemini')})")
                except Exception as e:
                    logger.warning(f"   ⚠️ AI 엔진 초기화 실패: {e}")
                    self.ai_engine = None
            else:
                logger.info("   ⏭️ AI 유니버스 선정 비활성화 (use_for_universe: false)")
            
            # 8. 프리마켓 분석기
            logger.info("\n[8/8] 프리마켓 분석기 초기화...")
            self.premarket_analyzer = PreMarketAnalyzer(
                config=self.config,
                broker=self.broker,
                secrets=self.secrets,
                ai_engine=self.ai_engine,
            )
            logger.info("   ✅ 프리마켓 분석기 초기화 완료")
            
            # Discord 알림 (선택적)
            discord_config = self.config.get('discord', {})
            if discord_config.get('enabled'):
                webhook_url = self.secrets.get('discord', {}).get('webhook_url', '')
                if webhook_url:
                    self.notifier = DiscordNotifier(webhook_url=webhook_url)
                    self.notifier.start()  # ★ 스레드 시작 추가
                    logger.info("   ✅ Discord 알림 활성화")
            
            logger.info("\n" + "=" * 60)
            logger.info("✅ 모든 컴포넌트 초기화 완료")
            logger.info("=" * 60)
            
            # 이전 상태 확인
            self._check_previous_state()
            
            return True
            
        except Exception as e:
            logger.error(f"초기화 실패: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _check_previous_state(self):
        """이전 종료 상태 확인"""
        try:
            from pathlib import Path
            import json
            
            state_file = Path('logs') / 'last_state.json'
            if not state_file.exists():
                return
            
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            
            shutdown_time = state.get('shutdown_time', '')
            prev_trades = state.get('stats', {}).get('trades', 0)
            prev_universe = state.get('universe', [])
            
            logger.info("\n📋 이전 세션 정보:")
            logger.info(f"   종료 시간: {shutdown_time}")
            logger.info(f"   거래 횟수: {prev_trades}건")
            logger.info(f"   유니버스: {len(prev_universe)}종목")
            
            # 파일 삭제 (새 세션 시작)
            state_file.unlink()
            logger.info("   → 새 세션 시작")
            
        except Exception as e:
            logger.debug(f"이전 상태 확인 실패: {e}")
    
    def _acquire_pid_lock(self) -> bool:
        """
        PID 락 획득 (중복 실행 방지)
        
        Returns:
            True: 락 획득 성공
            False: 이미 실행 중인 프로세스 존재
        """
        pid_file = Path('logs') / 'scalping.pid'
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        
        if pid_file.exists():
            try:
                with open(pid_file, 'r') as f:
                    old_pid = int(f.read().strip())
                
                # 프로세스 존재 확인
                try:
                    os.kill(old_pid, 0)  # 시그널 0 = 존재 확인만
                    # 프로세스 존재함 → 중복 실행
                    logger.error(f"⚠️ 이미 실행 중인 프로세스 존재: PID {old_pid}")
                    return False
                except OSError:
                    # 프로세스 없음 → 오래된 PID 파일, 계속 진행
                    logger.info(f"🔄 이전 PID {old_pid} 종료됨 - 락 재획득")
            except (ValueError, FileNotFoundError):
                pass
        
        # 새 PID 기록
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
        
        logger.info(f"🔒 PID 락 획득: {os.getpid()}")
        return True
    
    def _release_pid_lock(self):
        """PID 락 해제"""
        pid_file = Path('logs') / 'scalping.pid'
        try:
            if pid_file.exists():
                pid_file.unlink()
                logger.debug("PID 락 해제")
        except Exception as e:
            logger.debug(f"PID 락 해제 실패: {e}")
    
    def _sync_positions_with_broker(self):
        """
        브로커와 포지션 동기화 (재시작 시 불일치 해결)
        
        DB에는 있지만 실제 보유하지 않은 포지션 삭제
        """
        if not self.broker or not self.position_manager:
            return
        
        positions = self.position_manager.get_all_positions()
        if not positions:
            return
        
        logger.info(f"\n🔄 포지션 동기화 시작: {len(positions)}개")
        
        # 실제 보유 종목 조회
        try:
            broker_positions = self.broker.get_positions()
            holding_codes = {p.stock_code for p in broker_positions} if broker_positions else set()
            logger.info(f"   브로커 보유: {len(holding_codes)}개 종목")
        except Exception as e:
            logger.warning(f"보유 종목 조회 실패: {e}")
            return
        
        removed = 0
        updated = 0
        
        for pos in positions:
            code = pos.stock_code
            
            # 실제로 보유하지 않음 → 삭제
            if code not in holding_codes:
                logger.warning(f"   ⚠️ 보유 불일치: {pos.stock_name}({code}) - DB 삭제")
                self.position_manager.remove_position(code)
                removed += 1
                continue
            
            # 현재가 갱신
            try:
                current_price = self.broker.get_current_price(code)
                if current_price > 0:
                    pos.current_price = current_price
                    pos.high_price = max(pos.high_price or 0, current_price)
                    pos.profit_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                    updated += 1
                    logger.info(f"   ✅ 가격 갱신: {pos.stock_name} {pos.entry_price:,}→{current_price:,}원 ({pos.profit_pct:+.2f}%)")
            except Exception as e:
                logger.debug(f"   가격 조회 실패 ({code}): {e}")
        
        if removed > 0 or updated > 0:
            logger.info(f"🔄 동기화 완료: 삭제 {removed}개, 갱신 {updated}개")
    
    # =========================================================================
    # 메인 루프
    # =========================================================================
    
    def run(self):
        """메인 실행"""
        # PID 락 획득 (중복 실행 방지)
        if not self._acquire_pid_lock():
            logger.error("❌ 이미 실행 중인 프로세스 존재 - 종료")
            return
        
        if not self.initialize():
            logger.error("초기화 실패 - 종료")
            self._release_pid_lock()
            return
        
        # 브로커와 포지션 동기화 (재시작 시 불일치 해결)
        self._sync_positions_with_broker()
        
        # 시그널 핸들러
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self._running = True
        logger.info("\n🚀 ScalpEngine 시작!")
        
        try:
            while self._running:
                now = datetime.now()
                current_time = now.time()
                
                # 프리마켓 분석 (08:00~08:50)
                if dt_time(8, 0) <= current_time < dt_time(8, 50):
                    self._run_premarket()
                
                # 갭 분석 (08:50~09:05)
                elif dt_time(8, 50) <= current_time < self.market_open:
                    self._analyze_gaps()
                
                # 스캘핑 매매 (09:05~14:50)
                elif self.market_open <= current_time < self.market_close:
                    self._run_scalping_loop()
                
                # 장 마감 청산 (14:50)
                elif current_time >= self.market_close:
                    self._close_all_positions()
                    self._generate_daily_report()
                    self._running = False
                
                else:
                    # 장 시작 전 대기
                    logger.info(f"장 시작 대기 중... ({now.strftime('%H:%M:%S')})")
                    self._interruptible_sleep(60)
                
        except Exception as e:
            logger.error(f"실행 에러: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            self._shutdown()
    
    def _signal_handler(self, signum, frame):
        """시그널 핸들러 (Ctrl+C)"""
        logger.info(f"\n⚠️ 종료 신호 수신 (signal={signum})")
        self._running = False
        # 즉시 종료 처리
        self._shutdown()
        import sys
        sys.exit(0)
    
    def _interruptible_sleep(self, seconds: float, interval: float = 1.0):
        """
        인터럽트 가능한 sleep
        
        Args:
            seconds: 총 대기 시간
            interval: 체크 간격 (기본 1초)
        """
        elapsed = 0
        while elapsed < seconds and self._running:
            time.sleep(min(interval, seconds - elapsed))
            elapsed += interval
    
    def _shutdown(self):
        """종료 처리 - 포지션 청산 + 상태 저장"""
        # 중복 호출 방지
        if hasattr(self, '_shutdown_called') and self._shutdown_called:
            return
        self._shutdown_called = True
        
        logger.info("\n" + "=" * 60)
        logger.info("🛑 ScalpEngine 종료 처리 시작")
        logger.info("=" * 60)
        
        # 1. 미체결 주문 취소
        if self.broker:
            try:
                cancelled = self.broker.cancel_all_pending_orders()
                if cancelled > 0:
                    logger.info(f"✅ 미체결 주문 {cancelled}건 취소")
            except Exception as e:
                logger.error(f"미체결 취소 실패: {e}")
        
        # 2. 보유 포지션 청산
        if self.position_manager:
            positions = self.position_manager.get_all_positions()
            if positions:
                logger.warning(f"⚠️ 미청산 포지션 {len(positions)}개 - 청산 시도")
                
                for pos in positions:
                    try:
                        result = self.broker.sell_market(pos.stock_code, pos.quantity)
                        if result.success:
                            logger.info(f"  ✅ {pos.stock_code} {pos.quantity}주 청산 완료")
                            self.position_manager.remove_position(pos.stock_code)
                        else:
                            logger.error(f"  ❌ {pos.stock_code} 청산 실패: {result.error}")
                            # 수량 초과 = 실제로 없음 → 포지션 삭제
                            if "수량" in str(result.error) and "초과" in str(result.error):
                                logger.warning(f"  ⚠️ 보유 불일치 - {pos.stock_code} 포지션 강제 삭제")
                                self.position_manager.remove_position(pos.stock_code)
                    except Exception as e:
                        logger.error(f"  ❌ {pos.stock_code} 청산 에러: {e}")
            else:
                logger.info("✅ 보유 포지션 없음")
        
        # 3. 상태 저장
        self._save_shutdown_state()
        
        # 4. Discord 알림
        if self.notifier:
            try:
                self.notifier.send_message(
                    "🛑 ScalpEngine 종료",
                    f"시간: {datetime.now().strftime('%H:%M:%S')}\n"
                    f"거래: {self._stats.get('trades', 0)}건\n"
                    f"승률: {self._calculate_winrate():.1f}%"
                )
                import time
                time.sleep(1)  # 메시지 전송 대기
                self.notifier.stop()  # ★ 스레드 정리
            except:
                pass
        
        # 5. PID 락 해제
        self._release_pid_lock()
        
        logger.info("=" * 60)
        logger.info("ScalpEngine 종료 완료")
        logger.info("=" * 60)
    
    def _save_shutdown_state(self):
        """종료 시 상태 저장"""
        try:
            from pathlib import Path
            import json
            
            state = {
                'shutdown_time': datetime.now().isoformat(),
                'stats': self._stats,
                'mode': self.mode,
                'premarket_done': self.premarket_result is not None,
                'universe': list(self._trackers.keys()) if self._trackers else [],
            }
            
            state_file = Path('logs') / 'last_state.json'
            state_file.parent.mkdir(exist_ok=True)
            
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            
            logger.info(f"✅ 상태 저장: {state_file}")
            
        except Exception as e:
            logger.error(f"상태 저장 실패: {e}")
    
    def _calculate_winrate(self) -> float:
        """승률 계산"""
        wins = self._stats.get('wins', 0)
        losses = self._stats.get('losses', 0)
        total = wins + losses
        return (wins / total * 100) if total > 0 else 0.0
    
    # =========================================================================
    # 프리마켓 분석
    # =========================================================================
    
    def _run_premarket(self):
        """프리마켓 분석 실행"""
        if self.premarket_result:
            # 이미 분석 완료
            self._interruptible_sleep(60)
            return
        
        logger.info("📊 프리마켓 분석 시작...")
        
        import asyncio
        
        async def run_analysis():
            return await self.premarket_analyzer.run_full_analysis()
        
        # 비동기 실행
        self.premarket_result = asyncio.run(run_analysis())
        
        # 트래커 생성
        if self.premarket_result and self.premarket_result.selected_stocks:
            for stock in self.premarket_result.selected_stocks:
                self._trackers[stock.code] = StockTracker(
                    code=stock.code,
                    name=stock.name,
                    prev_close=stock.prev_close,
                    ai_score=stock.ai_score,
                    scenarios=stock.ai_analysis.get('scenarios', {}),
                )
            
            logger.info(f"✅ {len(self._trackers)}개 종목 유니버스 설정")
        
        # 회피 목록 저장 (당일 한정)
        if self.premarket_result and self.premarket_result.avoid_stocks:
            self._avoid_codes.clear()
            self._avoid_names = set()  # 종목명도 저장
            self._avoid_date = datetime.now().strftime('%Y-%m-%d')
            for item in self.premarket_result.avoid_stocks:
                # item 형태: (종목명, 이유) 튜플 또는 dict
                if isinstance(item, tuple) and len(item) >= 1:
                    name = item[0]
                    self._avoid_names.add(name)
                    # 종목명으로 code 찾기
                    code = self.stock_mapper.name_to_code(name) if self.stock_mapper else None
                    if code:
                        self._avoid_codes.add(code)
                elif isinstance(item, dict):
                    if 'code' in item:
                        self._avoid_codes.add(item['code'])
                    if 'name' in item:
                        self._avoid_names.add(item['name'])
                elif hasattr(item, 'code'):
                    self._avoid_codes.add(item.code)
                    if hasattr(item, 'name'):
                        self._avoid_names.add(item.name)
            if self._avoid_codes or self._avoid_names:
                logger.info(f"🚫 회피 목록 캐시: {len(self._avoid_codes)}개 코드, {len(self._avoid_names)}개 종목명")
        
        # Discord 알림
        if self.notifier and self.premarket_result:
            self._send_premarket_notification()
    
    def _analyze_gaps(self):
        """갭 분석 (08:50~09:05)"""
        # 유니버스가 없으면 프리마켓 분석 강제 실행
        if not self._trackers:
            logger.warning("⚠️ 유니버스 미설정 - 프리마켓 분석 강제 실행")
            self._run_premarket()
            return
        
        logger.info("📊 갭 분석 중... (08:50~09:05)")
        # 시초가 형성 후 갭 분석
        # TODO: 시초가 조회 및 시나리오 업데이트
        self._interruptible_sleep(30)
    
    # =========================================================================
    # 스캘핑 메인 루프
    # =========================================================================
    
    def _run_scalping_loop(self):
        """스캘핑 매매 루프"""
        loop_start = time.time()
        
        # 1분마다 상태 로그 출력
        now = datetime.now()
        if not hasattr(self, '_last_status_log') or (now - self._last_status_log).total_seconds() >= 60:
            pos_count = self.position_manager.get_position_count()
            logger.info(f"📈 스캘핑 모니터링 중... (유니버스: {len(self._trackers)}개, 포지션: {pos_count}/{self.max_positions})")
            self._last_status_log = now
        
        # -1. 유니버스 체크 (없으면 프리마켓 분석 실행)
        if not self._trackers:
            logger.warning("⚠️ 유니버스 미설정 - 프리마켓 분석 실행")
            self._run_premarket()
            return
        
        # 0. 유니버스 갱신 체크 (10분마다)
        self._check_universe_refresh()
        
        # 1. 시장 상태 확인
        market_state = self.market_monitor.get_state()
        
        if market_state.mode == MarketMode.EMERGENCY:
            logger.warning("🚨 비상 모드 - 신규 진입 금지")
            time.sleep(SCAN_INTERVAL)
            return
        
        # 2. 킬스위치 체크
        if self.kill_switch.should_pause():
            logger.warning("⚠️ 킬스위치 발동 - 매매 일시 정지")
            time.sleep(SCAN_INTERVAL)
            return
        
        # 3. 포지션 체크 (손절/익절)
        self._check_positions()
        
        # 4. 유니버스 스캔 (신규 진입)
        if self.position_manager.get_position_count() < self.max_positions:
            self._scan_for_entry(market_state)
        
        # 루프 시간 조절
        elapsed = time.time() - loop_start
        sleep_time = max(0, SCAN_INTERVAL - elapsed)
        time.sleep(sleep_time)
    
    def _check_universe_refresh(self):
        """장중 유니버스 갱신 (TV100)"""
        universe_config = self.config.get('universe', {})
        
        if not universe_config.get('refresh_enabled', True):
            return
        
        refresh_interval = universe_config.get('refresh_interval', 10)
        now = datetime.now()
        
        # 마지막 갱신 시간 체크 (첫 호출 시 즉시 실행)
        if not hasattr(self, '_last_universe_refresh'):
            self._last_universe_refresh = now - timedelta(minutes=refresh_interval + 1)  # 과거로 설정 → 즉시 실행
        
        minutes_since_refresh = (now - self._last_universe_refresh).total_seconds() / 60
        
        if minutes_since_refresh < refresh_interval:
            return
        
        logger.info(f"🔄 유니버스 갱신 시작 ({refresh_interval}분 경과)")
        
        try:
            # TV100 조건검색 호출
            condition_name = universe_config.get('condition_name', 'TV100')
            new_stocks = self.broker.get_condition_stocks(condition_name)
            
            if not new_stocks:
                logger.warning("TV100 결과 없음")
                self._last_universe_refresh = now
                return
            
            # 가격 필터링
            min_price = universe_config.get('min_price', 3000)
            max_price = universe_config.get('max_price', 50000)
            
            # ETF 제외 패턴
            etf_patterns = ['KODEX', 'TIGER', 'KOSEF', 'KBSTAR', 'HANARO', 'SOL', 'ACE', 'ARIRANG']
            
            added_count = 0
            skipped_avoid = 0
            skipped_ai = 0
            
            # 회피 목록 날짜 체크 (당일 한정)
            today = datetime.now().strftime('%Y-%m-%d')
            if self._avoid_date != today:
                self._avoid_codes.clear()
                self._avoid_names.clear()
                self._avoid_date = today
            
            for stock in new_stocks[:20]:  # 상위 20개만
                code = stock.get('code', '')
                price = stock.get('price', 0)
                name = stock.get('name', '')
                
                # 가격 필터
                if not (min_price <= price <= max_price):
                    continue
                
                # ETF 필터 (스캘핑에 부적합)
                if any(pattern in name for pattern in etf_patterns):
                    continue
                
                # 이미 있으면 스킵
                if code in self._trackers:
                    continue
                
                # ========================================
                # A안: 회피 목록 체크 (빠름, API 호출 없음)
                # ========================================
                if code in self._avoid_codes or name in self._avoid_names:
                    logger.info(f"   ⏭️ 회피목록 스킵: {name}({code})")
                    skipped_avoid += 1
                    continue
                
                # ========================================
                # B안: 실시간 AI 필터 (config에서 활성화 시에만)
                # ========================================
                ai_score = 50  # 기본값
                ai_config = self.config.get('ai', {})
                use_quick_filter = ai_config.get('use_for_quick_filter', False)
                
                if use_quick_filter and self.ai_engine:
                    try:
                        ai_result = self._quick_ai_filter(code, name, price)
                        if ai_result.get('avoid', False):
                            # 회피 목록에 추가 (code + name 둘 다)
                            self._avoid_codes.add(code)
                            self._avoid_names.add(name)
                            reason = ai_result.get('reason', '조건 미충족')
                            logger.info(f"   🚫 AI 필터 제외: {name}({code}) - {reason}")
                            skipped_ai += 1
                            continue
                        ai_score = ai_result.get('score', 50)
                    except Exception as e:
                        logger.debug(f"   AI 필터 실패 ({name}): {e}")
                
                # 새 종목 추가
                self._trackers[code] = StockTracker(
                    code=code,
                    name=name,
                    prev_close=price,  # 현재가를 기준으로
                    ai_score=ai_score,
                    scenarios={},
                )
                added_count += 1
                logger.info(f"   + 추가: {name}({code}) {price:,}원 (AI:{ai_score}점)")
            
            # 너무 많으면 오래된 것 제거 (최대 20개 유지)
            max_universe = 20
            if len(self._trackers) > max_universe:
                # 보유 중인 종목은 유지
                holding_codes = set(self.position_manager.get_all_codes())
                
                # 보유 중이 아닌 종목 중 오래된 것 제거
                removable = [
                    code for code in self._trackers.keys()
                    if code not in holding_codes
                ]
                
                while len(self._trackers) > max_universe and removable:
                    old_code = removable.pop(0)
                    del self._trackers[old_code]
            
            self._last_universe_refresh = now
            skip_info = f"(회피:{skipped_avoid}, AI제외:{skipped_ai})" if (skipped_avoid + skipped_ai) > 0 else ""
            logger.info(f"✅ 유니버스 갱신 완료: +{added_count}개, 총 {len(self._trackers)}개 {skip_info}")
            
        except Exception as e:
            logger.error(f"유니버스 갱신 실패: {e}")
            self._last_universe_refresh = now
    
    def _quick_ai_filter(self, code: str, name: str, price: float) -> Dict[str, Any]:
        """
        장중 유니버스 갱신용 빠른 AI 필터
        
        Returns:
            {
                'avoid': bool,      # True면 제외
                'score': int,       # 0-100 점수
                'reason': str,      # 제외 이유 (avoid=True일 때)
            }
        """
        if not self.ai_engine:
            return {'avoid': False, 'score': 50, 'reason': ''}
        
        # 간단한 프롬프트 (JSON만 응답 강조)
        prompt = f"""{name}({code}) {price:,.0f}원 - 스캘핑 적합?
JSON만: {{"avoid":false,"score":70,"reason":""}}
avoid=true: 관리종목/급락/과열
모르면 score:50"""

        try:
            import json
            import re
            response = self.ai_engine.generate(
                prompt=prompt,
                max_tokens=10000,  # 테스트: 충분히 늘려서 파싱 성공률 확인
            )
            
            # 응답 로그 (테스트용 INFO)
            response_len = len(response)
            logger.info(f"   🤖 AI필터 [{name}] 응답:{response_len}자")
            
            # JSON 추출 (여러 방법 시도)
            text = response.strip()
            
            # 방법1: ```json 블록에서 추출
            if '```' in text:
                match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
                if match:
                    text = match.group(1)
            
            # 방법2: { } 사이만 추출
            match = re.search(r'\{[^{}]*\}', text)
            if match:
                text = match.group(0)
            
            result = json.loads(text)
            
            # 파싱 성공 로그
            score = result.get('score', 50)
            avoid = result.get('avoid', False)
            reason = result.get('reason', '')
            
            # avoid=true일 때만 상세 로그
            if avoid:
                logger.info(f"      → avoid=True, score={score}, reason={reason[:50]}")
            
            return {
                'avoid': avoid,
                'score': score,
                'reason': reason,
            }
            
        except Exception as e:
            logger.debug(f"AI 필터 파싱 실패 ({name}): {e}")
            return {'avoid': False, 'score': 50, 'reason': ''}
    
    def _scan_for_entry(self, market_state):
        """진입 기회 스캔"""
        self._stats['scans'] += 1
        
        # 🍽️ 점심시간 매수 금지 (config에서 설정)
        current_time = datetime.now().time()
        
        trading_config = self.config.get('trading', {})
        lunch_start_str = trading_config.get('lunch_break_start', '11:30')
        lunch_end_str = trading_config.get('lunch_break_end', '13:00')
        lunch_enabled = trading_config.get('lunch_break_enabled', True)
        
        if lunch_enabled:
            h, m = map(int, lunch_start_str.split(':'))
            lunch_start = dt_time(h, m)
            h, m = map(int, lunch_end_str.split(':'))
            lunch_end = dt_time(h, m)
            
            if lunch_start <= current_time < lunch_end:
                # 1분마다 한 번만 로그
                if not hasattr(self, '_last_lunch_log') or \
                   (datetime.now() - self._last_lunch_log).seconds >= 60:
                    logger.info(f"🍽️ 점심시간 - 매수 중지 ({current_time.strftime('%H:%M')}, ~{lunch_end_str})")
                    self._last_lunch_log = datetime.now()
                return None
        
        # ☕ 연패 휴식 체크
        if self.kill_switch and self.kill_switch.is_resting():
            remaining = self.kill_switch.get_rest_remaining()
            # 1분마다 한 번만 로그
            if not hasattr(self, '_last_rest_log') or \
               (datetime.now() - self._last_rest_log).seconds >= 60:
                logger.info(f"☕ 휴식 중 - 매수 중지 (남은 시간: {remaining // 60}분 {remaining % 60}초)")
                self._last_rest_log = datetime.now()
            return None
        
        # 마켓 컨텍스트
        context = MarketContext(
            kospi_change_pct=market_state.kospi_change,
            kosdaq_change_pct=market_state.kosdaq_change,
            current_time=current_time,
            conservative_mode=(market_state.mode == MarketMode.CONSERVATIVE),
            emergency_mode=(market_state.mode == MarketMode.EMERGENCY),
        )
        
        best_signal: Optional[ScalpSignal] = None
        scan_stats = {'total': 0, 'holding': 0, 'cooldown': 0, 'no_data': 0, 'tech_fail': 0, 'no_signal': 0, 'candidates': 0}
        
        for code, tracker in self._trackers.items():
            scan_stats['total'] += 1
            
            # 이미 보유 중이면 스킵
            if self.position_manager.has_position(code):
                scan_stats['holding'] += 1
                continue
            
            # 쿨타임 체크
            if not self.cooldown_tracker.can_buy(code):
                scan_stats['cooldown'] += 1
                continue
            
            # 분봉 데이터 업데이트 (기술적 필터용으로 30개)
            minute_data = self.broker.get_minute_ohlcv(code, interval=1, count=30)
            if not minute_data or len(minute_data) < 10:  # 20 → 10으로 완화 (장 초반 대응)
                scan_stats['no_data'] += 1
                logger.debug(f"   {tracker.name}: 분봉 데이터 부족 ({len(minute_data) if minute_data else 0}개)")
                continue
            
            # 🆕 기술적 사전 필터 (MACD + RSI)
            closes = [float(d.get('close', 0)) for d in minute_data]
            tech_filter = self._check_technical_filter(closes)
            
            if not tech_filter['buy_signal']:
                # 기술적 조건 미충족 → 스킵 (API 호출 절감)
                scan_stats['tech_fail'] += 1
                continue
            
            # OHLCV 변환 (최신 봉)
            latest = minute_data[0]
            candle = OHLCV(
                timestamp=latest.get('timestamp', ''),
                open=float(latest.get('open', 0)),
                high=float(latest.get('high', 0)),
                low=float(latest.get('low', 0)),
                close=float(latest.get('close', 0)),
                volume=int(latest.get('volume', 0)),
            )
            
            # 지표 업데이트
            indicators = tracker.indicators.update(candle)
            
            # 전일 정보 업데이트
            context.prev_close = tracker.prev_close
            
            # 시그널 평가
            signal = self.signal_generator.evaluate(
                stock_code=code,
                indicators=indicators,
                context=context,
                stock_name=tracker.name,
            )
            
            # 🆕 기술적 필터 보너스 점수 추가
            signal.score += tech_filter['score_bonus']
            if tech_filter['reasons']:
                signal.reason += f" | {', '.join(tech_filter['reasons'])}"
            
            self._stats['signals'] += 1
            
            # BUY 시그널이면서 점수가 높으면 선택
            if signal.action == 'BUY':
                scan_stats['candidates'] += 1
                logger.info(f"   💡 매수 후보: {tracker.name}({code}) 점수:{signal.score:.0f} - {signal.reason}")
                if best_signal is None or signal.score > best_signal.score:
                    best_signal = signal
                    best_signal.stock_code = code
            else:
                scan_stats['no_signal'] += 1
        
        # 스캔 통계 로그 (5분마다)
        now = datetime.now()
        if not hasattr(self, '_last_scan_log') or (now - self._last_scan_log).total_seconds() >= 300:
            logger.info(f"🔍 스캔 통계: 총{scan_stats['total']} | 보유{scan_stats['holding']} | 쿨타임{scan_stats['cooldown']} | 데이터없음{scan_stats['no_data']} | 기술필터탈락{scan_stats['tech_fail']} | 시그널없음{scan_stats['no_signal']} | 후보{scan_stats['candidates']}")
            self._last_scan_log = now
        
        # 최고 시그널로 매수
        if best_signal and best_signal.action == 'BUY':
            self._execute_buy(best_signal)
    
    def _check_technical_filter(self, closes: list) -> dict:
        """기술적 사전 필터 (MACD + RSI)"""
        # config에서 필터 활성화 여부 확인 (기본: 비활성화)
        trading_config = self.config.get('trading', {})
        if not trading_config.get('use_technical_filter', False):
            # 필터 비활성화 → 항상 통과
            return {
                'buy_signal': True,
                'score_bonus': 0,
                'reasons': [],
            }
        
        try:
            from scalping.strategy.minute_indicators import check_technical_filter
            return check_technical_filter(closes)
        except Exception as e:
            logger.debug(f"기술적 필터 에러: {e}")
            # 에러 시 통과 (기존 로직 유지)
            return {
                'buy_signal': True,
                'score_bonus': 0,
                'reasons': [],
            }
    
    def _check_positions(self):
        """포지션 체크 (손절/익절/시간손절)"""
        positions = self.position_manager.get_all_positions()
        
        for position in positions:
            # 현재가 조회
            current_price = self.broker.get_current_price(position.stock_code)
            
            if current_price <= 0:
                continue
            
            # 가격 업데이트 및 매도 신호 확인
            signal = self.position_manager.update_price(
                position.stock_code,
                current_price
            )
            
            if signal.action == 'SELL':
                self._execute_sell(
                    stock_code=position.stock_code,
                    quantity=position.quantity,
                    reason=signal.reason,
                    current_price=current_price,
                )
    
    # =========================================================================
    # 매수/매도 실행
    # =========================================================================
    
    def _execute_buy(self, signal: ScalpSignal):
        """매수 실행"""
        stock_code = signal.stock_code
        tracker = self._trackers.get(stock_code)
        
        if not tracker:
            return
        
        # 수량 계산
        price = signal.entry_price
        quantity = int(self.max_position_size / price)
        
        if quantity <= 0:
            logger.warning(f"수량 0 - 매수 불가 ({tracker.name})")
            return
        
        logger.info(f"\n{'='*50}")
        logger.info(f"🔵 매수 시그널: {tracker.name} ({stock_code})")
        logger.info(f"   점수: {signal.score:.0f}점 ({signal.signal_type.value})")
        logger.info(f"   가격: {price:,.0f}원 x {quantity}주")
        logger.info(f"   손절: {signal.stop_loss:,.0f}원 ({self.stop_loss}%)")
        logger.info(f"   익절: {signal.take_profit_1:,.0f}원 (+{self.take_profit_1}%)")
        logger.info(f"{'='*50}")
        
        # 실제 매수 (모드에 따라)
        if self.mode in ['LIVE', 'LIVE_MICRO']:
            order_result = self.broker.buy_market(
                stock_code=stock_code,
                quantity=quantity,
            )
            
            if order_result.success:
                # 포지션 추가
                self.position_manager.add_position(
                    stock_code=stock_code,
                    stock_name=tracker.name,
                    entry_price=price,
                    quantity=quantity,
                    score=signal.score,
                    entry_cci=signal.indicators.get('cci', 0),
                )
                
                self._stats['buys'] += 1
                self._today_trades.append({
                    'time': datetime.now(),
                    'side': 'BUY',
                    'code': stock_code,
                    'name': tracker.name,
                    'price': price,
                    'quantity': quantity,
                    'score': signal.score,
                    'signal_type': signal.signal_type.value,
                })
                
                # 쿨타임 등록 (매수 후 기본 쿨타임)
                self.cooldown_tracker.set_cooldown(stock_code, is_loss=False, reason="매수 완료")
                
                # Discord 알림
                if self.notifier:
                    self.notifier.send_buy_signal(
                        stock_code=stock_code,
                        stock_name=tracker.name,
                        price=price,
                        quantity=quantity,
                        score=signal.score,
                    )
                
                logger.info(f"✅ 매수 완료: {tracker.name}")
            else:
                logger.error(f"❌ 매수 실패: {order_result.error}")
        else:
            logger.info(f"📝 [시뮬] 매수: {tracker.name} (LIVE_DATA_ONLY 모드)")
    
    def _execute_sell(
        self,
        stock_code: str,
        quantity: int,
        reason: SellReason,
        current_price: float,
    ):
        """매도 실행"""
        position = self.position_manager.get_position(stock_code)
        
        if not position:
            return
        
        profit_pct = (current_price - position.entry_price) / position.entry_price * 100
        
        # 이모지 선택
        if reason == SellReason.TAKE_PROFIT:
            emoji = "🟢"
        elif reason == SellReason.STOP_LOSS:
            emoji = "🔴"
        elif reason == SellReason.TIME_STOP:
            emoji = "⏰"
        elif reason == SellReason.TRAILING_STOP:
            emoji = "📉"
        else:
            emoji = "⚪"
        
        logger.info(f"\n{'='*50}")
        logger.info(f"{emoji} 매도: {position.stock_name} ({stock_code})")
        logger.info(f"   사유: {reason.value}")
        logger.info(f"   진입가: {position.entry_price:,.0f}원")
        logger.info(f"   현재가: {current_price:,.0f}원")
        logger.info(f"   수익률: {profit_pct:+.2f}%")
        logger.info(f"{'='*50}")
        
        # 실제 매도
        if self.mode in ['LIVE', 'LIVE_MICRO']:
            order_result = self.broker.sell_market(
                stock_code=stock_code,
                quantity=quantity,
            )
            
            if order_result.success:
                # 포지션 제거
                self.position_manager.remove_position(stock_code)
                
                self._stats['sells'] += 1
                if profit_pct > 0:
                    self._stats['wins'] += 1
                else:
                    self._stats['losses'] += 1
                
                # 보유 시간 계산
                hold_minutes = 0
                if position.entry_time:
                    hold_delta = datetime.now() - position.entry_time
                    hold_minutes = int(hold_delta.total_seconds() / 60)
                
                # 수익금 계산
                profit_amount = (current_price - position.entry_price) * quantity
                
                self._today_trades.append({
                    'time': datetime.now(),
                    'side': 'SELL',
                    'stock_code': stock_code,
                    'stock_name': position.stock_name,
                    'buy_time': position.entry_time.strftime('%H:%M:%S') if position.entry_time else '',
                    'sell_time': datetime.now().strftime('%H:%M:%S'),
                    'buy_price': position.entry_price,
                    'sell_price': current_price,
                    'quantity': quantity,
                    'profit_pct': profit_pct,
                    'profit_amount': profit_amount,
                    'reason': reason.value,
                    'hold_minutes': hold_minutes,
                })
                
                # 킬스위치 업데이트
                is_win = profit_pct > 0
                self.kill_switch.record_trade(is_win=is_win, stock_code=stock_code)
                
                # Discord 알림
                if self.notifier:
                    self.notifier.send_sell_signal(
                        stock_code=stock_code,
                        stock_name=position.stock_name,
                        price=current_price,
                        quantity=quantity,
                        profit_pct=profit_pct,
                        reason=reason.value,
                    )
                
                logger.info(f"✅ 매도 완료: {position.stock_name}")
            else:
                logger.error(f"❌ 매도 실패: {order_result.error}")
                
                # 🔧 "수량 초과" 에러 = 실제로 보유하지 않음 → 포지션 강제 삭제
                if "수량" in str(order_result.error) and "초과" in str(order_result.error):
                    logger.warning(f"⚠️ 보유 수량 불일치 감지 - 포지션 강제 삭제: {position.stock_name}")
                    self.position_manager.remove_position(stock_code)
                    # 쿨타임도 설정 (같은 종목 재진입 방지)
                    if self.cooldown_tracker:
                        self.cooldown_tracker.set_cooldown(stock_code, minutes=10)
        else:
            logger.info(f"📝 [시뮬] 매도: {position.stock_name} (LIVE_DATA_ONLY 모드)")
    
    # =========================================================================
    # 장 마감 처리
    # =========================================================================
    
    def _close_all_positions(self):
        """전량 청산"""
        logger.info("\n📉 장 마감 - 전량 청산 시작")
        
        positions = self.position_manager.get_all_positions()
        
        for position in positions:
            current_price = self.broker.get_current_price(position.stock_code)
            
            if current_price <= 0:
                current_price = position.current_price
            
            self._execute_sell(
                stock_code=position.stock_code,
                quantity=position.quantity,
                reason=SellReason.TIME_LIMIT,
                current_price=current_price,
            )
        
        logger.info("📉 장 마감 청산 완료")
    
    def _generate_daily_report(self):
        """일일 리포트 생성"""
        logger.info("\n" + "=" * 60)
        logger.info("📊 일일 리포트")
        logger.info("=" * 60)
        
        total_trades = len([t for t in self._today_trades if t['side'] == 'SELL'])
        wins = self._stats['wins']
        losses = self._stats['losses']
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        total_profit = sum(
            t.get('profit_pct', 0) 
            for t in self._today_trades 
            if t['side'] == 'SELL'
        )
        
        logger.info(f"총 거래: {total_trades}회")
        logger.info(f"승/패: {wins}/{losses}")
        logger.info(f"승률: {win_rate:.1f}%")
        logger.info(f"총 수익률: {total_profit:+.2f}%")
        logger.info("=" * 60)
        
        # 학습 데이터 저장
        self._save_to_learning_store()
        
        # MD 매매일지 + AI 복기 생성
        self._generate_trading_journal()
        
        # Discord 알림
        if self.notifier:
            self.notifier.send_daily_report(
                date=datetime.now().strftime('%Y-%m-%d'),
                total_trades=total_trades,
                wins=wins,
                losses=losses,
                total_profit=0,
                total_profit_pct=total_profit,
            )
    
    def _save_to_learning_store(self):
        """학습 데이터 저장"""
        try:
            from scalping.ai.learning_store import LearningStore
            
            store = LearningStore()
            
            sell_trades = [t for t in self._today_trades if t['side'] == 'SELL']
            
            for trade in sell_trades:
                store.add_result(
                    stock_code=trade.get('stock_code', ''),
                    decision='BUY',
                    confidence=0.7,  # 기본값 (추후 시그널 점수 연동)
                    profit=trade.get('profit_pct', 0),
                    win=trade.get('profit_pct', 0) > 0,
                )
            
            # 일별 집계 업데이트
            store.update_daily_summary()
            
            logger.info(f"📚 학습 데이터 저장: {len(sell_trades)}건")
            
        except Exception as e:
            logger.error(f"학습 데이터 저장 실패: {e}")
    
    def _generate_trading_journal(self):
        """MD 매매일지 + AI 복기 생성"""
        sell_trades = [t for t in self._today_trades if t['side'] == 'SELL']
        
        if not sell_trades:
            logger.info("📝 오늘 매매 기록 없음 - 일지 생성 스킵")
            return
        
        try:
            from pathlib import Path
            
            today = datetime.now()
            today_str = today.strftime('%Y-%m-%d')
            
            # 통계 계산
            total_trades = len(sell_trades)
            wins = sum(1 for t in sell_trades if t.get('profit_pct', 0) > 0)
            losses = total_trades - wins
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
            total_profit = sum(t.get('profit_pct', 0) for t in sell_trades)
            total_amount = sum(t.get('profit_amount', 0) for t in sell_trades)
            
            # MD 내용 생성
            md_content = f"""# 📊 매매일지 {today_str}

## 요약
| 항목 | 값 |
|------|------|
| 총 거래 | {total_trades}회 |
| 승률 | {win_rate:.1f}% ({wins}승 {losses}패) |
| 수익률 | {total_profit:+.1f}% |
| 수익금 | {total_amount:+,.0f}원 |

## 매매 기록
| 시간 | 종목 | 매수가 | 매도가 | 수익률 | 보유 | 사유 |
|------|------|--------|--------|--------|------|------|
"""
            
            for t in sell_trades:
                md_content += f"| {t.get('sell_time', '')} | {t.get('stock_name', '')} | {t.get('buy_price', 0):,} | {t.get('sell_price', 0):,} | {t.get('profit_pct', 0):+.1f}% | {t.get('hold_minutes', 0)}분 | {t.get('reason', '')} |\n"
            
            # AI 복기 요청
            ai_review = self._get_ai_review(sell_trades, win_rate, total_profit)
            
            if ai_review:
                md_content += f"""
## 🤖 AI 복기

{ai_review}
"""
            
            # 파일 저장
            journal_dir = Path('logs/journal')
            journal_dir.mkdir(parents=True, exist_ok=True)
            
            filename = today.strftime('%Y%m%d') + '_매매일지.md'
            filepath = journal_dir / filename
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(md_content)
            
            logger.info(f"📝 매매일지 저장: {filepath}")
            
        except Exception as e:
            logger.error(f"매매일지 생성 실패: {e}")
    
    def _get_ai_review(self, trades: list, win_rate: float, total_profit: float) -> str:
        """AI 복기 요청"""
        try:
            import requests
            
            # secrets에서 API 키 가져오기
            gemini_key = self.secrets.get('gemini', {}).get('api_key', '')
            if not gemini_key:
                return ""
            
            # 매매 요약 생성
            trade_summary = ""
            for t in trades:
                result = "✅ 익절" if t.get('profit_pct', 0) > 0 else "❌ 손절"
                trade_summary += f"- {t.get('stock_name')}: {t.get('profit_pct', 0):+.1f}% ({t.get('reason')}, {t.get('hold_minutes')}분 보유) {result}\n"
            
            prompt = f"""당신은 스캘핑 트레이딩 코치입니다.
오늘의 매매 결과를 분석하고 간단히 피드백해주세요.

## 오늘 성과
- 승률: {win_rate:.1f}%
- 총 수익률: {total_profit:+.1f}%

## 매매 내역
{trade_summary}

## 요청사항
1. **잘한 점** (1-2줄)
2. **개선점** (1-2줄)  
3. **내일 제안** (1줄)

간결하게 핵심만 작성해주세요. 한글로 응답."""

            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}"
            
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 500,
                }
            }
            
            response = requests.post(url, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return data['candidates'][0]['content']['parts'][0]['text']
            else:
                logger.warning(f"AI 복기 실패: {response.status_code}")
                return ""
                
        except Exception as e:
            logger.error(f"AI 복기 에러: {e}")
            return ""
    
    def _send_premarket_notification(self):
        """프리마켓 분석 결과 알림"""
        if not self.notifier or not self.premarket_result:
            return
        
        msg = "📊 **프리마켓 분석 완료**\n\n"
        msg += f"**선정 종목 ({len(self.premarket_result.selected_stocks)}개)**\n"
        
        for i, stock in enumerate(self.premarket_result.selected_stocks[:5]):
            msg += f"{i+1}. {stock.name} ({stock.code}) - {stock.ai_score:.0f}점\n"
        
        if self.premarket_result.market_summary:
            msg += f"\n**시황**\n{self.premarket_result.market_summary[:200]}"
        
        self.notifier.send_message(msg)


# =============================================================================
# 메인
# =============================================================================

if __name__ == '__main__':
    from logging.handlers import TimedRotatingFileHandler
    
    # 로깅 설정 (일별 로테이션)
    log_handler = TimedRotatingFileHandler(
        'logs/scalp_engine.log',
        when='midnight',
        interval=1,
        backupCount=7,  # 7일치 보관
        encoding='utf-8',
    )
    log_handler.suffix = "%Y-%m-%d"
    
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            log_handler,
        ]
    )
    
    print("=" * 60)
    print("ScalpEngine v3.0 - 스캘핑 전용 엔진")
    print("=" * 60)
    
    engine = ScalpEngine(
        config_path='config/config.yaml',
        secrets_path='config/secrets.yaml',
    )
    
    engine.run()