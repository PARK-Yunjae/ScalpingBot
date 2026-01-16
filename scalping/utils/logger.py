#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Logger (로깅 시스템)
============================================================================
파일 + 콘솔 로깅 with 일별 로테이션

핵심 기능:
- 일별 로그 파일 로테이션
- 에러 전용 로그 파일 (errors.log)
- 콘솔 컬러 출력
- 스택 트레이스 자동 포함
- 로그 레벨 동적 변경

로그 파일 구조:
    logs/
    ├── scalping.log           # 전체 로그
    ├── scalping_2026-01-16.log # 일별 로그
    ├── errors.log             # 에러만 (최근 30일)
    └── trades.log             # 매매 기록 전용

사용법:
    from scalping.utils.logger import get_logger, setup_logging
    
    setup_logging(log_dir="logs", level="INFO")
    logger = get_logger("MyModule")
    logger.info("작업 시작")
============================================================================
"""

import os
import sys
import logging
import traceback
from pathlib import Path
from datetime import datetime, date
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from typing import Optional, Dict
import threading

# 전역 설정
_initialized = False
_log_dir: Optional[Path] = None
_lock = threading.Lock()


# =============================================================================
# 컬러 포맷터 (콘솔용)
# =============================================================================

class ColorFormatter(logging.Formatter):
    """콘솔 컬러 포맷터"""
    
    # ANSI 컬러 코드
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    def format(self, record):
        # 레벨별 컬러
        color = self.COLORS.get(record.levelname, '')
        
        # 원본 포맷
        original = super().format(record)
        
        # 컬러 적용
        if color:
            return f"{color}{original}{self.RESET}"
        return original


# =============================================================================
# 커스텀 포맷터 (파일용)
# =============================================================================

class DetailedFormatter(logging.Formatter):
    """상세 로그 포맷터 (파일용)"""
    
    def format(self, record):
        # 기본 포맷
        result = super().format(record)
        
        # 예외 정보가 있으면 구분선 추가
        if record.exc_info:
            result += "\n" + "=" * 80
        
        return result


# =============================================================================
# 트레이드 전용 포맷터
# =============================================================================

class TradeFormatter(logging.Formatter):
    """매매 기록 전용 포맷터"""
    
    def format(self, record):
        # CSV 형태로 포맷
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return f"{timestamp},{record.getMessage()}"


# =============================================================================
# 로깅 설정 함수
# =============================================================================

def setup_logging(
    log_dir: str = "logs",
    level: str = "INFO",
    console: bool = True,
    file: bool = True,
    max_days: int = 30,
    max_size_mb: int = 50,
) -> None:
    """
    로깅 시스템 초기화
    
    Args:
        log_dir: 로그 디렉토리
        level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console: 콘솔 출력 여부
        file: 파일 출력 여부
        max_days: 로그 보관 일수
        max_size_mb: 단일 파일 최대 크기 (MB)
    """
    global _initialized, _log_dir
    
    with _lock:
        if _initialized:
            return
        
        # 로그 디렉토리 생성
        _log_dir = Path(log_dir)
        _log_dir.mkdir(parents=True, exist_ok=True)
        
        # 루트 로거 설정
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        
        # 기존 핸들러 제거
        root_logger.handlers.clear()
        
        # 1. 콘솔 핸들러
        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.DEBUG)
            console_handler.setFormatter(ColorFormatter(
                fmt='[%(asctime)s] %(levelname)-8s %(name)-20s │ %(message)s',
                datefmt='%H:%M:%S'
            ))
            root_logger.addHandler(console_handler)
        
        # 2. 메인 로그 파일 (일별 로테이션)
        if file:
            main_log_path = _log_dir / "scalping.log"
            main_handler = TimedRotatingFileHandler(
                filename=str(main_log_path),
                when='midnight',
                interval=1,
                backupCount=max_days,
                encoding='utf-8',
            )
            main_handler.setLevel(logging.DEBUG)
            main_handler.setFormatter(DetailedFormatter(
                fmt='[%(asctime)s] %(levelname)-8s %(name)-25s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            ))
            main_handler.suffix = "%Y-%m-%d"
            root_logger.addHandler(main_handler)
            
            # 3. 에러 전용 로그 파일
            error_log_path = _log_dir / "errors.log"
            error_handler = RotatingFileHandler(
                filename=str(error_log_path),
                maxBytes=max_size_mb * 1024 * 1024,
                backupCount=5,
                encoding='utf-8',
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(DetailedFormatter(
                fmt='[%(asctime)s] %(levelname)-8s %(name)-25s | %(message)s\n'
                    'File: %(pathname)s:%(lineno)d\n'
                    'Function: %(funcName)s\n'
                    '%(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            ))
            root_logger.addHandler(error_handler)
        
        _initialized = True
        
        # 초기화 로그
        logger = logging.getLogger('ScalpingBot')
        logger.info(f"로깅 시스템 초기화 (레벨: {level}, 디렉토리: {log_dir})")


def setup_trade_logger() -> logging.Logger:
    """
    매매 기록 전용 로거 설정
    
    Returns:
        매매 전용 로거
    """
    global _log_dir
    
    if _log_dir is None:
        _log_dir = Path("logs")
        _log_dir.mkdir(parents=True, exist_ok=True)
    
    # 매매 전용 로거
    trade_logger = logging.getLogger('ScalpingBot.Trades')
    trade_logger.setLevel(logging.INFO)
    trade_logger.propagate = False  # 상위 로거로 전파 안함
    
    # 기존 핸들러 제거
    trade_logger.handlers.clear()
    
    # 매매 로그 파일
    trade_log_path = _log_dir / "trades.log"
    trade_handler = TimedRotatingFileHandler(
        filename=str(trade_log_path),
        when='midnight',
        interval=1,
        backupCount=365,  # 1년 보관
        encoding='utf-8',
    )
    trade_handler.setLevel(logging.INFO)
    trade_handler.setFormatter(TradeFormatter())
    trade_handler.suffix = "%Y-%m-%d"
    trade_logger.addHandler(trade_handler)
    
    return trade_logger


# =============================================================================
# 로거 팩토리
# =============================================================================

def get_logger(name: str = None) -> logging.Logger:
    """
    로거 인스턴스 가져오기
    
    Args:
        name: 모듈 이름 (None이면 루트)
    
    Returns:
        로거 인스턴스
    """
    # 초기화 확인
    if not _initialized:
        setup_logging()
    
    # ScalpingBot prefix 추가
    if name:
        if not name.startswith('ScalpingBot'):
            name = f'ScalpingBot.{name}'
    else:
        name = 'ScalpingBot'
    
    return logging.getLogger(name)


# =============================================================================
# 유틸리티 함수
# =============================================================================

def set_level(level: str, logger_name: str = None):
    """
    로그 레벨 동적 변경
    
    Args:
        level: 로그 레벨
        logger_name: 로거 이름 (None이면 루트)
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    if logger_name:
        logging.getLogger(logger_name).setLevel(log_level)
    else:
        logging.getLogger().setLevel(log_level)
    
    get_logger().info(f"로그 레벨 변경: {level}")


