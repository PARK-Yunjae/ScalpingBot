#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Emergency Stop (비상 정지 스크립트)
============================================================================
긴급 상황 시 모든 포지션을 청산하고 시스템을 종료

사용법:
    python emergency_stop.py              # 전량 청산 + 시스템 종료
    python emergency_stop.py --no-sell    # 청산 없이 시스템만 종료
    python emergency_stop.py --cancel     # 미체결만 취소 (포지션 유지)

주의:
    - 실행 전 확인 메시지가 표시됩니다
    - --force 옵션으로 확인 없이 실행 가능
============================================================================
"""

import os
import sys
import argparse
import signal
from pathlib import Path
from datetime import datetime

# 프로젝트 루트 경로
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def print_banner():
    """경고 배너 출력"""
    print()
    print("=" * 60)
    print("🚨" * 20)
    print("=" * 60)
    print()
    print("      ██████╗ ██╗███████╗ █████╗ ██████╗ ██╗     ███████╗")
    print("      ██╔══██╗██║██╔════╝██╔══██╗██╔══██╗██║     ██╔════╝")
    print("      ██║  ██║██║███████╗███████║██████╔╝██║     █████╗  ")
    print("      ██║  ██║██║╚════██║██╔══██║██╔══██╗██║     ██╔══╝  ")
    print("      ██████╔╝██║███████║██║  ██║██████╔╝███████╗███████╗")
    print("      ╚═════╝ ╚═╝╚══════╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚══════╝")
    print()
    print("                    비상 정지 스크립트                    ")
    print()
    print("=" * 60)
    print("🚨" * 20)
    print("=" * 60)
    print()


def load_secrets():
    """비밀 설정 로드"""
    import yaml
    
    secrets_path = PROJECT_ROOT / 'config' / 'secrets.yaml'
    
    if not secrets_path.exists():
        print("❌ secrets.yaml 파일을 찾을 수 없습니다.")
        return None
    
    with open(secrets_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def cancel_all_orders(broker) -> int:
    """모든 미체결 주문 취소"""
    try:
        pending = broker.get_pending_orders()
        cancelled = 0
        
        for order in pending:
            result = broker.cancel_order(
                order.order_id,
                order.stock_code,
                order.remaining_qty
            )
            if result:
                cancelled += 1
                print(f"  ✅ 취소: {order.stock_code} #{order.order_id}")
            else:
                print(f"  ❌ 취소 실패: {order.stock_code} #{order.order_id}")
        
        return cancelled
    except Exception as e:
        print(f"  ❌ 주문 취소 에러: {e}")
        return 0


def liquidate_all_positions(broker) -> int:
    """모든 포지션 시장가 청산"""
    try:
        positions = broker.get_positions()
        liquidated = 0
        
        for pos in positions:
            print(f"  청산 중: {pos.stock_name} ({pos.stock_code}) {pos.quantity}주...")
            
            result = broker.sell_market(pos.stock_code, pos.quantity)
            
            if result.success:
                liquidated += 1
                print(f"  ✅ 청산 완료: {pos.stock_code} @ {result.price:,.0f}원")
            else:
                print(f"  ❌ 청산 실패: {pos.stock_code} - {result.error}")
        
        return liquidated
    except Exception as e:
        print(f"  ❌ 청산 에러: {e}")
        return 0


def stop_trading_engine():
    """트레이딩 엔진 종료 신호 전송"""
    pid_file = PROJECT_ROOT / 'logs' / 'scalping.pid'
    
    if pid_file.exists():
        try:
            with open(pid_file, 'r') as f:
                pid = int(f.read().strip())
            
            os.kill(pid, signal.SIGTERM)
            print(f"  ✅ 종료 신호 전송: PID {pid}")
            return True
        except ProcessLookupError:
            print(f"  ⚠️ 프로세스가 이미 종료됨")
            pid_file.unlink()
        except Exception as e:
            print(f"  ❌ 종료 신호 전송 실패: {e}")
    else:
        print(f"  ⚠️ PID 파일 없음 (실행 중이 아닐 수 있음)")
    
    return False


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(
        description='ScalpingBot 비상 정지',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        '--no-sell',
        action='store_true',
        help='청산 없이 시스템만 종료'
    )
    
    parser.add_argument(
        '--cancel',
        action='store_true',
        help='미체결 주문만 취소 (포지션 유지)'
    )
    
    parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='확인 없이 즉시 실행'
    )
    
    args = parser.parse_args()
    
    # 배너 출력
    print_banner()
    
    print(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 실행 모드 표시
    if args.no_sell:
        print("모드: 시스템 종료 (청산 없음)")
    elif args.cancel:
        print("모드: 미체결 취소 (포지션 유지)")
    else:
        print("모드: 전량 청산 + 시스템 종료")
    
    print()
    
    # 확인
    if not args.force:
        print("⚠️  이 작업은 되돌릴 수 없습니다!")
        print()
        confirm = input("계속하시겠습니까? (yes 입력): ")
        
        if confirm.lower() != 'yes':
            print("\n취소되었습니다.")
            return 0
    
    print()
    print("-" * 60)
    print()
    
    # 청산/취소 실행
    if not args.no_sell:
        print("📡 한투 API 연결 중...")
        
        secrets = load_secrets()
        if not secrets:
            return 1
        
        try:
            from scalping.execution.broker import KISBroker
            
            broker = KISBroker(secrets.get('kis', {}), dry_run=False)
            
            if not broker.health_check():
                print("❌ API 연결 실패")
                return 1
            
            print("✅ API 연결 성공")
            print()
            
            # 미체결 취소
            print("📋 미체결 주문 취소 중...")
            cancelled = cancel_all_orders(broker)
            print(f"   취소 완료: {cancelled}건")
            print()
            
            # 포지션 청산
            if not args.cancel:
                print("💰 포지션 청산 중...")
                liquidated = liquidate_all_positions(broker)
                print(f"   청산 완료: {liquidated}건")
                print()
        
        except ImportError as e:
            print(f"❌ 모듈 import 실패: {e}")
            return 1
        except Exception as e:
            print(f"❌ 에러 발생: {e}")
            return 1
    
    # 시스템 종료
    print("🛑 시스템 종료 중...")
    stop_trading_engine()
    
    print()
    print("-" * 60)
    print()
    print("✅ 비상 정지 완료")
    print()
    print("=" * 60)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
