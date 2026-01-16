#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Config Loader 테스트
============================================================================
설계서 15.1~15.3 설정 관리 테스트

테스트 항목:
- YAML 로드/저장
- 스키마 검증
- 기본값 병합
- 핫리로드
- 원자적 저장
============================================================================
"""

import pytest
import yaml
import time
import tempfile
import threading
from pathlib import Path

import sys
sys.path.insert(0, '/home/claude/ScalpingBot')

from scalping.config.config_loader import (
    ConfigLoader, DEFAULT_CONFIG, 
    HOT_RELOAD_ALLOWED, HOT_RELOAD_BLOCKED
)
# =============================================================================
# 테스트 간 딜레이 (Lock 데드락 방지)
# =============================================================================

@pytest.fixture(autouse=True)
def delay_between_tests():
    """각 테스트 전후 0.3초 대기"""
    time.sleep(3)
    yield
    time.sleep(3)

# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_config_dir():
    """임시 설정 디렉토리"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def loader(temp_config_dir):
    """ConfigLoader 인스턴스"""
    config_path = Path(temp_config_dir) / "config.yaml"
    return ConfigLoader(str(config_path), auto_create=True)


# =============================================================================
# 로드 테스트
# =============================================================================

class TestLoad:
    """설정 로드 테스트"""
    
    def test_load_creates_default_config(self, temp_config_dir):
        """파일 없으면 기본 설정 생성"""
        config_path = Path(temp_config_dir) / "config.yaml"
        loader = ConfigLoader(str(config_path), auto_create=True)
        
        config = loader.load()
        
        assert config_path.exists()
        assert config['mode'] == DEFAULT_CONFIG['mode']
    
    def test_load_existing_config(self, temp_config_dir):
        """기존 설정 파일 로드"""
        config_path = Path(temp_config_dir) / "config.yaml"
        
        custom_config = {'mode': 'LIVE_MICRO', 'ai': {'model': 'custom'}}
        with open(config_path, 'w') as f:
            yaml.dump(custom_config, f)
        
        loader = ConfigLoader(str(config_path))
        config = loader.load()
        
        assert config['mode'] == 'LIVE_MICRO'
        assert config['ai']['model'] == 'custom'
    
    def test_load_merges_with_defaults(self, temp_config_dir):
        """누락된 항목은 기본값으로 채움"""
        config_path = Path(temp_config_dir) / "config.yaml"
        
        partial_config = {'mode': 'LIVE'}
        with open(config_path, 'w') as f:
            yaml.dump(partial_config, f)
        
        loader = ConfigLoader(str(config_path))
        config = loader.load()
        
        # 설정된 값
        assert config['mode'] == 'LIVE'
        
        # 기본값으로 채워진 값
        assert 'ai' in config
        assert config['ai']['model'] == DEFAULT_CONFIG['ai']['model']


# =============================================================================
# 검증 테스트
# =============================================================================

class TestValidation:
    """스키마 검증 테스트"""
    
    def test_valid_config_passes(self, loader):
        """유효한 설정 통과"""
        config = loader.load()
        errors = loader._validate_schema(config)
        
        assert len(errors) == 0
    
    def test_invalid_mode_fails(self, loader):
        """잘못된 모드 실패"""
        config = loader.load()
        config['mode'] = 'INVALID_MODE'
        
        errors = loader._validate_schema(config)
        
        assert len(errors) > 0
        assert any('mode' in e for e in errors)
    
    def test_out_of_range_value_fails(self, loader):
        """범위 벗어난 값 실패"""
        config = loader.load()
        config['risk']['stop_loss_pct'] = 5.0  # 양수는 안됨
        
        errors = loader._validate_schema(config)
        
        assert len(errors) > 0
    
    def test_wrong_type_fails(self, loader):
        """잘못된 타입 실패"""
        config = loader.load()
        config['ai']['timeout'] = "not_a_number"
        
        errors = loader._validate_schema(config)
        
        assert len(errors) > 0


# =============================================================================
# 저장 테스트
# =============================================================================

