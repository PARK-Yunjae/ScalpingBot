#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Config Loader (설정 로더)
============================================================================
SSOT (Single Source of Truth) 설정 관리

핵심 기능:
- YAML 설정 파일 로드/저장
- 스키마 검증 (타입, 범위)
- 핫리로드 (파일 변경 감지)
- 기본값 폴백
- 원자적 저장 (atomic write)
- 변경 이력 관리

설계서 15.1~15.3 준수:
- SSOT는 config.yaml 1개로 고정
- 장중 즉시 반영 가능 항목 vs 금지 항목 구분
- 핫리로드 지원 (5~10초 주기)

사용법:
    loader = ConfigLoader("config/config.yaml")
    config = loader.load()
    
    # 핫리로드 시작
    loader.start_hot_reload(callback=on_config_change)
============================================================================
"""

import os
import yaml
import json
import time
import shutil
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
from dataclasses import dataclass
from copy import deepcopy

# 로거
logger = logging.getLogger('ScalpingBot.ConfigLoader')


# =============================================================================
# 기본 설정값
# =============================================================================

DEFAULT_CONFIG = {
    # 모드
    'mode': 'LIVE_DATA_ONLY',  # LIVE_DATA_ONLY, LIVE_MICRO, LIVE
    
    # AI 설정
    'ai': {
        'model': 'qwen3:8b',
        'base_url': 'http://localhost:11434',
        'timeout': 10,
        'temperature': 0.3,
        'enabled': True,
    },
    
    # 전략 설정
    'strategy': {
        'min_score': 65,
        'min_confidence': 0.7,
        'weights': {
            'cci': 25,
            'price_change': 20,
            'ma_distance': 15,
            'consecutive': 15,
            'volume': 15,
            'candle': 10,
        },
    },
    
    # 리스크 설정
    'risk': {
        'stop_loss_pct': -1.5,
        'take_profit_pct': 1.0,
        'trailing_stop_pct': 0.5,
        'max_positions': 5,
        'position_size': 500000,
        'daily_loss_limit': -3.0,
        'max_consecutive_losses': 5,
    },
    
    # 쿨다운 설정
    'cooldown': {
        'buy_cooldown_minutes': 10,
        'global_cooldown_seconds': 3,
    },
    
    # 유니버스 설정
    'universe': {
        'top_n': 100,
        'min_price': 1000,
        'max_price': 500000,
        'min_market_cap': 500,  # 억원
        'change_min': -5.0,
        'change_max': 15.0,
    },
    
    # 시장 모니터
    'market': {
        'kospi_emergency_threshold': -2.0,
        'update_interval': 60,
    },
    
    # 로깅
    'logging': {
        'level': 'INFO',
        'file': True,
        'console': True,
        'max_days': 30,
    },
    
    # Discord
    'discord': {
        'enabled': False,
        'webhook_url': '',
        'mention_on_error': True,
    },
    
    # 브로커
    'broker': {
        'environment': 'V',  # V: 모의, P: 실전
        'order_type': 'market',  # market, limit
    },
}

# 핫리로드 가능 항목 (장중 즉시 반영 가능)
HOT_RELOAD_ALLOWED = [
    'logging.level',
    'logging.console',
    'discord.webhook_url',
    'discord.enabled',
    'discord.mention_on_error',
    'ai.model',
    'ai.timeout',
    'ai.temperature',
    'strategy.min_score',
    'strategy.min_confidence',
    'cooldown.buy_cooldown_minutes',
    'cooldown.global_cooldown_seconds',
]

# 핫리로드 금지 항목 (다음 거래일부터 적용)
HOT_RELOAD_BLOCKED = [
    'mode',
    'risk.stop_loss_pct',
    'risk.take_profit_pct',
    'risk.max_positions',
    'risk.position_size',
    'risk.daily_loss_limit',
    'universe.*',
    'broker.environment',
]


# =============================================================================
# 스키마 정의
# =============================================================================

@dataclass
class SchemaField:
    """설정 필드 스키마"""
    path: str
    type: type
    min_value: Any = None
    max_value: Any = None
    required: bool = False
    default: Any = None
    choices: List[Any] = None


# 스키마 검증 규칙
SCHEMA = [
    SchemaField('mode', str, choices=['LIVE_DATA_ONLY', 'LIVE_MICRO', 'LIVE']),
    SchemaField('ai.model', str, required=True),
    SchemaField('ai.timeout', int, min_value=1, max_value=60),
    SchemaField('ai.temperature', float, min_value=0, max_value=2),
    SchemaField('strategy.min_score', int, min_value=0, max_value=100),
    SchemaField('strategy.min_confidence', float, min_value=0, max_value=1),
    SchemaField('risk.stop_loss_pct', float, min_value=-10, max_value=0),
    SchemaField('risk.take_profit_pct', float, min_value=0, max_value=20),
    SchemaField('risk.max_positions', int, min_value=1, max_value=20),
    SchemaField('risk.position_size', int, min_value=10000, max_value=10000000),
    SchemaField('risk.daily_loss_limit', float, min_value=-20, max_value=0),
    SchemaField('cooldown.buy_cooldown_minutes', int, min_value=1, max_value=60),
    SchemaField('logging.level', str, choices=['DEBUG', 'INFO', 'WARNING', 'ERROR']),
]


# =============================================================================
# 설정 로더 클래스
# =============================================================================

class ConfigLoader:
    """
    설정 로더 with 핫리로드
    
    SSOT (Single Source of Truth) 설정 관리를 담당합니다.
    """
    
    def __init__(
        self,
        config_path: str = "config/config.yaml",
        secrets_path: str = "config/secrets.yaml",
        auto_create: bool = True,
    ):
        """
        초기화
        
        Args:
            config_path: 설정 파일 경로
            secrets_path: 비밀 설정 파일 경로
            auto_create: 파일 없으면 생성
        """
        self.config_path = Path(config_path)
        self.secrets_path = Path(secrets_path)
        self.auto_create = auto_create
        
        # 현재 설정
        self._config: Dict = {}
        self._secrets: Dict = {}
        self._last_mtime: float = 0
        
        # 핫리로드
        self._hot_reload_thread: Optional[threading.Thread] = None
        self._hot_reload_running = False
        self._hot_reload_callback: Optional[Callable] = None
        self._hot_reload_interval = 5  # 초
        
        # 대기 중인 변경 (다음날 적용)
        self._pending_changes: Dict = {}
        
        # 변경 이력
        self._change_history: List[Dict] = []
        
        # 락
        self._lock = threading.RLock()
        
        logger.info(f"ConfigLoader 초기화: {config_path}")
    
    # =========================================================================
    # 로드
    # =========================================================================
    
    def load(self, force_reload: bool = False) -> Dict:
        """
        설정 로드
        
        Args:
            force_reload: 강제 리로드
        
        Returns:
            설정 딕셔너리
        """
        with self._lock:
            # 이미 로드됨
            if self._config and not force_reload:
                return deepcopy(self._config)
            
            # 파일 존재 확인
            if not self.config_path.exists():
                if self.auto_create:
                    logger.warning(f"설정 파일 없음, 기본값 생성: {self.config_path}")
                    self._create_default_config()
                else:
                    logger.error(f"설정 파일 없음: {self.config_path}")
                    self._config = deepcopy(DEFAULT_CONFIG)
                    return self._config
            
            try:
                # YAML 로드
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded = yaml.safe_load(f) or {}
                
                # 기본값과 병합
                self._config = self._merge_with_defaults(loaded)
                
                # 스키마 검증
                errors = self._validate_schema(self._config)
                if errors:
                    for err in errors:
                        logger.warning(f"설정 검증 오류: {err}")
                
                # mtime 저장
                self._last_mtime = self.config_path.stat().st_mtime
                
                logger.info(f"설정 로드 완료: {self.config_path}")
                
            except yaml.YAMLError as e:
                logger.error(f"YAML 파싱 오류: {e}")
                self._config = deepcopy(DEFAULT_CONFIG)
            
            except Exception as e:
                logger.error(f"설정 로드 오류: {e}")
                self._config = deepcopy(DEFAULT_CONFIG)
            
            return deepcopy(self._config)
    
    def load_secrets(self) -> Dict:
        """
        비밀 설정 로드 (API 키 등)
        
        Returns:
            비밀 설정 딕셔너리
        """
        if self._secrets:
            return deepcopy(self._secrets)
        
        if not self.secrets_path.exists():
            logger.warning(f"비밀 설정 파일 없음: {self.secrets_path}")
            return {}
        
        try:
            with open(self.secrets_path, 'r', encoding='utf-8') as f:
                self._secrets = yaml.safe_load(f) or {}
            
            logger.info("비밀 설정 로드 완료")
            return deepcopy(self._secrets)
        
        except Exception as e:
            logger.error(f"비밀 설정 로드 오류: {e}")
            return {}
    
    def _create_default_config(self):
        """기본 설정 파일 생성"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, allow_unicode=True)
        
        logger.info(f"기본 설정 파일 생성: {self.config_path}")
    
    def _merge_with_defaults(self, loaded: Dict) -> Dict:
        """기본값과 병합"""
        return self._deep_merge(deepcopy(DEFAULT_CONFIG), loaded)
    
    def _deep_merge(self, base: Dict, override: Dict) -> Dict:
        """딥 머지"""
        result = deepcopy(base)
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    # =========================================================================
    # 검증
    # =========================================================================
    
    def _validate_schema(self, config: Dict) -> List[str]:
        """스키마 검증"""
        errors = []
        
        for field in SCHEMA:
            value = self._get_nested(config, field.path)
            
            # 필수값 체크
            if value is None:
                if field.required:
                    errors.append(f"필수 항목 누락: {field.path}")
                continue
            
            # 타입 체크
            if not isinstance(value, field.type):
                errors.append(f"타입 오류: {field.path} (기대: {field.type.__name__})")
                continue
            
            # 범위 체크
            if field.min_value is not None and value < field.min_value:
                errors.append(f"최소값 미달: {field.path} ({value} < {field.min_value})")
            
            if field.max_value is not None and value > field.max_value:
                errors.append(f"최대값 초과: {field.path} ({value} > {field.max_value})")
            
            # 선택지 체크
            if field.choices and value not in field.choices:
                errors.append(f"잘못된 값: {field.path} ({value} not in {field.choices})")
        
        return errors
    
    def _get_nested(self, d: Dict, path: str) -> Any:
        """중첩 딕셔너리 값 가져오기"""
        keys = path.split('.')
        value = d
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        
        return value
    
    def _set_nested(self, d: Dict, path: str, value: Any):
        """중첩 딕셔너리 값 설정"""
        keys = path.split('.')
        
        for key in keys[:-1]:
            if key not in d:
                d[key] = {}
            d = d[key]
        
        d[keys[-1]] = value
    
    # =========================================================================
    # 저장
    # =========================================================================
    
    def save(self, config: Dict = None) -> bool:
        """
        설정 저장 (원자적)
        
        Args:
            config: 저장할 설정 (None이면 현재 설정)
        
        Returns:
            성공 여부
        """
        with self._lock:
            config = config or self._config
            
            if not config:
                logger.error("저장할 설정 없음")
                return False
            
            # 스키마 검증
            errors = self._validate_schema(config)
            if errors:
                logger.error(f"설정 검증 실패: {errors}")
                return False
            
            try:
                # 1. 임시 파일에 저장
                tmp_path = self.config_path.with_suffix('.yaml.tmp')
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                
                # 2. 백업 생성
                if self.config_path.exists():
                    backup_path = self.config_path.with_suffix('.yaml.bak')
                    shutil.copy2(self.config_path, backup_path)
                
                # 3. 원자적 교체
                tmp_path.replace(self.config_path)
                
                # 4. 설정 업데이트
                self._config = config
                self._last_mtime = self.config_path.stat().st_mtime
                
                # 5. 변경 이력 기록
                self._record_change("save")
                
                logger.info("설정 저장 완료")
                return True
            
            except Exception as e:
                logger.error(f"설정 저장 오류: {e}")
                return False
    
    def update(self, path: str, value: Any) -> bool:
        """
        개별 설정 업데이트
        
        Args:
            path: 설정 경로 (예: "risk.stop_loss_pct")
            value: 새 값
        
        Returns:
            성공 여부
        """
        with self._lock:
            # 핫리로드 가능 여부 체크
            if self._is_hot_reload_blocked(path):
                logger.warning(f"장중 변경 불가 항목, 대기열에 추가: {path}")
                self._pending_changes[path] = value
                return True
            
            # 설정 업데이트
            self._set_nested(self._config, path, value)
            
            # 저장
            return self.save()
    
    def apply_pending_changes(self) -> int:
        """
        대기 중인 변경 적용 (장 시작 전 호출)
        
        Returns:
            적용된 변경 수
        """
        with self._lock:
            if not self._pending_changes:
                return 0
            
            count = 0
            for path, value in self._pending_changes.items():
                self._set_nested(self._config, path, value)
                count += 1
                logger.info(f"대기 변경 적용: {path} = {value}")
            
            self._pending_changes.clear()
            self.save()
            
            return count
    
    def _is_hot_reload_blocked(self, path: str) -> bool:
        """핫리로드 금지 항목인지 확인"""
        for blocked in HOT_RELOAD_BLOCKED:
            if blocked.endswith('.*'):
                prefix = blocked[:-2]
                if path.startswith(prefix):
                    return True
            elif path == blocked:
                return True
        return False
    
    def _record_change(self, action: str):
        """변경 이력 기록"""
        self._change_history.append({
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'config_hash': hash(str(self._config)),
        })
        
        # 최대 100개 유지
        if len(self._change_history) > 100:
            self._change_history = self._change_history[-100:]
    
    # =========================================================================
    # 핫리로드
    # =========================================================================
    
    def start_hot_reload(
        self,
        callback: Callable[[Dict], None] = None,
        interval: float = 5,
    ):
        """
        핫리로드 시작
        
        Args:
            callback: 변경 감지 시 콜백
            interval: 체크 간격 (초)
        """
        if self._hot_reload_running:
            logger.warning("핫리로드 이미 실행 중")
            return
        
        self._hot_reload_callback = callback
        self._hot_reload_interval = interval
        self._hot_reload_running = True
        
        self._hot_reload_thread = threading.Thread(
            target=self._hot_reload_loop,
            name="ConfigHotReload",
            daemon=True,
        )
        self._hot_reload_thread.start()
        
        logger.info(f"핫리로드 시작 (간격: {interval}초)")
    
    def stop_hot_reload(self):
        """핫리로드 중지"""
        self._hot_reload_running = False
        
        if self._hot_reload_thread and self._hot_reload_thread.is_alive():
            self._hot_reload_thread.join(timeout=5)
        
        logger.info("핫리로드 중지")
    
    def _hot_reload_loop(self):
        """핫리로드 루프"""
        while self._hot_reload_running:
            try:
                # 파일 변경 체크
                if self.config_path.exists():
                    current_mtime = self.config_path.stat().st_mtime
                    
                    if current_mtime > self._last_mtime:
                        logger.info("설정 파일 변경 감지")
                        
                        # 리로드
                        old_config = deepcopy(self._config)
                        new_config = self.load(force_reload=True)
                        
                        # 변경된 항목 확인
                        changes = self._get_config_diff(old_config, new_config)
                        
                        # 핫리로드 가능 항목만 적용
                        applied = []
                        pending = []
                        
                        for path, (old_val, new_val) in changes.items():
                            if self._is_hot_reload_blocked(path):
                                pending.append(path)
                                # 원래 값으로 복원
                                self._set_nested(self._config, path, old_val)
                                self._pending_changes[path] = new_val
                            else:
                                applied.append(path)
                        
                        if applied:
                            logger.info(f"핫리로드 적용: {applied}")
                        
                        if pending:
                            logger.warning(f"다음날 적용 대기: {pending}")
                        
                        # 콜백 호출
                        if self._hot_reload_callback and applied:
                            try:
                                self._hot_reload_callback(self._config)
                            except Exception as e:
                                logger.error(f"핫리로드 콜백 오류: {e}")
                
                time.sleep(self._hot_reload_interval)
            
            except Exception as e:
                logger.error(f"핫리로드 루프 오류: {e}")
                time.sleep(self._hot_reload_interval)
    
    def _get_config_diff(self, old: Dict, new: Dict, prefix: str = "") -> Dict:
        """설정 차이 추출"""
        diff = {}
        
        all_keys = set(old.keys()) | set(new.keys())
        
        for key in all_keys:
            path = f"{prefix}.{key}" if prefix else key
            old_val = old.get(key)
            new_val = new.get(key)
            
            if isinstance(old_val, dict) and isinstance(new_val, dict):
                diff.update(self._get_config_diff(old_val, new_val, path))
            elif old_val != new_val:
                diff[path] = (old_val, new_val)
        
        return diff
    
    # =========================================================================
    # 접근자
    # =========================================================================
    
    def get(self, path: str, default: Any = None) -> Any:
        """설정 값 가져오기"""
        return self._get_nested(self._config, path) or default
    
    def get_all(self) -> Dict:
        """전체 설정"""
        return deepcopy(self._config)
    
    def get_pending_changes(self) -> Dict:
        """대기 중인 변경"""
        return deepcopy(self._pending_changes)
    
    def get_change_history(self) -> List[Dict]:
        """변경 이력"""
        return deepcopy(self._change_history)