def log_exception(logger: logging.Logger, message: str, exc: Exception = None):
    """
    예외 로깅 (스택 트레이스 포함)
    
    Args:
        logger: 로거 인스턴스
        message: 메시지
        exc: 예외 객체
    """
    if exc:
        logger.error(f"{message}: {type(exc).__name__}: {exc}")
        logger.debug(f"스택 트레이스:\n{traceback.format_exc()}")
    else:
        logger.error(message, exc_info=True)


def log_trade(
    trade_type: str,
    stock_code: str,
    stock_name: str,
    quantity: int,
    price: float,
    profit: float = 0,
    reason: str = "",
):
    """
    매매 기록 로깅
    
    Args:
        trade_type: BUY/SELL
        stock_code: 종목코드
        stock_name: 종목명
        quantity: 수량
        price: 가격
        profit: 수익금 (SELL 시)
        reason: 매매 사유
    """
    trade_logger = logging.getLogger('ScalpingBot.Trades')
    
    # CSV 형태: type,code,name,qty,price,profit,reason
    trade_logger.info(
        f"{trade_type},{stock_code},{stock_name},{quantity},{price:.0f},{profit:.0f},{reason}"
    )


def get_log_files() -> Dict[str, str]:
    """
    로그 파일 경로 조회
    
    Returns:
        로그 파일 경로 딕셔너리
    """
    global _log_dir
    
    if _log_dir is None:
        return {}
    
    return {
        'main': str(_log_dir / "scalping.log"),
        'errors': str(_log_dir / "errors.log"),
        'trades': str(_log_dir / "trades.log"),
        'directory': str(_log_dir),
    }


def rotate_logs():
    """수동 로그 로테이션"""
    root_logger = logging.getLogger()
    
    for handler in root_logger.handlers:
        if isinstance(handler, (RotatingFileHandler, TimedRotatingFileHandler)):
            handler.doRollover()
    
    get_logger().info("로그 로테이션 완료")


# =============================================================================
# 컨텍스트 매니저
# =============================================================================

class LogContext:
    """로그 컨텍스트 매니저 (작업 시작/종료 자동 로깅)"""
    
    def __init__(self, logger: logging.Logger, operation: str):
        self.logger = logger
        self.operation = operation
        self.start_time = None
    
    def __enter__(self):
        self.start_time = datetime.now()
        self.logger.info(f"[시작] {self.operation}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = (datetime.now() - self.start_time).total_seconds()
        
        if exc_type:
            self.logger.error(
                f"[실패] {self.operation} ({elapsed:.2f}초) - {exc_type.__name__}: {exc_val}"
            )
        else:
            self.logger.info(f"[완료] {self.operation} ({elapsed:.2f}초)")
        
        return False  # 예외 전파


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    import tempfile
    
    print("=" * 60)
    print("Logger 테스트")
    print("=" * 60)
    
    # 임시 디렉토리에서 테스트
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 로깅 설정
        setup_logging(log_dir=tmpdir, level="DEBUG")
        
        print("\n1. 기본 로깅:")
        logger = get_logger("TestModule")
        logger.debug("디버그 메시지")
        logger.info("정보 메시지")
        logger.warning("경고 메시지")
        logger.error("에러 메시지")
        
        print("\n2. 예외 로깅:")
        try:
            raise ValueError("테스트 예외")
        except Exception as e:
            log_exception(logger, "예외 발생", e)
        
        print("\n3. 매매 로깅:")
        setup_trade_logger()
        log_trade("BUY", "005930", "삼성전자", 10, 70000)
        log_trade("SELL", "005930", "삼성전자", 10, 71000, profit=10000, reason="익절")
        
        print("\n4. 컨텍스트 매니저:")
        with LogContext(logger, "데이터 처리"):
            import time
            time.sleep(0.1)
        
        print("\n5. 로그 파일:")
        files = get_log_files()
        for name, path in files.items():
            print(f"   {name}: {path}")
        
        # 로그 파일 내용 확인
        print("\n6. errors.log 내용:")
        error_log = Path(tmpdir) / "errors.log"
        if error_log.exists():
            print(error_log.read_text()[:500])
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)
