# ============================================================================
# ScalpingBot v3.3 - 시뮬레이션 모드 패치
# ============================================================================
# 
# 적용 방법:
# 1. simulation_tracker.py를 scalping/strategy/ 폴더에 복사
# 2. 아래 코드를 scalp_engine.py에 반영
# 3. config.yaml에서 mode: SIMULATION 설정
#
# ============================================================================

# -----------------------------------------------------------------------------
# [1] config.yaml 수정
# -----------------------------------------------------------------------------
# mode: SIMULATION  # LIVE_DATA_ONLY / LIVE_MICRO / LIVE / SIMULATION

# simulation:
#   take_profit_pct: 2.5      # 익절 목표
#   stop_loss_pct: -0.8       # 손절선
#   max_hold_minutes: 30      # 최대 추적 시간
#   max_concurrent: 10        # 동시 추적 최대
#   min_score_to_track: 70    # 이 점수 이상만 추적


# -----------------------------------------------------------------------------
# [2] scalp_engine.py 상단 import 추가
# -----------------------------------------------------------------------------
# from scalping.strategy.simulation_tracker import SimulationTracker


# -----------------------------------------------------------------------------
# [3] ScalpEngine.__init__() 에 추가
# -----------------------------------------------------------------------------
"""
        # 🆕 v3.3 시뮬레이션 모드
        self.simulation_tracker: Optional[SimulationTracker] = None
        self.is_simulation_mode = (self.mode == 'SIMULATION')
        
        # 시뮬레이션 설정
        sim_config = self.config.get('simulation', {})
        self.sim_take_profit = sim_config.get('take_profit_pct', 2.5)
        self.sim_stop_loss = sim_config.get('stop_loss_pct', -0.8)
        self.sim_max_hold = sim_config.get('max_hold_minutes', 30)
        self.sim_max_concurrent = sim_config.get('max_concurrent', 10)
        self.sim_min_score = sim_config.get('min_score_to_track', 70)
"""


# -----------------------------------------------------------------------------
# [4] ScalpEngine.initialize() 에 시뮬레이션 트래커 초기화 추가
# -----------------------------------------------------------------------------
"""
            # 🆕 시뮬레이션 모드 초기화
            if self.is_simulation_mode:
                logger.info("\n[SIM] 시뮬레이션 트래커 초기화...")
                self.simulation_tracker = SimulationTracker(
                    db_path='db/simulation.db',
                    max_hold_minutes=self.sim_max_hold,
                    max_concurrent=self.sim_max_concurrent,
                )
                logger.info(f"   ✅ 시뮬레이션 모드 (매매 없음, 신호만 추적)")
                logger.info(f"   - 익절 목표: {self.sim_take_profit}%")
                logger.info(f"   - 손절선: {self.sim_stop_loss}%")
                logger.info(f"   - 최소 점수: {self.sim_min_score}점")
"""


# -----------------------------------------------------------------------------
# [5] _process_buy_signal() 메서드 수정 (매수 실행 부분)
# -----------------------------------------------------------------------------
"""
    def _process_buy_signal(self, signal: ScalpSignal, tracker: StockTracker):
        \"\"\"매수 신호 처리\"\"\"
        
        # 🆕 시뮬레이션 모드: 실제 매수 대신 가상 진입
        if self.is_simulation_mode:
            if signal.score >= self.sim_min_score:
                self.simulation_tracker.enter_virtual(
                    stock_code=tracker.code,
                    stock_name=tracker.name,
                    entry_price=signal.entry_price,
                    signal_score=signal.score,
                    signal_type=signal.signal_type.value,
                    take_profit_pct=self.sim_take_profit,
                    stop_loss_pct=self.sim_stop_loss,
                )
            return  # 실제 매수하지 않음
        
        # 기존 실제 매수 로직...
"""


# -----------------------------------------------------------------------------
# [6] _scan_universe() 또는 메인 루프에서 가격 업데이트 추가
# -----------------------------------------------------------------------------
"""
        # 🆕 시뮬레이션: 가상 포지션 가격 업데이트
        if self.is_simulation_mode and self.simulation_tracker:
            # 현재 추적 중인 종목들의 가격 수집
            active_positions = self.simulation_tracker.get_active_positions()
            if active_positions:
                price_dict = {}
                for pos in active_positions:
                    try:
                        price_info = self.broker.get_current_price(pos.stock_code)
                        if price_info and price_info.get('price'):
                            price_dict[pos.stock_code] = price_info['price']
                    except:
                        pass
                
                if price_dict:
                    closed = self.simulation_tracker.update_prices(price_dict)
                    # closed 포지션에 대한 Discord 알림 등 처리 가능
"""


# -----------------------------------------------------------------------------
# [7] _generate_daily_report() 에 시뮬레이션 통계 추가
# -----------------------------------------------------------------------------
"""
        # 🆕 시뮬레이션 모드 통계
        if self.is_simulation_mode and self.simulation_tracker:
            self.simulation_tracker.close_all()  # 미결 포지션 정리
            self.simulation_tracker.print_daily_report()
            self.simulation_tracker.export_csv()
"""


# -----------------------------------------------------------------------------
# [8] 시뮬레이션 모드 메인 루프 로그 수정
# -----------------------------------------------------------------------------
"""
        if self.is_simulation_mode:
            logger.info("📝 [시뮬레이션 모드] 실제 매매 없이 신호만 추적합니다")
"""