class TestSave:
    """설정 저장 테스트"""
    
    def test_save_creates_file(self, loader):
        """저장 시 파일 생성"""
        config = loader.load()
        result = loader.save(config)
        
        assert result == True
        assert loader.config_path.exists()
    
    def test_save_is_atomic(self, temp_config_dir):
        """원자적 저장 (임시 파일 사용)"""
        config_path = Path(temp_config_dir) / "config.yaml"
        loader = ConfigLoader(str(config_path), auto_create=True)
        loader.load()
        
        # 저장
        loader.save()
        
        # 백업 파일 확인
        backup_path = config_path.with_suffix('.yaml.bak')
        # 두 번째 저장부터 백업 생성
        loader.update('logging.level', 'DEBUG')
        assert backup_path.exists()
    
    def test_save_validates_before_write(self, loader):
        """저장 전 검증"""
        config = loader.load()
        config['mode'] = 'INVALID'
        
        result = loader.save(config)
        
        # 검증 실패로 저장 안됨
        assert result == False


# =============================================================================
# 업데이트 테스트
# =============================================================================

class TestUpdate:
    """개별 설정 업데이트 테스트"""
    
    def test_update_single_value(self, loader):
        """단일 값 업데이트"""
        loader.load()
        loader.update('logging.level', 'DEBUG')
        
        assert loader.get('logging.level') == 'DEBUG'
    
    def test_update_nested_value(self, loader):
        """중첩 값 업데이트"""
        loader.load()
        loader.update('ai.timeout', 15)
        
        assert loader.get('ai.timeout') == 15


# =============================================================================
# 핫리로드 테스트
# =============================================================================

class TestHotReload:
    """핫리로드 테스트"""
    
    def test_hot_reload_allowed_list(self):
        """핫리로드 허용 목록 확인"""
        assert 'logging.level' in HOT_RELOAD_ALLOWED
        assert 'discord.webhook_url' in HOT_RELOAD_ALLOWED
    
    def test_hot_reload_blocked_list(self):
        """핫리로드 금지 목록 확인"""
        assert 'mode' in HOT_RELOAD_BLOCKED
        assert 'risk.stop_loss_pct' in HOT_RELOAD_BLOCKED
    
    def test_blocked_item_goes_to_pending(self, loader):
        """금지 항목은 대기열로"""
        loader.load()
        loader.update('risk.stop_loss_pct', -2.0)
        
        pending = loader.get_pending_changes()
        assert 'risk.stop_loss_pct' in pending
    
    def test_allowed_item_applied_immediately(self, loader):
        """허용 항목은 즉시 적용"""
        loader.load()
        loader.update('logging.level', 'WARNING')
        
        pending = loader.get_pending_changes()
        assert 'logging.level' not in pending
        assert loader.get('logging.level') == 'WARNING'
    
    def test_apply_pending_changes(self, loader):
        """대기 변경 적용"""
        loader.load()
        loader.update('risk.stop_loss_pct', -2.5)
        loader.update('mode', 'LIVE')
        
        # 아직 적용 안됨
        assert loader.get('risk.stop_loss_pct') == -1.5
        
        # 적용
        count = loader.apply_pending_changes()
        
        assert count == 2
        assert loader.get('risk.stop_loss_pct') == -2.5
        assert loader.get('mode') == 'LIVE'
    
    def test_hot_reload_callback(self, temp_config_dir):
        """핫리로드 콜백 호출"""
        config_path = Path(temp_config_dir) / "config.yaml"
        loader = ConfigLoader(str(config_path), auto_create=True)
        loader.load()
        
        callback_called = [False]
        
        def on_change(config):
            callback_called[0] = True
        
        # 핫리로드 시작
        loader.start_hot_reload(callback=on_change, interval=0.1)
        
        time.sleep(0.2)
        
        # 파일 수정
        config = loader.get_all()
        config['logging']['level'] = 'DEBUG'
        with open(config_path, 'w') as f:
            yaml.dump(config, f)
        
        time.sleep(0.3)
        
        loader.stop_hot_reload()
        
        # 콜백 호출됨
        assert callback_called[0] == True


# =============================================================================
# 변경 이력 테스트
# =============================================================================

class TestChangeHistory:
    """변경 이력 테스트"""
    
    def test_history_recorded_on_save(self, loader):
        """저장 시 이력 기록"""
        loader.load()
        loader.update('logging.level', 'DEBUG')
        
        history = loader.get_change_history()
        
        assert len(history) > 0
        assert 'timestamp' in history[-1]
        assert 'action' in history[-1]


# =============================================================================
# 테스트 실행
# =============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
