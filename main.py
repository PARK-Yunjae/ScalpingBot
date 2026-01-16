#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# .\venv\Scripts\activate
"""
============================================================================
ScalpingBot v2.4 - 한국 주식 자동매매 봇
============================================================================
진입점 (Entry Point)

사용법:
    python main.py                    # 기본 실행 (config.yaml의 mode 사용)
    python main.py --mode LIVE_MICRO  # 모드 지정 실행
    python main.py --dry-run          # 드라이런 (매매 없이 시뮬레이션)
    python main.py --debug            # 디버그 모드 (상세 로그)

모드 설명:
    - LIVE_DATA_ONLY: 실시간 데이터 수집만, 실제 매매 없음
    - LIVE_MICRO: 소액 매매 (종목당 10만원 이하)
    - LIVE: 실전 매매

로그 파일:
    - logs/scalping.log       # 전체 로그 (일별 로테이션)
    - logs/scalping_YYYY-MM-DD.log  # 날짜별 백업
    - logs/errors.log         # 에러만
    - logs/trades.log         # 매매 기록
============================================================================
"""

import argparse
import signal
import sys
import os
import time
from pathlib import Path
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# 로깅 먼저 설정 (다른 import 전에)
from scalping.utils.logger import setup_logging, setup_trade_logger, get_logger, LogContext, log_exception

# PID 파일 경로
PID_FILE = PROJECT_ROOT / 'logs' / 'scalping.pid'


# =============================================================================
# 시그널 핸들러
# =============================================================================

_shutdown_requested = False

def signal_handler(signum, frame):
    """시그널 핸들러 (Ctrl+C, SIGTERM)"""
    global _shutdown_requested
    
    logger = get_logger('Main')
    
    if _shutdown_requested:
        logger.warning("강제 종료...")
        sys.exit(1)
    
    _shutdown_requested = True
    logger.info(f"종료 신호 수신 (signal {signum}), 안전하게 종료 중...")


def cleanup():
    """종료 시 정리 작업"""
    logger = get_logger('Main')
    
    # PID 파일 삭제
    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
            logger.debug("PID 파일 삭제")
        except Exception as e:
            logger.warning(f"PID 파일 삭제 실패: {e}")


# =============================================================================
# 메인 함수
# =============================================================================