# =============================================================================
# 전역 인스턴스
# =============================================================================

_loader_instance: Optional[ConfigLoader] = None


def get_config_loader(
    config_path: str = "config/config.yaml",
    secrets_path: str = "config/secrets.yaml",
) -> ConfigLoader:
    """전역 ConfigLoader 인스턴스"""
    global _loader_instance
    
    if _loader_instance is None:
        _loader_instance = ConfigLoader(config_path, secrets_path)
    
    return _loader_instance


def get_config() -> Dict:
    """현재 설정 가져오기"""
    return get_config_loader().load()


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("ConfigLoader 테스트")
    print("=" * 60)
    
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.yaml"
        
        # 1. 로더 생성 (자동 생성)
        print("\n1. 로더 생성:")
        loader = ConfigLoader(str(config_path), auto_create=True)
        config = loader.load()
        print(f"   모드: {config.get('mode')}")
        print(f"   AI 모델: {config['ai']['model']}")
        
        # 2. 설정 업데이트
        print("\n2. 설정 업데이트:")
        loader.update('logging.level', 'DEBUG')
        print(f"   logging.level = {loader.get('logging.level')}")
        
        # 3. 핫리로드 금지 항목
        print("\n3. 핫리로드 금지 항목 업데이트:")
        loader.update('risk.stop_loss_pct', -2.0)
        print(f"   대기 변경: {loader.get_pending_changes()}")
        
        # 4. 대기 변경 적용
        print("\n4. 대기 변경 적용:")
        count = loader.apply_pending_changes()
        print(f"   적용된 변경: {count}개")
        print(f"   risk.stop_loss_pct = {loader.get('risk.stop_loss_pct')}")
        
        # 5. 스키마 검증 테스트
        print("\n5. 스키마 검증:")
        test_config = deepcopy(config)
        test_config['risk']['stop_loss_pct'] = 5.0  # 잘못된 값
        errors = loader._validate_schema(test_config)
        print(f"   오류: {errors}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
