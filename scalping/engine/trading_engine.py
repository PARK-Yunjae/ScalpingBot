#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Trading Engine (메인 트레이딩 엔진)
============================================================================
전체 매매 시스템을 통합 관리하는 메인 엔진

핵심 기능:
- 시스템 초기화 및 구성요소 연결
- 메인 트레이딩 루프 실행
- 유니버스 스캔 및 점수 계산
- AI 분석 요청 및 결과 처리
- 매수/매도 실행
- 포지션 모니터링
- 일일 마감 처리

트레이딩 흐름:
1. 초기화 (08:55)
2. 지수 모니터 시작
3. AI 워커 시작
4. 장 시작 (09:00)
5. 유니버스 구성
6. 메인 스캔 루프 (1분마다)
7. 포지션 관리 (1초마다)
8. 장 마감 전 청산 (14:50)
9. 일일 마감 리포트

사용법:
    engine = TradingEngine(config, secrets)
    engine.run()  # 전체 트레이딩 시작
============================================================================
"""

import time
import logging
import threading
import schedule
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from queue import Queue, Empty

# 내부 모듈
from scalping.execution.broker import KISBroker
from scalping.execution.position_manager import PositionManager, SellReason
from scalping.execution.cooldown_tracker import CooldownTracker
from scalping.execution.price_validator import PriceValidator
from scalping.data.market_monitor import MarketMonitor, MarketMode
from scalping.strategy.score_engine import ScoreEngine
from scalping.strategy.indicators import calculate_all_indicators
from scalping.ai.ai_engine import AIEngine
from scalping.ai.learning_store import LearningStore
from scalping.notification.discord_bot import DiscordNotifier
from scalping.safety.kill_switch import KillSwitch, StopReason, SystemState

# 로거 설정
logger = logging.getLogger('ScalpingBot.Engine')


# =============================================================================
# 상수 설정
# =============================================================================

# 스캔 간격
SCAN_INTERVAL_SECONDS = 60       # 종목 스캔 간격 (1분)
POSITION_CHECK_SECONDS = 1       # 포지션 체크 간격 (1초)

# 장 시간
MARKET_PREP_TIME = "08:55"      # 준비 시작
MARKET_OPEN_TIME = "09:00"      # 장 시작
MARKET_CLOSE_TIME = "15:20"     # 장 마감 (청산 시작)
MARKET_END_TIME = "15:30"       # 완전 종료

# 점수 임계값
MIN_SCORE_NORMAL = 65           # 정상 모드 최소 점수
MIN_SCORE_CONSERVATIVE = 75     # 보수적 모드 최소 점수
MIN_AI_CONFIDENCE = 0.6         # 최소 AI 신뢰도


# =============================================================================
# 트레이딩 엔진 클래스
# =============================================================================

class TradingEngine:
    """
    메인 트레이딩 엔진
    
    모든 구성요소를 통합하고 트레이딩 루프를 실행합니다.
    """
    
    def __init__(
        self,
        config: Dict[str, Any],
        secrets: Dict[str, Any] = None,
        kis_config: Dict[str, Any] = None,
        discord_config: Dict[str, Any] = None,
        dry_run: bool = None,
    ):
        """
        초기화
        
        지원하는 호출 방식:
        1. TradingEngine(config, secrets) - 원래 방식
        2. TradingEngine(config, kis_config=..., discord_config=..., dry_run=...) - 새로운 방식
        
        Args:
            config: 설정 딕셔너리
            secrets: 비밀 정보 딕셔너리 (방식 1)
            kis_config: 한투 API 설정 (방식 2)
            discord_config: Discord 설정 (방식 2)
            dry_run: 드라이런 모드 (방식 2에서 명시적 지정)
        """
        self.config = config
        
        # 호출 방식에 따라 secrets 구성
        if secrets is not None:
            # 방식 1: TradingEngine(config, secrets)
            self.secrets = secrets
        else:
            # 방식 2: TradingEngine(config, kis_config=..., discord_config=..., dry_run=...)
            self.secrets = {
                'kis': kis_config or {},
                'discord': discord_config or {},
            }
        
        # 설정값 추출 (다양한 구조 지원)
        # dry_run: 명시적 파라미터 > config.trading.dry_run > config.mode 기반
        if dry_run is not None:
            self.dry_run = dry_run
        elif config.get('trading', {}).get('dry_run') is not None:
            self.dry_run = config['trading']['dry_run']
        else:
            # mode가 LIVE_DATA_ONLY이면 dry_run=True
            self.dry_run = config.get('mode', 'LIVE_DATA_ONLY') == 'LIVE_DATA_ONLY'
        
        # max_positions: config.trading > config.safety > config.risk
        self.max_positions = (
            config.get('trading', {}).get('max_positions') or
            config.get('safety', {}).get('max_positions') or
            config.get('risk', {}).get('max_positions', 5)
        )
        
        # position_size: config.trading > config.safety > config.risk
        self.position_size = (
            config.get('trading', {}).get('position_size') or
            config.get('safety', {}).get('max_position_size') or
            config.get('risk', {}).get('position_size', 500000)
        )
        
        # 구성요소 (초기화 전)
        self.broker: Optional[KISBroker] = None
        self.position_manager: Optional[PositionManager] = None
        self.cooldown_tracker: Optional[CooldownTracker] = None
        self.price_validator: Optional[PriceValidator] = None
        self.market_monitor: Optional[MarketMonitor] = None
        self.score_engine: Optional[ScoreEngine] = None
        self.ai_engine: Optional[AIEngine] = None
        self.learning_store: Optional[LearningStore] = None
        self.notifier: Optional[DiscordNotifier] = None
        self.kill_switch: Optional[KillSwitch] = None
        
        # 유니버스 (매매 대상 종목)
        self.universe: List[Dict] = []
        
        # 일봉 캐시 (종목코드 -> 점수)
        self._daily_score_cache: Dict[str, float] = {}
        
        # 실행 상태
        self._running = False
        self._scan_thread: Optional[threading.Thread] = None
        self._position_thread: Optional[threading.Thread] = None
        self._engine_thread: Optional[threading.Thread] = None  # start() 메서드용
        
        # 통계
        self._stats = {
            'total_scans': 0,
            'total_ai_requests': 0,
            'total_buys': 0,
            'total_sells': 0,
            'today_profit': 0.0,
        }
        
        # 오늘 매매 기록
        self._today_trades: List[Dict] = []
        
        logger.info(
            f"TradingEngine 초기화 "
            f"(dry_run={self.dry_run}, max_positions={self.max_positions})"
        )
    
    # =========================================================================
    # 초기화
    # =========================================================================
    
    def initialize(self) -> bool:
        """
        시스템 초기화
        
        모든 구성요소를 초기화하고 연결합니다.
        
        Returns:
            성공 여부
        """
        logger.info("=" * 60)
        logger.info("🚀 ScalpingBot v2.4 초기화 시작")
        logger.info("=" * 60)
        
        try:
            # 1. 브로커 초기화
            logger.info("1. 브로커 초기화...")
            self.broker = KISBroker(
                config=self.secrets.get('kis', {}),
                dry_run=self.dry_run
            )
            logger.info("   ✅ 브로커 초기화 완료")
            
            # 2. 포지션 관리자
            logger.info("2. 포지션 관리자 초기화...")
            db_path = Path(self.config.get('paths', {}).get('db_dir', 'db'))
            self.position_manager = PositionManager(
                db_path=db_path / 'positions.db'
            )
            logger.info("   ✅ 포지션 관리자 초기화 완료")
            
            # 3. 쿨다운 트래커
            logger.info("3. 쿨다운 트래커 초기화...")
            self.cooldown_tracker = CooldownTracker()
            logger.info("   ✅ 쿨다운 트래커 초기화 완료")
            
            # 4. 가격 검증기
            logger.info("4. 가격 검증기 초기화...")
            self.price_validator = PriceValidator()
            logger.info("   ✅ 가격 검증기 초기화 완료")
            
            # 5. 시장 모니터
            logger.info("5. 시장 모니터 초기화...")
            self.market_monitor = MarketMonitor(
                broker=self.broker,
                update_interval=10,
                on_mode_change=self._on_market_mode_change
            )
            
            # 5-1. 지수 일봉 히스토리 초기화 (MA20 계산용)
            logger.info("   지수 일봉 히스토리 로드 중...")
            try:
                kospi_daily = self.broker.get_index_daily('0001', period=60)  # 코스피
                kosdaq_daily = self.broker.get_index_daily('1001', period=60)  # 코스닥
                
                if kospi_daily:
                    self.market_monitor.initialize_history(kospi_daily, kosdaq_daily)
                    logger.info(f"   지수 히스토리 로드 완료: 코스피 {len(kospi_daily)}일")
                else:
                    logger.warning("   지수 일봉 데이터 없음, MA 계산이 부정확할 수 있음")
            except Exception as e:
                logger.warning(f"   지수 히스토리 로드 실패: {e}")
            
            logger.info("   ✅ 시장 모니터 초기화 완료")
            
            # 6. 점수 엔진
            logger.info("6. 점수 엔진 초기화...")
            self.score_engine = ScoreEngine(self.config)
            logger.info("   ✅ 점수 엔진 초기화 완료")
            
            # 7. AI 엔진
            logger.info("7. AI 엔진 초기화...")
            ai_config = self.config.get('ai', {})
            # AIEngine은 config dict를 받음
            self.ai_engine = AIEngine(config=ai_config)
            logger.info("   ✅ AI 엔진 초기화 완료")
            
            # 8. 학습 저장소
            logger.info("8. 학습 저장소 초기화...")
            self.learning_store = LearningStore(
                db_path=db_path / 'learning.db'
            )
            logger.info("   ✅ 학습 저장소 초기화 완료")
            
            # 9. Discord 알림
            logger.info("9. Discord 알림 초기화...")
            discord_url = self.secrets.get('discord', {}).get('webhook_url', '')
            self.notifier = DiscordNotifier(
                webhook_url=discord_url,
                enabled=bool(discord_url) and not self.dry_run
            )
            logger.info("   ✅ Discord 알림 초기화 완료")
            
            # 10. Kill Switch
            logger.info("10. Kill Switch 초기화...")
            safety_config = self.config.get('safety', {})
            self.kill_switch = KillSwitch(
                broker=self.broker,
                notifier=self.notifier,
                position_manager=self.position_manager,
                max_consecutive_losses=safety_config.get('max_consecutive_losses', 5),
                max_daily_loss_pct=safety_config.get('max_daily_loss_pct', -3.0),
                on_emergency=self._on_emergency
            )
            logger.info("   ✅ Kill Switch 초기화 완료")
            
            # 11. 브로커 연결 테스트
            logger.info("11. API 연결 테스트...")
            if not self.dry_run:
                if self.broker.health_check():
                    logger.info("   ✅ API 연결 정상")
                else:
                    logger.warning("   ⚠️ API 연결 확인 필요")
            else:
                logger.info("   ⏭️ DRY RUN 모드 - 연결 테스트 스킵")
            
            # 12. 포지션 동기화
            logger.info("12. 포지션 동기화...")
            self._sync_positions()
            logger.info("   ✅ 포지션 동기화 완료")
            
            logger.info("=" * 60)
            logger.info("✅ 초기화 완료!")
            logger.info("=" * 60)
            
            return True
        
        except Exception as e:
            logger.exception(f"❌ 초기화 실패: {e}")
            return False
    
    def _sync_positions(self):
        """브로커와 포지션 동기화"""
        try:
            broker_positions = self.broker.get_positions()
            
            # Position 객체를 딕셔너리로 변환
            positions_dict = [
                {
                    'stock_code': p.stock_code,
                    'stock_name': p.stock_name,
                    'quantity': p.quantity,
                    'avg_price': p.avg_price,
                }
                for p in broker_positions
            ]
            
            self.position_manager.sync_with_broker(positions_dict)
            
            logger.info(f"포지션 동기화: {len(positions_dict)}개 종목")
        
        except Exception as e:
            logger.error(f"포지션 동기화 실패: {e}")
    
    # =========================================================================
    # 시작/중지 인터페이스 (main.py 호환용)
    # =========================================================================
    
    def start(self):
        """
        트레이딩 시스템 시작 (별도 스레드)
        
        main.py에서 engine.start() 형태로 호출할 수 있도록 지원합니다.
        내부적으로 run()을 별도 스레드에서 실행합니다.
        """
        if self._running:
            logger.warning("TradingEngine이 이미 실행 중입니다.")
            return
        
        # 별도 스레드에서 run() 실행
        self._engine_thread = threading.Thread(
            target=self.run,
            name="TradingEngine-Main",
            daemon=True
        )
        self._engine_thread.start()
        
        # 초기화 완료까지 대기 (최대 30초)
        for _ in range(300):
            if self._running:
                break
            time.sleep(0.1)
        
        logger.info("TradingEngine.start() 완료")
    
    def is_running(self) -> bool:
        """실행 상태 확인"""
        return self._running
    
    # =========================================================================
    # 메인 실행
    # =========================================================================
    
    def run(self):
        """
        트레이딩 시스템 실행
        
        전체 트레이딩 루프를 시작합니다.
        """
        # 초기화
        if not self.initialize():
            logger.error("초기화 실패, 시스템 종료")
            return
        
        self._running = True
        
        # 시스템 시작 알림
        self.notifier.start()
        self.notifier.send_system_start(
            f"모드: {'DRY RUN' if self.dry_run else 'LIVE'}\n"
            f"최대 포지션: {self.max_positions}개\n"
            f"포지션 크기: {self.position_size:,}원"
        )
        
        try:
            # 시장 모니터 시작
            logger.info("📊 시장 모니터 시작...")
            self.market_monitor.start()
            
            # AI 엔진 시작
            logger.info("🤖 AI 엔진 시작...")
            self.ai_engine.start()
            
            # 메인 루프
            logger.info("🔄 메인 트레이딩 루프 시작...")
            self._main_loop()
        
        except KeyboardInterrupt:
            logger.info("⌨️ 키보드 인터럽트 감지")
        
        except Exception as e:
            logger.exception(f"❌ 치명적 에러: {e}")
            self.kill_switch.trigger(
                StopReason.SYSTEM_ERROR,
                str(e)
            )
        
        finally:
            self._shutdown()
    
    def _main_loop(self):
        """메인 트레이딩 루프"""
        last_scan_time = 0
        last_position_check = 0
        
        while self._running:
            try:
                now = time.time()
                current_time = datetime.now()
                
                # Kill Switch 체크
                if self.kill_switch.should_stop():
                    logger.warning("🛑 Kill Switch 활성화, 루프 중지")
                    break
                
                # 장 운영 시간 체크
                if not self._is_trading_time():
                    # 장 마감 체크
                    if self._is_closing_time():
                        self._handle_market_close()
                    
                    time.sleep(10)
                    continue
                
                # 1. 포지션 체크 (1초마다)
                if now - last_position_check >= POSITION_CHECK_SECONDS:
                    self._check_positions()
                    last_position_check = now
                
                # 2. 종목 스캔 (1분마다)
                if now - last_scan_time >= SCAN_INTERVAL_SECONDS:
                    self._scan_universe()
                    last_scan_time = now
                
                # 3. AI 응답 처리
                self._process_ai_results()
                
                # 짧은 대기
                time.sleep(0.1)
            
            except Exception as e:
                logger.error(f"루프 에러: {e}")
                self.kill_switch.record_system_error(str(e))
                time.sleep(5)
    
    def _shutdown(self):
        """시스템 종료"""
        logger.info("=" * 60)
        logger.info("🛑 시스템 종료 시작")
        logger.info("=" * 60)
        
        self._running = False
        
        # 구성요소 중지
        if self.market_monitor:
            self.market_monitor.stop()
        
        if self.ai_engine:
            self.ai_engine.stop()
        
        # 일일 마감 처리
        self._handle_daily_close()
        
        # 알림 전송
        if self.notifier:
            self.notifier.send_system_stop("정상 종료")
            time.sleep(1)  # 메시지 전송 대기
            self.notifier.stop()
        
        logger.info("✅ 시스템 종료 완료")
    
    # =========================================================================
    # 시간 체크
    # =========================================================================
    
    def _is_trading_time(self) -> bool:
        """장 운영 시간 여부"""
        now = datetime.now()
        
        # 주말 체크
        if now.weekday() >= 5:
            return False
        
        current_str = now.strftime("%H:%M")
        
        return MARKET_OPEN_TIME <= current_str < MARKET_CLOSE_TIME
    
    def _is_closing_time(self) -> bool:
        """장 마감 시간 여부"""
        now = datetime.now()
        current_str = now.strftime("%H:%M")
        
        return MARKET_CLOSE_TIME <= current_str <= MARKET_END_TIME
    
    # =========================================================================
    # 유니버스 스캔
    # =========================================================================
    
    def _scan_universe(self):
        """유니버스 스캔"""
        self._stats['total_scans'] += 1
        
        # 일시 정지 상태면 스킵
        if self.kill_switch.should_pause():
            return
        
        # 시장 상태 확인
        market_state = self.market_monitor.get_state()
        market_mode = market_state.mode
        
        # 안전장치에 시장 상태 전달
        self.kill_switch.check_market_condition(
            market_state.kospi_change,
            market_mode.value
        )
        
        # 점수 임계값 결정
        if market_mode == MarketMode.EMERGENCY:
            logger.info("🚨 비상 모드 - 스캔 스킵")
            return
        elif market_mode == MarketMode.CONSERVATIVE:
            min_score = MIN_SCORE_CONSERVATIVE
        else:
            min_score = MIN_SCORE_NORMAL
        
        # 유니버스 가져오기 (TODO: 실제 구현에서는 거래대금 상위 종목 조회)
        # 현재는 테스트용 더미 데이터
        if not self.universe:
            self._build_universe()
        
        # 스캔 결과
        candidates = []
        
        for stock in self.universe[:100]:  # 최대 100개 스캔
            stock_code = stock['code']
            
            # 이미 보유 중이면 스킵
            if self.position_manager.has_position(stock_code):
                continue
            
            # 쿨타임 체크
            if not self.cooldown_tracker.can_buy(stock_code):
                continue
            
            # 최대 포지션 체크
            if self.position_manager.get_position_count() >= self.max_positions:
                break
            
            # 점수 계산 (캐시 활용)
            score = self._calculate_score(stock)
            
            if score >= min_score:
                candidates.append({
                    'stock': stock,
                    'score': score,
                    'market_mode': market_mode.value,
                })
        
        # AI 분석 요청
        for candidate in candidates[:5]:  # 상위 5개만 AI 분석
            self._request_ai_analysis(candidate)
        
        logger.debug(
            f"스캔 완료: {len(self.universe)}개 중 {len(candidates)}개 후보"
        )
    
    def _build_universe(self):
        """유니버스 구성 (테스트용)"""
        # TODO: 실제 구현에서는 거래대금 상위 종목 조회
        self.universe = [
            {'code': '005930', 'name': '삼성전자'},
            {'code': '000660', 'name': 'SK하이닉스'},
            {'code': '035720', 'name': '카카오'},
            {'code': '005380', 'name': '현대차'},
            {'code': '000270', 'name': '기아'},
        ]
        
        logger.info(f"유니버스 구성: {len(self.universe)}개 종목")
    
    def _calculate_score(self, stock: Dict) -> float:
        """종목 점수 계산"""
        stock_code = stock['code']
        
        # 캐시 확인
        if stock_code in self._daily_score_cache:
            return self._daily_score_cache[stock_code]
        
        # TODO: 실제 구현에서는 일봉/분봉 데이터로 점수 계산
        # 테스트용 더미 점수
        import random
        score = random.uniform(50, 90)
        
        self._daily_score_cache[stock_code] = score
        
        return score
    
    # =========================================================================
    # AI 분석
    # =========================================================================
    
    def _request_ai_analysis(self, candidate: Dict):
        """AI 분석 요청"""
        stock = candidate['stock']
        score = candidate['score']
        market_mode = candidate['market_mode']
        
        # 현재가 조회
        current_price = self.broker.get_current_price(stock['code'])
        
        # 과거 승률 조회
        pattern_stats = self.learning_store.get_pattern_stats(
            cci_range=(150, 180),  # TODO: 실제 CCI 값 사용
            score_range=(int(score) // 10 * 10, int(score) // 10 * 10 + 10),
        )
        
        # AI 분석 요청
        request_id = self.ai_engine.request_analysis(
            stock_code=stock['code'],
            stock_name=stock['name'],
            rule_score=score,
            cci=160,  # TODO: 실제 CCI 값
            change_pct=3.0,  # TODO: 실제 등락률
            volume_ratio=2.0,  # TODO: 실제 거래량 비율
            market_mode=market_mode,
            market_change=self.market_monitor.get_state().kospi_change,
            past_winrate=pattern_stats.get('winrate', 50),
        )
        
        self._stats['total_ai_requests'] += 1
        
        logger.debug(f"AI 분석 요청: {stock['code']} {stock['name']} (점수: {score:.1f})")
    
    def _process_ai_results(self):
        """AI 응답 처리"""
        while True:
            result = self.ai_engine.get_result()
            
            if result is None:
                break
            
            # BUY 결정이고 신뢰도 충족 시
            if result.decision == 'BUY' and result.confidence >= MIN_AI_CONFIDENCE:
                self._execute_buy(result)
            else:
                logger.debug(
                    f"AI 결정 SKIP: {result.stock_code} "
                    f"({result.decision}, 신뢰도: {result.confidence:.2f})"
                )
    
    # =========================================================================
    # 매수/매도 실행
    # =========================================================================
    
    def _execute_buy(self, ai_result):
        """매수 실행"""
        stock_code = ai_result.stock_code
        
        # 최대 포지션 재확인
        if self.position_manager.get_position_count() >= self.max_positions:
            logger.info(f"최대 포지션 도달, 매수 스킵: {stock_code}")
            return
        
        # 이미 보유 중인지 재확인
        if self.position_manager.has_position(stock_code):
            logger.info(f"이미 보유 중, 매수 스킵: {stock_code}")
            return
        
        # 현재가 조회
        current_price = self.broker.get_current_price(stock_code)
        
        if current_price <= 0:
            logger.warning(f"현재가 조회 실패: {stock_code}")
            return
        
        # 가격 검증
        validation = self.price_validator.validate(
            stock_code=stock_code,
            analysis_price=ai_result.original_price,
            current_price=current_price,
            analysis_time=datetime.fromtimestamp(ai_result.timestamp),
        )
        
        if not validation.is_valid:
            logger.info(f"가격 검증 실패: {stock_code} - {validation.reason}")
            return
        
        # 수량 계산
        quantity = int(self.position_size / current_price)
        
        if quantity <= 0:
            logger.warning(f"수량 계산 실패: {stock_code}")
            return
        
        # 매수 실행
        result = self.broker.buy_market(stock_code, quantity)
        
        if result.success:
            self._stats['total_buys'] += 1
            
            # 포지션 등록
            self.position_manager.add_position(
                stock_code=stock_code,
                stock_name=ai_result.stock_name or stock_code,
                entry_price=current_price,
                quantity=quantity,
                score=ai_result.rule_score,
                ai_confidence=ai_result.confidence,
            )
            
            # 알림
            self.notifier.send_buy_signal(
                stock_code=stock_code,
                stock_name=ai_result.stock_name or stock_code,
                price=current_price,
                quantity=quantity,
                score=ai_result.rule_score,
                ai_confidence=ai_result.confidence,
                grade=self._get_grade(ai_result.rule_score),
            )
            
            # 매매 기록
            self._today_trades.append({
                'time': datetime.now(),
                'stock_code': stock_code,
                'side': 'BUY',
                'price': current_price,
                'quantity': quantity,
                'score': ai_result.rule_score,
                'ai_confidence': ai_result.confidence,
            })
            
            logger.info(
                f"✅ 매수 완료: {stock_code} {quantity}주 @ {current_price:,.0f}원"
            )
        else:
            logger.error(f"❌ 매수 실패: {stock_code} - {result.error}")
            self.kill_switch.record_api_error()
    
    def _execute_sell(
        self,
        stock_code: str,
        quantity: int,
        reason: SellReason,
        current_price: float,
    ):
        """매도 실행"""
        # 포지션 정보 조회
        position = self.position_manager.get_position(stock_code)
        
        if not position:
            logger.warning(f"포지션 없음: {stock_code}")
            return
        
        # 매도 실행
        result = self.broker.sell_market(stock_code, quantity)
        
        if result.success:
            self._stats['total_sells'] += 1
            
            # 수익률 계산
            profit_pct = (current_price - position.entry_price) / position.entry_price * 100
            
            # 포지션 삭제
            self.position_manager.remove_position(stock_code)
            
            # 쿨타임 설정
            is_loss = profit_pct < 0
            self.cooldown_tracker.set_cooldown(
                stock_code=stock_code,
                is_loss=is_loss,
                reason=reason.value
            )
            
            # Kill Switch에 기록
            if is_loss:
                self.kill_switch.record_loss(stock_code)
            else:
                self.kill_switch.record_win(stock_code)
            
            # 학습 저장소에 기록
            self.learning_store.add_result(
                stock_code=stock_code,
                decision='BUY',
                confidence=position.ai_confidence,
                profit=profit_pct,
                rule_score=position.score,
                cci=0,  # TODO: 실제 CCI
                market_mode=self.market_monitor.get_state().mode.value,
            )
            
            # 알림
            self.notifier.send_sell_signal(
                stock_code=stock_code,
                stock_name=position.stock_name,
                price=current_price,
                quantity=quantity,
                profit_pct=profit_pct,
                reason=reason.value,
            )
            
            # 매매 기록
            self._today_trades.append({
                'time': datetime.now(),
                'stock_code': stock_code,
                'side': 'SELL',
                'price': current_price,
                'quantity': quantity,
                'profit_pct': profit_pct,
                'reason': reason.value,
            })
            
            logger.info(
                f"✅ 매도 완료: {stock_code} {quantity}주 @ {current_price:,.0f}원 "
                f"({profit_pct:+.2f}%, {reason.value})"
            )
        else:
            logger.error(f"❌ 매도 실패: {stock_code} - {result.error}")
            self.kill_switch.record_api_error()
    
    # =========================================================================
    # 포지션 관리
    # =========================================================================
    
    def _check_positions(self):
        """포지션 체크 (손절/익절/트레일링)"""
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
    # 장 마감 처리
    # =========================================================================
    
    def _handle_market_close(self):
        """장 마감 처리 (청산)"""
        logger.info("📉 장 마감 - 전량 청산 시작")
        
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
    
    def _handle_daily_close(self):
        """일일 마감 처리"""
        logger.info("📊 일일 마감 처리...")
        
        # 일일 통계 계산
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        total_trades = len(self._today_trades)
        buy_trades = [t for t in self._today_trades if t['side'] == 'BUY']
        sell_trades = [t for t in self._today_trades if t['side'] == 'SELL']
        
        wins = len([t for t in sell_trades if t.get('profit_pct', 0) > 0])
        losses = len([t for t in sell_trades if t.get('profit_pct', 0) <= 0])
        
        total_profit_pct = sum(t.get('profit_pct', 0) for t in sell_trades)
        
        # 최고/최저 매매
        best_trade = None
        worst_trade = None
        
        if sell_trades:
            best = max(sell_trades, key=lambda x: x.get('profit_pct', 0))
            worst = min(sell_trades, key=lambda x: x.get('profit_pct', 0))
            
            best_trade = {'name': best['stock_code'], 'profit': best.get('profit_pct', 0)}
            worst_trade = {'name': worst['stock_code'], 'profit': worst.get('profit_pct', 0)}
        
        # 일일 리포트 전송
        self.notifier.send_daily_report(
            date=today_str,
            total_trades=total_trades,
            wins=wins,
            losses=losses,
            total_profit=total_profit_pct * 10000,  # 임의 환산
            total_profit_pct=total_profit_pct,
            best_trade=best_trade,
            worst_trade=worst_trade,
            ai_stats={
                'total': self._stats['total_ai_requests'],
                'avg_confidence': 0.7,  # TODO: 실제 계산
            }
        )
        
        # 학습 저장소 일일 집계
        self.learning_store.update_daily_summary()
        
        logger.info(
            f"일일 마감: {total_trades}건 매매, "
            f"{wins}승 {losses}패, {total_profit_pct:+.2f}%"
        )
    
    # =========================================================================
    # 콜백
    # =========================================================================
    
    def _on_market_mode_change(self, old_mode: MarketMode, new_mode: MarketMode):
        """시장 모드 변경 콜백"""
        logger.warning(f"시장 모드 변경: {old_mode.value} → {new_mode.value}")
        
        self.notifier.send_market_mode_change(
            old_mode=old_mode.value,
            new_mode=new_mode.value,
            reason=self.market_monitor.get_state().mode_reason
        )
        
        # 비상 모드면 일시 정지
        if new_mode == MarketMode.EMERGENCY:
            self.kill_switch.pause("시장 비상 모드")
    
    def _on_emergency(self, reason: StopReason, message: str):
        """비상 상황 콜백"""
        logger.critical(f"🚨 비상 상황: {reason.value} - {message}")
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def _get_grade(self, score: float) -> str:
        """점수에 따른 등급"""
        if score >= 90:
            return 'S'
        elif score >= 80:
            return 'A'
        elif score >= 70:
            return 'B'
        else:
            return 'C'
    
    def get_stats(self) -> Dict:
        """통계 조회"""
        return {
            **self._stats,
            'positions': self.position_manager.get_position_count() if self.position_manager else 0,
            'running': self._running,
        }
    
    def stop(self):
        """시스템 중지"""
        logger.info("🛑 시스템 중지 요청")
        self._running = False


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(name)s - %(message)s'
    )
    
    print("=" * 60)
    print("TradingEngine 테스트")
    print("=" * 60)
    
    # 테스트 설정
    test_config = {
        'trading': {
            'dry_run': True,
            'max_positions': 3,
            'position_size': 500000,
        },
        'ai': {
            'model': 'qwen3:8b',
            'timeout': 10,
        },
        'safety': {
            'max_consecutive_losses': 5,
            'max_daily_loss_pct': -3.0,
        },
        'paths': {
            'db_dir': '/tmp/scalpingbot_test',
        }
    }
    
    test_secrets = {
        'kis': {
            'app_key': 'TEST_KEY',
            'app_secret': 'TEST_SECRET',
            'account_number': '12345678',
            'account_code': '01',
        },
        'discord': {
            'webhook_url': '',
        }
    }
    
    # 엔진 생성
    engine = TradingEngine(test_config, test_secrets)
    
    # 초기화 테스트
    print("\n1. 초기화 테스트...")
    success = engine.initialize()
    print(f"   초기화 결과: {'✅ 성공' if success else '❌ 실패'}")
    
    if success:
        # 통계 확인
        print("\n2. 통계:")
        stats = engine.get_stats()
        for key, value in stats.items():
            print(f"   {key}: {value}")
        
        # 유니버스 구성
        print("\n3. 유니버스 구성...")
        engine._build_universe()
        print(f"   종목 수: {len(engine.universe)}")
        
        # 점수 계산 테스트
        print("\n4. 점수 계산 테스트...")
        if engine.universe:
            score = engine._calculate_score(engine.universe[0])
            print(f"   {engine.universe[0]['name']}: {score:.1f}점")
    
    print("\n" + "=" * 60)
    print("테스트 완료 (실제 트레이딩 루프는 실행하지 않음)")
    print("=" * 60)
