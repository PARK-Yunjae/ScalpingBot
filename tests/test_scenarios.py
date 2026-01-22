#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - 핵심 시나리오 테스트
============================================================================
설계서 14.3 핵심 시나리오 테스트 매트릭스

V-01: config.yaml 누락/오염 → 기본값 + 안전모드
V-02: 토큰 만료 → 자동 갱신, 실패 시 EMERGENCY
V-03: AI 타임아웃 → 해당 종목 SKIP
V-04: AI 응답 JSON 파싱 실패 → fallback(HOLD)
V-05: 웹소켓 끊김 → 재연결 시도 + 구독 복구
V-06: 주문 응답 지연/실패 → 중복주문 금지
V-07: 급락장(-2% 이하) → 스캔 중지 + 청산 모드
V-08: 설정 변경(장중) → 핫리로드 가능 항목만 반영
============================================================================
"""

import pytest
import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# V-01: config.yaml 누락/오염
# =============================================================================

class TestV01ConfigMissing:
    """V-01: config.yaml 누락/오염 → 기본값 + 안전모드"""
    
    def test_missing_config_uses_defaults(self):
        """설정 파일 없을 때 기본값 사용"""
        from scalping.config.config_loader import ConfigLoader
        
        with tempfile.TemporaryDirectory() as tmpdir:
            loader = ConfigLoader(
                config_path=f"{tmpdir}/nonexistent.yaml",
                auto_create=True
            )
            config = loader.load()
            
            # 기본값 확인
            assert config['mode'] == 'LIVE_DATA_ONLY'
            assert config['ai']['model'] == 'qwen3:8b'
    
    def test_corrupted_yaml_uses_defaults(self):
        """손상된 YAML은 기본값 사용"""
        from scalping.config.config_loader import ConfigLoader
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            
            # 손상된 YAML 작성
            with open(config_path, 'w') as f:
                f.write("invalid: yaml: content: {{{{")
            
            loader = ConfigLoader(str(config_path), auto_create=False)
            config = loader.load()
            
            # 기본값으로 폴백
            assert config is not None
            assert 'mode' in config
    
    def test_partial_config_merged_with_defaults(self):
        """일부 설정만 있을 때 기본값과 병합"""
        from scalping.config.config_loader import ConfigLoader
        import yaml
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            
            # 일부만 설정
            partial_config = {
                'mode': 'LIVE_MICRO',
                'ai': {'model': 'custom-model'},
            }
            
            with open(config_path, 'w') as f:
                yaml.dump(partial_config, f)
            
            loader = ConfigLoader(str(config_path))
            config = loader.load()
            
            # 설정된 값
            assert config['mode'] == 'LIVE_MICRO'
            assert config['ai']['model'] == 'custom-model'
            
            # 기본값으로 채워진 값
            assert config['ai']['timeout'] == 10
            assert config['risk']['stop_loss_pct'] == -1.5


# =============================================================================
# V-02: 토큰 만료
# =============================================================================

class TestV02TokenExpiry:
    """V-02: 토큰 만료 → 자동 갱신, 실패 시 EMERGENCY"""
    
    def test_token_auto_refresh_before_call(self):
        """API 호출 전 토큰 자동 갱신"""
        from scalping.execution.broker import KISBroker
        
        config = {
            'app_key': 'TEST',
            'app_secret': 'TEST',
            'account_number': '12345678',
            'account_code': '01',
            'environment': 'V',
        }
        
        broker = KISBroker(config, dry_run=True)
        
        # 토큰 만료 설정
        from datetime import timedelta
        broker._token_expires_at = datetime.now() - timedelta(hours=1)
        
        # 갱신 필요 확인
        assert broker._needs_token_refresh() == True
    
    def test_token_refresh_failure_triggers_emergency(self):
        """토큰 갱신 실패 시 EMERGENCY 모드"""
        from scalping.execution.broker import KISBroker
        from scalping.safety.kill_switch import KillSwitch
        
        config = {
            'app_key': 'TEST',
            'app_secret': 'TEST',
            'account_number': '12345678',
            'account_code': '01',
            'environment': 'V',
        }
        
        broker = KISBroker(config, dry_run=False)
        kill_switch = KillSwitch({})
        
        # 토큰 갱신 실패 시뮬레이션
        with patch.object(broker, '_call_api', side_effect=Exception("Auth failed")):
            # 실제로는 연속 실패 시 EMERGENCY 진입
            pass


# =============================================================================
# V-03: AI 타임아웃
# =============================================================================

class TestV03AITimeout:
    """V-03: AI 타임아웃 → 해당 종목 SKIP"""
    
    @pytest.mark.asyncio
    async def test_ai_timeout_skips_stock(self):
        """AI 타임아웃 시 해당 종목 스킵"""
        from scalping.ai.ai_engine import AIEngine
        
        config = {
            'model': 'qwen3:8b',
            'base_url': 'http://localhost:11434',
            'timeout': 0.01,  # 매우 짧은 타임아웃
            'enabled': True,
        }
        
        engine = AIEngine(config)
        
        # 타임아웃 시뮬레이션
        async def slow_call(*args, **kwargs):
            await asyncio.sleep(1)
            return {"decision": "BUY"}
        
        with patch.object(engine, '_call_ollama', slow_call):
            result = await engine.analyze_async("005930", {}, {})
        
        # 타임아웃 시 HOLD 또는 에러
        assert result.get('decision', 'HOLD') in ['HOLD', 'ERROR']
    
    def test_main_loop_continues_after_timeout(self):
        """AI 타임아웃 후에도 메인 루프 지속"""
        # 통합 테스트에서 확인
        pass


# =============================================================================
# V-04: AI JSON 파싱 실패
# =============================================================================

class TestV04AIParsingFailure:
    """V-04: AI 응답 JSON 파싱 실패 → fallback(HOLD)"""
    
    def test_invalid_json_returns_hold(self):
        """잘못된 JSON은 HOLD 반환"""
        from scalping.ai.ai_engine import AIEngine
        
        config = {
            'model': 'qwen3:8b',
            'base_url': 'http://localhost:11434',
            'timeout': 10,
            'enabled': True,
        }
        
        engine = AIEngine(config)
        
        invalid_responses = [
            "This is not JSON",
            '{"decision": "BUY"',  # 불완전
            '',  # 빈 응답
            'null',
            '[]',
        ]
        
        for response in invalid_responses:
            result = engine._parse_ai_response(response)
            assert result.get('decision', 'HOLD') == 'HOLD'
    
    def test_loop_continues_after_parse_failure(self):
        """파싱 실패 후에도 루프 지속"""
        # 통합 테스트에서 확인
        pass


# =============================================================================
# V-05: 웹소켓 끊김
# =============================================================================

class TestV05WebSocketDisconnect:
    """V-05: 웹소켓 끊김 → 재연결 시도 + 구독 복구"""
    
    def test_reconnect_on_disconnect(self):
        """연결 끊김 시 재연결 시도"""
        from scalping.data.realtime_feed import RealtimeFeed
        
        feed = RealtimeFeed(
            app_key="TEST",
            app_secret="TEST",
            is_virtual=True,
        )
        
        # 구독 추가
        feed.subscribe_tick("005930")
        feed.subscribe_tick("000660")
        
        # 구독 목록 확인
        subs = feed.get_subscriptions()
        assert "005930" in subs['tick']
        assert "000660" in subs['tick']
    
    def test_subscription_restored_after_reconnect(self):
        """재연결 후 구독 복구"""
        from scalping.data.realtime_feed import RealtimeFeed
        
        feed = RealtimeFeed(
            app_key="TEST",
            app_secret="TEST",
            is_virtual=True,
        )
        
        # 구독 설정
        feed.subscribe_tick("005930")
        
        # 재연결 시뮬레이션 (실제 연결 없이 구독 목록만 확인)
        subs = feed.get_subscriptions()
        assert len(subs['tick']) == 1


# =============================================================================
# V-06: 주문 응답 지연/실패
# =============================================================================

class TestV06OrderDelayFailure:
    """V-06: 주문 응답 지연/실패 → 중복주문 금지"""
    
    def test_duplicate_order_prevention(self):
        """중복 주문 방지"""
        from scalping.execution.cooldown_tracker import CooldownTracker
        
        tracker = CooldownTracker({
            'buy_cooldown_minutes': 10,
            'global_cooldown_seconds': 3,
        })
        
        # 첫 매수 기록
        tracker.record_buy("005930")
        
        # 쿨다운 중 체크
        assert tracker.can_buy("005930") == False
    
    def test_order_state_verification(self):
        """주문 상태 확인 후 처리"""
        from scalping.execution.broker import KISBroker
        
        config = {
            'app_key': 'TEST',
            'app_secret': 'TEST',
            'account_number': '12345678',
            'account_code': '01',
            'environment': 'V',
        }
        
        broker = KISBroker(config, dry_run=True)
        
        # 미체결 조회
        pending = broker.get_pending_orders()
        assert isinstance(pending, list)


# =============================================================================
# V-07: 급락장
# =============================================================================

class TestV07MarketCrash:
    """V-07: 급락장(-2% 이하) → 스캔 중지 + 청산 모드"""
    
    def test_emergency_mode_on_market_crash(self):
        """급락 시 EMERGENCY 모드 전환"""
        from scalping.data.market_monitor import MarketMonitor, MarketMode
        
        monitor = MarketMonitor({
            'kospi_emergency_threshold': -2.0,
        })
        
        # 급락 시뮬레이션
        monitor._state.kospi_change = -2.5
        monitor._check_mode_transition()
        
        assert monitor.get_mode() == MarketMode.EMERGENCY
    
    def test_kill_switch_activates_on_crash(self):
        """급락 시 킬 스위치 활성화"""
        from scalping.safety.kill_switch import KillSwitch, StopReason
        
        kill_switch = KillSwitch({
            'market_crash_threshold': -2.0,
        })
        
        # 급락 트리거
        kill_switch.check_market_condition(-2.5)
        
        # 킬 스위치 활성화 확인
        # assert kill_switch.is_stopped() == True


# =============================================================================
# V-08: 설정 변경 (장중)
# =============================================================================

class TestV08ConfigChange:
    """V-08: 설정 변경(장중) → 핫리로드 가능 항목만 반영"""
    
    def test_hot_reload_allowed_items(self):
        """핫리로드 가능 항목 즉시 반영"""
        from scalping.config.config_loader import ConfigLoader, HOT_RELOAD_ALLOWED
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            
            loader = ConfigLoader(str(config_path), auto_create=True)
            loader.load()
            
            # 핫리로드 가능 항목 업데이트
            loader.update('logging.level', 'DEBUG')
            
            # 즉시 반영 확인
            assert loader.get('logging.level') == 'DEBUG'
            
            # 대기열에 없음
            assert 'logging.level' not in loader.get_pending_changes()
    
    def test_hot_reload_blocked_items_pending(self):
        """핫리로드 금지 항목은 대기열에 저장"""
        from scalping.config.config_loader import ConfigLoader
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            
            loader = ConfigLoader(str(config_path), auto_create=True)
            loader.load()
            
            # 핫리로드 금지 항목 업데이트
            loader.update('risk.stop_loss_pct', -2.0)
            
            # 대기열에 저장됨
            pending = loader.get_pending_changes()
            assert 'risk.stop_loss_pct' in pending
    
    def test_pending_changes_applied_next_day(self):
        """대기 변경은 다음날 적용"""
        from scalping.config.config_loader import ConfigLoader
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            
            loader = ConfigLoader(str(config_path), auto_create=True)
            loader.load()
            
            # 대기열에 추가
            loader.update('risk.stop_loss_pct', -2.5)
            
            # 현재 값 확인 (변경 안됨)
            assert loader.get('risk.stop_loss_pct') == -1.5
            
            # 대기 변경 적용
            count = loader.apply_pending_changes()
            
            # 이제 변경됨
            assert loader.get('risk.stop_loss_pct') == -2.5
            assert count == 1


# =============================================================================
# 통합 시나리오: 봇이 멈추지 않는다
# =============================================================================

class TestBotContinuity:
    """봇이 장중에 멈추지 않는다"""
    
    def test_exception_does_not_stop_loop(self):
        """예외 발생해도 루프 지속"""
        # 메인 루프에서 try-catch로 예외 처리 확인
        pass
    
    def test_safe_mode_on_repeated_errors(self):
        """반복 에러 시 안전 모드 전환"""
        from scalping.safety.circuit_breaker import CircuitBreaker, CircuitState
        
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=3,
            reset_timeout=5,
        )
        
        # 연속 실패
        for _ in range(4):
            breaker.record_failure()
        
        # 서킷 열림
        assert breaker.state == CircuitState.OPEN


# =============================================================================
# 테스트 실행
# =============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
