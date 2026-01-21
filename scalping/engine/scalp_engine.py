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
        
        # 종목 트래커 (유니버스)
        self._trackers: Dict[str, StockTracker] = {}
        
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
                max_consecutive_losses=safety_config.get('consecutive_loss_stop', 5),
            )
            self.cooldown_tracker = CooldownTracker()
            logger.info("   ✅ 안전장치 초기화 완료")
            
            # 5. 종목 매퍼
            logger.info("\n[5/7] 종목 매퍼 초기화...")
            self.stock_mapper = StockMapper()
            logger.info("   ✅ 종목 매퍼 초기화 완료")
            
            # 6. 시그널 생성기
            logger.info("\n[6/7] 시그널 생성기 초기화...")
            self.signal_generator = ScalpSignalGenerator(self.config)
            logger.info("   ✅ 시그널 생성기 초기화 완료")
            
            # 7. 프리마켓 분석기
            logger.info("\n[7/7] 프리마켓 분석기 초기화...")
            self.premarket_analyzer = PreMarketAnalyzer(
                config=self.config,
                broker=self.broker,
            )
            logger.info("   ✅ 프리마켓 분석기 초기화 완료")
            
            # Discord 알림 (선택적)
            discord_config = self.config.get('discord', {})
            if discord_config.get('enabled'):
                webhook_url = self.secrets.get('discord', {}).get('webhook_url', '')
                if webhook_url:
                    self.notifier = DiscordNotifier(webhook_url=webhook_url)
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
    
    # =========================================================================
    # 메인 루프
    # =========================================================================
    
    def run(self):
        """메인 실행"""
        if not self.initialize():
            logger.error("초기화 실패 - 종료")
            return
        
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
                    time.sleep(60)
                
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
    
    def _shutdown(self):
        """종료 처리 - 포지션 청산 + 상태 저장"""
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
                        else:
                            logger.error(f"  ❌ {pos.stock_code} 청산 실패: {result.error}")
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
            except:
                pass
        
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
            time.sleep(60)
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
        
        # Discord 알림
        if self.notifier and self.premarket_result:
            self._send_premarket_notification()
    
    def _analyze_gaps(self):
        """갭 분석 (08:50~09:05)"""
        # 시초가 형성 후 갭 분석
        # TODO: 시초가 조회 및 시나리오 업데이트
        time.sleep(30)
    
    # =========================================================================
    # 스캘핑 메인 루프
    # =========================================================================
    
    def _run_scalping_loop(self):
        """스캘핑 매매 루프"""
        logger.debug("스캘핑 루프 시작")
        
        loop_start = time.time()
        
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
        
        refresh_interval = universe_config.get('refresh_interval_minutes', 10)
        now = datetime.now()
        
        # 마지막 갱신 시간 체크
        if not hasattr(self, '_last_universe_refresh'):
            self._last_universe_refresh = now
            return
        
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
            
            added_count = 0
            for stock in new_stocks[:20]:  # 상위 20개만
                code = stock.get('code', '')
                price = stock.get('price', 0)
                name = stock.get('name', '')
                
                # 가격 필터
                if not (min_price <= price <= max_price):
                    continue
                
                # 이미 있으면 스킵
                if code in self._trackers:
                    continue
                
                # 새 종목 추가
                self._trackers[code] = StockTracker(
                    code=code,
                    name=name,
                    prev_close=price,  # 현재가를 기준으로
                    ai_score=50,  # 기본 점수
                    scenarios={},
                )
                added_count += 1
                logger.info(f"   + 추가: {name}({code}) {price:,}원")
            
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
            logger.info(f"✅ 유니버스 갱신 완료: +{added_count}개, 총 {len(self._trackers)}개")
            
        except Exception as e:
            logger.error(f"유니버스 갱신 실패: {e}")
            self._last_universe_refresh = now
    
    def _scan_for_entry(self, market_state):
        """진입 기회 스캔"""
        self._stats['scans'] += 1
        
        # 마켓 컨텍스트
        context = MarketContext(
            kospi_change_pct=market_state.kospi_change,
            kosdaq_change_pct=market_state.kosdaq_change,
            current_time=datetime.now().time(),
            conservative_mode=(market_state.mode == MarketMode.CONSERVATIVE),
            emergency_mode=(market_state.mode == MarketMode.EMERGENCY),
        )
        
        best_signal: Optional[ScalpSignal] = None
        
        for code, tracker in self._trackers.items():
            # 이미 보유 중이면 스킵
            if self.position_manager.has_position(code):
                continue
            
            # 쿨타임 체크
            if not self.cooldown_tracker.can_buy(code):
                continue
            
            # 분봉 데이터 업데이트 (기술적 필터용으로 30개)
            minute_data = self.broker.get_minute_ohlcv(code, interval=1, count=30)
            if not minute_data or len(minute_data) < 20:
                continue
            
            # 🆕 기술적 사전 필터 (MACD + RSI)
            closes = [float(d.get('close', 0)) for d in minute_data]
            tech_filter = self._check_technical_filter(closes)
            
            if not tech_filter['buy_signal']:
                # 기술적 조건 미충족 → 스킵 (API 호출 절감)
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
                if best_signal is None or signal.score > best_signal.score:
                    best_signal = signal
                    best_signal.stock_code = code
        
        # 최고 시그널로 매수
        if best_signal and best_signal.action == 'BUY':
            self._execute_buy(best_signal)
    
    def _check_technical_filter(self, closes: list) -> dict:
        """기술적 사전 필터 (MACD + RSI)"""
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
            
            if order_result.get('success'):
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
                
                # 쿨타임 등록
                self.cooldown_tracker.record_buy(stock_code)
                
                # Discord 알림
                if self.notifier:
                    self.notifier.send_buy_notification(
                        stock_name=tracker.name,
                        stock_code=stock_code,
                        price=price,
                        quantity=quantity,
                        score=signal.score,
                    )
                
                logger.info(f"✅ 매수 완료: {tracker.name}")
            else:
                logger.error(f"❌ 매수 실패: {order_result.get('message')}")
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
            
            if order_result.get('success'):
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
                    self.notifier.send_sell_notification(
                        stock_name=position.stock_name,
                        stock_code=stock_code,
                        entry_price=position.entry_price,
                        exit_price=current_price,
                        quantity=quantity,
                        profit_pct=profit_pct,
                        reason=reason.value,
                    )
                
                logger.info(f"✅ 매도 완료: {position.stock_name}")
            else:
                logger.error(f"❌ 매도 실패: {order_result.get('message')}")
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
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('logs/scalp_engine.log', encoding='utf-8'),
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
