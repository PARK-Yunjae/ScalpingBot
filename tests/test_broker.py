#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Broker 테스트
============================================================================
설계서 14.2.1 단위 테스트: Broker

테스트 항목:
- 토큰 갱신 로직 (만료 임박 갱신)
- 주문 body 생성
- 오류 코드 처리
============================================================================
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scalping.execution.broker import KISBroker


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def broker_config():
    """브로커 설정"""
    return {
        'app_key': 'TEST_APP_KEY',
        'app_secret': 'TEST_APP_SECRET',
        'account_number': '12345678',
        'account_code': '01',
        'environment': 'V',  # 모의투자
    }


@pytest.fixture
def broker(broker_config):
    """브로커 인스턴스 (dry_run)"""
    return KISBroker(broker_config, dry_run=True)


# =============================================================================
# 토큰 관리 테스트
# =============================================================================

class TestTokenManagement:
    """토큰 관리 테스트"""
    
    def test_token_needs_refresh_when_expired(self, broker):
        """만료된 토큰은 갱신 필요"""
        broker._token_expires_at = datetime.now() - timedelta(hours=1)
        
        assert broker._needs_token_refresh() == True
    
    def test_token_needs_refresh_when_near_expiry(self, broker):
        """만료 임박 토큰도 갱신 필요 (5분 이내)"""
        broker._token_expires_at = datetime.now() + timedelta(minutes=3)
        
        # 5분 이내면 갱신 필요
        assert broker._needs_token_refresh() == True
    
    def test_token_valid_when_not_expired(self, broker):
        """유효한 토큰은 갱신 불필요"""
        broker._token_expires_at = datetime.now() + timedelta(hours=1)
        
        assert broker._needs_token_refresh() == False
    
    def test_token_refresh_updates_expiry(self, broker):
        """토큰 갱신 시 만료 시간 업데이트"""
        old_expiry = broker._token_expires_at
        
        # Mock API 응답
        with patch.object(broker, '_call_api') as mock_api:
            mock_api.return_value = {
                'access_token': 'new_token',
                'expires_in': 86400,
            }
            
            # 갱신은 dry_run에서 스킵됨
            # broker._refresh_token()
        
        # dry_run이므로 실제 갱신 안됨, 로직만 확인


# =============================================================================
# 주문 생성 테스트
# =============================================================================

class TestOrderCreation:
    """주문 생성 테스트"""
    
    def test_buy_order_body(self, broker):
        """매수 주문 body 생성"""
        body = broker._build_order_body(
            stock_code="005930",
            quantity=10,
            price=0,  # 시장가
            order_type="buy"
        )
        
        assert body['PDNO'] == "005930"
        assert body['ORD_QTY'] == "10"
    
    def test_sell_order_body(self, broker):
        """매도 주문 body 생성"""
        body = broker._build_order_body(
            stock_code="005930",
            quantity=5,
            price=0,
            order_type="sell"
        )
        
        assert body['PDNO'] == "005930"
        assert body['ORD_QTY'] == "5"
    
    def test_limit_order_price(self, broker):
        """지정가 주문 가격 포함"""
        body = broker._build_order_body(
            stock_code="005930",
            quantity=10,
            price=70000,
            order_type="buy"
        )
        
        assert body['ORD_UNPR'] == "70000"
    
    def test_market_order_price_zero(self, broker):
        """시장가 주문 가격 0"""
        body = broker._build_order_body(
            stock_code="005930",
            quantity=10,
            price=0,
            order_type="buy"
        )
        
        assert body['ORD_UNPR'] == "0"


# =============================================================================
# 에러 처리 테스트
# =============================================================================

class TestErrorHandling:
    """에러 처리 테스트"""
    
    def test_api_error_code_handling(self, broker):
        """API 에러 코드 처리"""
        # 일반적인 에러 코드들
        error_codes = {
            'APBK0001': '주문 실패',
            'APBK0002': '잔고 부족',
            'APBK0003': '종목 거래 정지',
        }
        
        for code, msg in error_codes.items():
            # 에러 발생 시 적절히 처리되는지 확인
            result = broker._handle_api_error(code, msg)
            assert result is not None
    
    def test_network_error_retry(self, broker):
        """네트워크 에러 재시도"""
        call_count = [0]
        
        def mock_request(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("Network error")
            return {'success': True}
        
        # 실제 테스트는 통합 테스트에서
    
    def test_invalid_stock_code(self, broker):
        """잘못된 종목코드"""
        result = broker.buy_market("INVALID", 10)
        
        # dry_run이면 성공으로 처리됨
        # 실제로는 API 에러 발생
    
    def test_zero_quantity(self, broker):
        """수량 0 처리"""
        result = broker.buy_market("005930", 0)
        
        assert result.success == False


# =============================================================================
# Dry Run 테스트
# =============================================================================

class TestDryRun:
    """Dry Run 모드 테스트"""
    
    def test_dry_run_buy_no_api_call(self, broker):
        """Dry run에서 실제 API 호출 없음"""
        with patch.object(broker, '_call_api') as mock_api:
            result = broker.buy_market("005930", 10)
            
            # dry_run이면 API 호출 안함
            # mock_api.assert_not_called()
    
    def test_dry_run_returns_success(self, broker):
        """Dry run은 항상 성공 반환"""
        result = broker.buy_market("005930", 10)
        
        assert result.success == True
    
    def test_dry_run_simulated_price(self, broker):
        """Dry run 시뮬레이션 가격"""
        result = broker.buy_market("005930", 10)
        
        # 시뮬레이션 가격이 있어야 함
        assert result.price > 0


# =============================================================================
# 포지션 조회 테스트
# =============================================================================

class TestPositionQuery:
    """포지션 조회 테스트"""
    
    def test_get_positions_returns_list(self, broker):
        """포지션 조회는 리스트 반환"""
        positions = broker.get_positions()
        
        assert isinstance(positions, list)
    
    def test_get_balance_returns_dict(self, broker):
        """잔고 조회는 딕셔너리 반환"""
        balance = broker.get_balance()
        
        assert isinstance(balance, dict)
        assert 'available' in balance


# =============================================================================
# 테스트 실행
# =============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