def main():
    """메인 함수"""
    global _shutdown_requested
    
    # -------------------------------------------------------------------------
    # 1. 인자 파싱
    # -------------------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description='ScalpingBot v2.4 - 한국 주식 자동매매 봇',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py                      # 기본 실행
  python main.py --mode LIVE_MICRO    # 소액 테스트
  python main.py --mode LIVE --dry-run # 실전 모의
  python main.py --debug              # 디버그 모드
        """
    )
    
    parser.add_argument(
        '--mode', '-m',
        choices=['LIVE_DATA_ONLY', 'LIVE_MICRO', 'LIVE'],
        help='실행 모드 (기본값: config.yaml의 mode)'
    )
    
    parser.add_argument(
        '--dry-run', '-d',
        action='store_true',
        help='드라이런 모드 (실제 주문 없이 시뮬레이션)'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='디버그 모드 (상세 로그)'
    )
    
    parser.add_argument(
        '--config', '-c',
        type=str,
        default='config/config.yaml',
        help='설정 파일 경로'
    )
    
    args = parser.parse_args()
    
    # -------------------------------------------------------------------------
    # 2. 로깅 시스템 초기화
    # -------------------------------------------------------------------------
    log_level = 'DEBUG' if args.debug else 'INFO'
    
    setup_logging(
        log_dir=str(PROJECT_ROOT / 'logs'),
        level=log_level,
        console=True,
        file=True,
        max_days=30,
    )
    
    setup_trade_logger()
    
    logger = get_logger('Main')
    
    # -------------------------------------------------------------------------
    # 3. 시작 배너
    # -------------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("ScalpingBot v2.4 시작")
    logger.info("=" * 60)
    logger.info(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Python: {sys.version.split()[0]}")
    logger.info(f"PID: {os.getpid()}")
    logger.info(f"디버그: {args.debug}")
    logger.info(f"드라이런: {args.dry_run}")
    
    # -------------------------------------------------------------------------
    # 4. 설정 로드
    # -------------------------------------------------------------------------
    from scalping.config import ConfigLoader
    
    config_path = PROJECT_ROOT / args.config
    secrets_path = PROJECT_ROOT / 'config' / 'secrets.yaml'
    
    with LogContext(logger, "설정 로드"):
        try:
            config_loader = ConfigLoader(
                config_path=str(config_path),
                secrets_path=str(secrets_path),
                auto_create=True,
            )
            
            config = config_loader.load()
            secrets = config_loader.load_secrets()
            
            # 대기 중인 변경 적용 (전날 설정)
            pending_count = config_loader.apply_pending_changes()
            if pending_count > 0:
                logger.info(f"대기 중이던 설정 {pending_count}개 적용됨")
            
            # 명령줄 모드 우선
            if args.mode:
                config['mode'] = args.mode
            
            # 설정 로그 (다양한 config 구조 지원)
            logger.info(f"모드: {config.get('mode', 'UNKNOWN')}")
            logger.info(f"AI 모델: {config.get('ai', {}).get('model', 'N/A')}")
            
            # min_score: strategy > trading
            min_score = config.get('strategy', {}).get('min_score') or config.get('trading', {}).get('min_score', 'N/A')
            logger.info(f"최소 점수: {min_score}")
            
            # stop_loss: risk > trading > safety
            stop_loss = (
                config.get('risk', {}).get('stop_loss_pct') or
                config.get('trading', {}).get('stop_loss') or
                config.get('safety', {}).get('stop_loss_pct', 'N/A')
            )
            logger.info(f"손절: {stop_loss}%")
            
            # take_profit: risk > trading > safety
            take_profit = (
                config.get('risk', {}).get('take_profit_pct') or
                config.get('trading', {}).get('take_profit') or
                config.get('safety', {}).get('take_profit_pct', 'N/A')
            )
            logger.info(f"익절: {take_profit}%")
            
        except Exception as e:
            log_exception(logger, "설정 로드 실패", e)
            logger.warning("기본 설정으로 진행합니다 (안전 모드)")
            from scalping.config import DEFAULT_CONFIG
            config = DEFAULT_CONFIG.copy()
            config['mode'] = 'LIVE_DATA_ONLY'  # 안전 모드
            secrets = {}
    
    # -------------------------------------------------------------------------
    # 5. 시그널 핸들러 등록
    # -------------------------------------------------------------------------
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # PID 파일 생성
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    logger.debug(f"PID 파일 생성: {PID_FILE}")
    
    # -------------------------------------------------------------------------
    # 6. 트레이딩 엔진 초기화
    # -------------------------------------------------------------------------
    engine = None
    
    try:
        from scalping.engine import TradingEngine
        
        with LogContext(logger, "트레이딩 엔진 초기화"):
            # KIS API 설정 병합
            kis_config = secrets.get('kis', {})
            
            # Discord 설정 병합
            discord_config = config.get('discord', {}).copy()
            discord_secrets = secrets.get('discord', {})
            if discord_secrets.get('webhook_url'):
                discord_config['webhook_url'] = discord_secrets['webhook_url']
            
            # 엔진 생성
            engine = TradingEngine(
                config=config,
                kis_config=kis_config,
                discord_config=discord_config,
                dry_run=args.dry_run,
            )
            
            logger.info("트레이딩 엔진 초기화 완료")
    
    except ImportError as e:
        log_exception(logger, "모듈 import 실패", e)
        logger.error("필요한 패키지를 설치해주세요: pip install -r requirements.txt")
        cleanup()
        return 1
    
    except Exception as e:
        log_exception(logger, "트레이딩 엔진 초기화 실패", e)
        cleanup()
        return 1
    
    # -------------------------------------------------------------------------
    # 7. 핫리로드 시작
    # -------------------------------------------------------------------------
    def on_config_change(new_config):
        """설정 변경 콜백"""
        logger.info("설정 파일 변경 감지, 적용 중...")
        try:
            # 엔진에 설정 전달
            if engine and hasattr(engine, 'update_config'):
                engine.update_config(new_config)
        except Exception as e:
            log_exception(logger, "설정 업데이트 실패", e)
    
    config_loader.start_hot_reload(
        callback=on_config_change,
        interval=10,  # 10초마다 체크
    )
    logger.info("핫리로드 활성화 (10초 간격)")
    
    # -------------------------------------------------------------------------
    # 8. 메인 루프 실행
    # -------------------------------------------------------------------------
    logger.info("-" * 60)
    logger.info("메인 루프 시작")
    logger.info("-" * 60)
    
    exit_code = 0
    error_count = 0
    max_errors = 10  # 연속 에러 허용 횟수
    
    try:
        # 엔진 시작
        engine.start()
        
        # 메인 루프
        while not _shutdown_requested:
            try:
                # 엔진이 실행 중인지 확인
                if not engine.is_running():
                    logger.warning("엔진이 중지됨, 종료합니다")
                    break
                
                # 에러 카운터 리셋 (정상 작동 중)
                error_count = 0
                
                # 대기 (CPU 사용률 절감)
                time.sleep(1)
                
            except KeyboardInterrupt:
                logger.info("Ctrl+C 감지")
                break
            
            except Exception as e:
                error_count += 1
                log_exception(logger, f"메인 루프 에러 ({error_count}/{max_errors})", e)
                
                if error_count >= max_errors:
                    logger.critical(f"연속 에러 {max_errors}회 초과, 비상 종료")
                    exit_code = 1
                    break
                
                # 에러 발생해도 루프 계속 (설계서 14.1)
                time.sleep(5)
    
    except Exception as e:
        log_exception(logger, "치명적 에러", e)
        exit_code = 1
    
    finally:
        # -------------------------------------------------------------------------
        # 9. 정리 및 종료
        # -------------------------------------------------------------------------
        logger.info("-" * 60)
        logger.info("종료 절차 시작")
        logger.info("-" * 60)
        
        # 핫리로드 중지
        try:
            config_loader.stop_hot_reload()
        except:
            pass
        
        # 대기 중인 설정 변경 저장
        try:
            pending = config_loader.get_pending_changes()
            if pending:
                logger.info(f"대기 중인 설정 변경 {len(pending)}개 (다음 시작 시 적용)")
                for key, value in pending.items():
                    logger.debug(f"  {key}: {value}")
        except:
            pass
        
        # 엔진 종료
        if engine:
            try:
                with LogContext(logger, "엔진 종료"):
                    engine.stop()
            except Exception as e:
                log_exception(logger, "엔진 종료 에러", e)
        
        # 정리
        cleanup()
        
        # 종료 요약
        logger.info("=" * 60)
        logger.info(f"ScalpingBot v2.4 종료")
        logger.info(f"종료 코드: {exit_code}")
        logger.info(f"종료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)
        
        # 로그 파일 위치 안내
        from scalping.utils.logger import get_log_files
        log_files = get_log_files()
        if log_files:
            logger.info(f"로그 파일: {log_files.get('main', 'logs/scalping.log')}")
    
    return exit_code


# =============================================================================
# 진입점
# =============================================================================

if __name__ == '__main__':
    sys.exit(main())
