#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - KIS Broker (한국투자증권 API 브로커)
============================================================================
한국투자증권 Open API를 통한 주문 실행 및 조회

핵심 기능:
- API 토큰 자동 갱신 (만료 1시간 전)
- 주문 실행 (시장가/지정가 매수/매도)
- 주문 취소
- 잔고/보유종목/미체결 조회
- 현재가/지수 조회
- 호가단위 계산
- dry_run 모드 지원 (실제 주문 없이 시뮬레이션)

예외 처리:
- 401/403: 토큰 자동 갱신
- 429: 1초 대기 후 재시도 (최대 3회)
- Timeout: 3회 재시도

사용법:
    broker = KISBroker(secrets['kis'], dry_run=False)
    
    # 시장가 매수
    result = broker.buy_market("005930", 10)
    if result.success:
        print(f"주문번호: {result.order_id}")
    
    # 보유 종목 조회
    positions = broker.get_positions()
============================================================================
"""

import time
import logging
import threading
import requests
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

# 로거 설정
logger = logging.getLogger('ScalpingBot.Broker')


# =============================================================================
# 상수 및 열거형
# =============================================================================

class OrderType(Enum):
    """주문 유형"""
    LIMIT = "00"      # 지정가
    MARKET = "01"     # 시장가
    CONDITIONAL = "02"  # 조건부지정가
    BEST = "03"       # 최유리지정가
    PRIORITY = "04"   # 최우선지정가


class OrderSide(Enum):
    """주문 방향"""
    BUY = "buy"
    SELL = "sell"


# 한투 API TR ID
TR_IDS = {
    # 주문
    'buy': 'TTTC0802U',           # 매수
    'sell': 'TTTC0801U',          # 매도
    'cancel': 'TTTC0803U',        # 취소/정정
    
    # 조회
    'balance': 'TTTC8434R',       # 잔고 조회
    'pending': 'TTTC8001R',       # 미체결 조회
    'price': 'FHKST01010100',     # 현재가 조회
    'index': 'FHPUP02100000',     # 지수 조회
    'daily_ohlcv': 'FHKST01010400',  # 일봉 데이터
    
    # 모의투자
    'buy_mock': 'VTTC0802U',
    'sell_mock': 'VTTC0801U',
    'cancel_mock': 'VTTC0803U',
    'balance_mock': 'VTTC8434R',
    'pending_mock': 'VTTC8001R',
}

# 재시도 설정
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # 초
REQUEST_TIMEOUT = 10  # 초


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class OrderResult:
    """주문 결과"""
    success: bool                  # 성공 여부
    order_id: str = ""             # 주문 번호
    stock_code: str = ""           # 종목 코드
    side: str = ""                 # buy/sell
    order_type: str = ""           # 주문 유형 (00/01)
    price: float = 0               # 주문 가격
    quantity: int = 0              # 주문 수량
    filled_qty: int = 0            # 체결 수량
    filled_price: float = 0        # 체결 가격
    error: str = ""                # 에러 메시지
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Position:
    """보유 포지션"""
    stock_code: str                # 종목 코드
    stock_name: str                # 종목명
    quantity: int                  # 보유 수량
    avg_price: float               # 평균 매입가
    current_price: float           # 현재가
    profit_loss: float             # 평가손익
    profit_pct: float              # 수익률 (%)


@dataclass  
class PendingOrder:
    """미체결 주문"""
    order_id: str                  # 주문 번호
    stock_code: str                # 종목 코드
    stock_name: str                # 종목명
    side: str                      # buy/sell
    order_type: str                # 주문 유형
    order_qty: int                 # 주문 수량
    filled_qty: int                # 체결 수량
    pending_qty: int               # 미체결 수량
    order_price: float             # 주문 가격
    order_time: str                # 주문 시간


# =============================================================================
# 호가단위 함수
# =============================================================================

def get_tick_size(price: int) -> int:
    """
    호가단위 계산
    
    한국 주식시장 호가단위:
    - 1,000원 미만: 1원
    - 1,000원 ~ 5,000원: 5원
    - 5,000원 ~ 10,000원: 10원
    - 10,000원 ~ 50,000원: 50원
    - 50,000원 ~ 100,000원: 100원
    - 100,000원 ~ 500,000원: 500원
    - 500,000원 이상: 1,000원
    
    Args:
        price: 주가
    
    Returns:
        호가단위
    """
    if price < 1000:
        return 1
    elif price < 5000:
        return 5
    elif price < 10000:
        return 10
    elif price < 50000:
        return 50
    elif price < 100000:
        return 100
    elif price < 500000:
        return 500
    else:
        return 1000


def round_price(price: float, direction: str = 'down') -> int:
    """
    호가단위로 반올림
    
    Args:
        price: 가격
        direction: 'down' (내림), 'up' (올림), 'round' (반올림)
    
    Returns:
        호가단위에 맞춘 가격
    """
    tick = get_tick_size(int(price))
    
    if direction == 'down':
        return int(price // tick * tick)
    elif direction == 'up':
        return int((price + tick - 1) // tick * tick)
    else:  # round
        return int(round(price / tick) * tick)


# =============================================================================
# KIS 브로커 클래스
# =============================================================================

class KISBroker:
    """
    한국투자증권 API 브로커
    
    실제 API 호출과 dry_run 모드를 모두 지원합니다.
    """
    
    def __init__(
        self,
        config: Dict[str, Any],
        dry_run: bool = False,
    ):
        """
        초기화
        
        Args:
            config: API 설정 딕셔너리
                - app_key: 앱 키
                - app_secret: 앱 시크릿
                - account_number: 계좌번호 (8자리)
                - account_code: 상품코드 (보통 01)
                - base_url: API 서버 URL
                - environment: 환경 (P: 실전, V: 모의)
            dry_run: True면 실제 주문 없이 시뮬레이션
        """
        self.config = config
        self.dry_run = dry_run
        
        # API 인증 정보
        self.app_key = config['app_key']
        self.app_secret = config['app_secret']
        self.account_number = config['account_number']
        self.account_code = config.get('account_code', '01')
        self.base_url = config.get(
            'base_url', 
            'https://openapi.koreainvestment.com:9443'
        )
        self.environment = config.get('environment', 'P')  # P: 실전, V: 모의
        
        # 토큰 관리
        self._token: Optional[str] = None
        self._token_expires: float = 0
        self._token_lock = threading.Lock()
        
        # 웹소켓
        self._ws = None
        self._ws_approval_key: Optional[str] = None
        
        # 통계
        self._stats = {
            'total_orders': 0,
            'success_orders': 0,
            'failed_orders': 0,
            'total_api_calls': 0,
        }
        
        # dry_run 모드용 가상 데이터
        self._mock_positions: Dict[str, Dict] = {}
        self._mock_orders: List[Dict] = []
        self._mock_order_id = 1000000
        
        mode_str = "🔸 DRY RUN" if dry_run else "🔹 LIVE"
        env_str = "모의투자" if self.environment == 'V' else "실전투자"
        logger.info(f"KIS 브로커 초기화 ({mode_str}, {env_str})")
    
    # =========================================================================
    # 토큰 관리
    # =========================================================================
    
    def _get_token(self) -> str:
        """
        토큰 조회 (자동 갱신)
        
        만료 1시간 전에 자동으로 갱신합니다.
        
        Returns:
            액세스 토큰
        """
        with self._token_lock:
            # 만료 1시간 전이면 갱신
            if self._token and time.time() < self._token_expires - 3600:
                return self._token
            
            return self._refresh_token()
    
    def _refresh_token(self) -> str:
        """
        토큰 갱신
        
        Returns:
            새 액세스 토큰
        
        Raises:
            Exception: 토큰 갱신 실패
        """
        logger.info("API 토큰 갱신 중...")
        
        try:
            response = requests.post(
                f"{self.base_url}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                },
                timeout=REQUEST_TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                self._token = data['access_token']
                # 토큰 유효기간: 보통 24시간
                expires_in = int(data.get('expires_in', 86400))
                self._token_expires = time.time() + expires_in
                
                logger.info(f"✅ API 토큰 갱신 완료 (유효: {expires_in // 3600}시간)")
                return self._token
            else:
                error_msg = response.json().get('msg', '알 수 없는 오류')
                raise Exception(f"토큰 갱신 실패 [{response.status_code}]: {error_msg}")
        
        except requests.Timeout:
            raise Exception("토큰 갱신 타임아웃")
        except requests.RequestException as e:
            raise Exception(f"토큰 갱신 네트워크 오류: {e}")
    
    def _get_headers(self, tr_id: str = None) -> Dict[str, str]:
        """
        API 요청 헤더 생성
        
        Args:
            tr_id: 거래 ID (TR ID)
        
        Returns:
            헤더 딕셔너리
        """
        headers = {
            "authorization": f"Bearer {self._get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "content-type": "application/json; charset=utf-8",
        }
        
        if tr_id:
            headers["tr_id"] = tr_id
        
        return headers
    
    # =========================================================================
    # API 요청 래퍼 (재시도 로직 포함)
    # =========================================================================
    
    def _request(
        self,
        method: str,
        endpoint: str,
        tr_id: str,
        params: Dict = None,
        json_body: Dict = None,
        retry_count: int = MAX_RETRIES,
    ) -> Dict:
        """
        API 요청 (재시도 로직 포함)
        
        Args:
            method: HTTP 메서드 (GET/POST)
            endpoint: API 엔드포인트
            tr_id: 거래 ID
            params: 쿼리 파라미터
            json_body: JSON 바디
            retry_count: 재시도 횟수
        
        Returns:
            응답 JSON
        
        Raises:
            Exception: 최대 재시도 후에도 실패
        """
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers(tr_id)
        
        self._stats['total_api_calls'] += 1
        
        last_error = None
        
        for attempt in range(retry_count):
            try:
                if method.upper() == 'GET':
                    response = requests.get(
                        url,
                        headers=headers,
                        params=params,
                        timeout=REQUEST_TIMEOUT
                    )
                else:
                    response = requests.post(
                        url,
                        headers=headers,
                        json=json_body,
                        timeout=REQUEST_TIMEOUT
                    )
                
                # 성공
                if response.status_code == 200:
                    return response.json()
                
                # 401/403: 토큰 만료 → 갱신 후 재시도
                if response.status_code in (401, 403):
                    logger.warning(f"토큰 만료 감지, 갱신 중... (시도 {attempt + 1})")
                    self._token = None
                    headers = self._get_headers(tr_id)
                    continue
                
                # 429: Rate Limit → 대기 후 재시도
                if response.status_code == 429:
                    wait_time = RETRY_DELAY * (attempt + 1)
                    logger.warning(f"Rate Limit 도달, {wait_time}초 대기... (시도 {attempt + 1})")
                    time.sleep(wait_time)
                    continue
                
                # 기타 에러
                error_data = response.json()
                last_error = Exception(
                    f"API 오류 [{response.status_code}]: "
                    f"{error_data.get('msg1', error_data.get('msg', ''))}"
                )
                
            except requests.Timeout:
                last_error = Exception(f"API 타임아웃 (시도 {attempt + 1})")
                logger.warning(str(last_error))
                
            except requests.RequestException as e:
                last_error = Exception(f"네트워크 오류: {e}")
                logger.warning(str(last_error))
            
            # 재시도 전 대기
            if attempt < retry_count - 1:
                time.sleep(RETRY_DELAY)
        
        raise last_error or Exception("알 수 없는 오류")
    
    # =========================================================================
    # 주문 관련
    # =========================================================================
    
    def buy_market(self, stock_code: str, quantity: int) -> OrderResult:
        """
        시장가 매수
        
        Args:
            stock_code: 종목 코드 (6자리)
            quantity: 매수 수량
        
        Returns:
            OrderResult
        """
        return self._place_order(
            stock_code=stock_code,
            quantity=quantity,
            order_type=OrderType.MARKET,
            price=0,
            side=OrderSide.BUY
        )
    
    def buy_limit(self, stock_code: str, quantity: int, price: int) -> OrderResult:
        """
        지정가 매수
        
        Args:
            stock_code: 종목 코드
            quantity: 매수 수량
            price: 지정가
        
        Returns:
            OrderResult
        """
        # 호가단위 정리
        price = round_price(price, 'down')
        
        return self._place_order(
            stock_code=stock_code,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            price=price,
            side=OrderSide.BUY
        )
    
    def sell_market(self, stock_code: str, quantity: int) -> OrderResult:
        """
        시장가 매도
        
        Args:
            stock_code: 종목 코드
            quantity: 매도 수량
        
        Returns:
            OrderResult
        """
        return self._place_order(
            stock_code=stock_code,
            quantity=quantity,
            order_type=OrderType.MARKET,
            price=0,
            side=OrderSide.SELL
        )
    
    def sell_limit(self, stock_code: str, quantity: int, price: int) -> OrderResult:
        """
        지정가 매도
        
        Args:
            stock_code: 종목 코드
            quantity: 매도 수량
            price: 지정가
        
        Returns:
            OrderResult
        """
        # 호가단위 정리
        price = round_price(price, 'up')
        
        return self._place_order(
            stock_code=stock_code,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            price=price,
            side=OrderSide.SELL
        )
    
    def _place_order(
        self,
        stock_code: str,
        quantity: int,
        order_type: OrderType,
        price: int,
        side: OrderSide,
    ) -> OrderResult:
        """
        주문 실행 (내부)
        
        Args:
            stock_code: 종목 코드
            quantity: 수량
            order_type: 주문 유형
            price: 가격 (시장가는 0)
            side: 매수/매도
        
        Returns:
            OrderResult
        """
        self._stats['total_orders'] += 1
        
        # dry_run 모드
        if self.dry_run:
            return self._mock_order(stock_code, quantity, order_type, price, side)
        
        try:
            # TR ID 선택 (실전/모의)
            if self.environment == 'V':
                tr_id = TR_IDS['buy_mock'] if side == OrderSide.BUY else TR_IDS['sell_mock']
            else:
                tr_id = TR_IDS['buy'] if side == OrderSide.BUY else TR_IDS['sell']
            
            # 요청 바디
            body = {
                "CANO": self.account_number,
                "ACNT_PRDT_CD": self.account_code,
                "PDNO": stock_code,
                "ORD_DVSN": order_type.value,
                "ORD_QTY": str(quantity),
                "ORD_UNPR": str(price) if price > 0 else "0",
            }
            
            response = self._request(
                method='POST',
                endpoint='/uapi/domestic-stock/v1/trading/order-cash',
                tr_id=tr_id,
                json_body=body
            )
            
            # 응답 처리
            if response.get('rt_cd') == '0':
                output = response.get('output', {})
                
                result = OrderResult(
                    success=True,
                    order_id=output.get('ODNO', ''),
                    stock_code=stock_code,
                    side=side.value,
                    order_type=order_type.value,
                    price=price,
                    quantity=quantity,
                )
                
                self._stats['success_orders'] += 1
                logger.info(
                    f"✅ 주문 성공: {stock_code} {side.value} {quantity}주 "
                    f"@ {price if price > 0 else '시장가'} (주문번호: {result.order_id})"
                )
                
                return result
            else:
                error_msg = response.get('msg1', response.get('msg', '주문 실패'))
                
                result = OrderResult(
                    success=False,
                    stock_code=stock_code,
                    side=side.value,
                    error=error_msg,
                )
                
                self._stats['failed_orders'] += 1
                logger.error(f"❌ 주문 실패: {stock_code} - {error_msg}")
                
                return result
        
        except Exception as e:
            self._stats['failed_orders'] += 1
            logger.exception(f"❌ 주문 에러: {stock_code} - {e}")
            
            return OrderResult(
                success=False,
                stock_code=stock_code,
                side=side.value,
                error=str(e),
            )
    
    def _mock_order(
        self,
        stock_code: str,
        quantity: int,
        order_type: OrderType,
        price: int,
        side: OrderSide,
    ) -> OrderResult:
        """
        가상 주문 (dry_run 모드)
        """
        self._mock_order_id += 1
        order_id = str(self._mock_order_id)
        
        # 시장가인 경우 현재가로 가정 (실제로는 현재가 조회 필요)
        if price == 0:
            price = 50000  # 임시 가격
        
        result = OrderResult(
            success=True,
            order_id=order_id,
            stock_code=stock_code,
            side=side.value,
            order_type=order_type.value,
            price=price,
            quantity=quantity,
            filled_qty=quantity,
            filled_price=price,
        )
        
        # 가상 포지션 업데이트
        if side == OrderSide.BUY:
            if stock_code in self._mock_positions:
                pos = self._mock_positions[stock_code]
                new_qty = pos['quantity'] + quantity
                pos['avg_price'] = (
                    (pos['avg_price'] * pos['quantity'] + price * quantity) / new_qty
                )
                pos['quantity'] = new_qty
            else:
                self._mock_positions[stock_code] = {
                    'quantity': quantity,
                    'avg_price': price,
                }
        else:  # SELL
            if stock_code in self._mock_positions:
                pos = self._mock_positions[stock_code]
                pos['quantity'] -= quantity
                if pos['quantity'] <= 0:
                    del self._mock_positions[stock_code]
        
        self._stats['success_orders'] += 1
        logger.info(
            f"🔸 [DRY RUN] 주문: {stock_code} {side.value} {quantity}주 "
            f"@ {price} (주문번호: {order_id})"
        )
        
        return result
    
    def cancel_order(
        self,
        order_id: str,
        stock_code: str,
        quantity: int,
    ) -> bool:
        """
        주문 취소
        
        Args:
            order_id: 주문 번호
            stock_code: 종목 코드
            quantity: 취소 수량
        
        Returns:
            취소 성공 여부
        """
        if self.dry_run:
            logger.info(f"🔸 [DRY RUN] 주문 취소: {order_id}")
            return True
        
        try:
            # TR ID 선택 (실전/모의)
            tr_id = TR_IDS['cancel_mock'] if self.environment == 'V' else TR_IDS['cancel']
            
            body = {
                "CANO": self.account_number,
                "ACNT_PRDT_CD": self.account_code,
                "KRX_FWDG_ORD_ORGNO": "",
                "ORGN_ODNO": order_id,
                "ORD_DVSN": "00",
                "RVSE_CNCL_DVSN_CD": "02",  # 02: 취소
                "ORD_QTY": str(quantity),
                "ORD_UNPR": "0",
                "QTY_ALL_ORD_YN": "Y",
            }
            
            response = self._request(
                method='POST',
                endpoint='/uapi/domestic-stock/v1/trading/order-rvsecncl',
                tr_id=tr_id,
                json_body=body
            )
            
            success = response.get('rt_cd') == '0'
            
            if success:
                logger.info(f"✅ 주문 취소 성공: {order_id}")
            else:
                logger.error(f"❌ 주문 취소 실패: {response.get('msg1', '')}")
            
            return success
        
        except Exception as e:
            logger.exception(f"❌ 주문 취소 에러: {e}")
            return False
    
    def cancel_all_pending_orders(self) -> int:
        """
        모든 미체결 주문 취소
        
        Returns:
            취소된 주문 수
        """
        pending = self.get_pending_orders()
        cancelled = 0
        
        for order in pending:
            if self.cancel_order(
                order_id=order.order_id,
                stock_code=order.stock_code,
                quantity=order.pending_qty,
            ):
                cancelled += 1
        
        logger.info(f"미체결 주문 {cancelled}/{len(pending)}건 취소 완료")
        return cancelled
    
    # =========================================================================
    # 조회 관련
    # =========================================================================
    
    def get_balance(self) -> Dict:
        """
        계좌 잔고 조회
        
        Returns:
            잔고 딕셔너리
            {
                'total_eval': float,       # 총 평가금액
                'total_profit': float,     # 총 평가손익
                'cash': float,             # 예수금
                'available_cash': float,   # 주문가능금액
            }
        """
        if self.dry_run:
            return {
                'total_eval': 10000000,
                'total_profit': 0,
                'cash': 5000000,
                'available_cash': 5000000,
            }
        
        try:
            tr_id = TR_IDS['balance_mock'] if self.environment == 'V' else TR_IDS['balance']
            
            params = {
                "CANO": self.account_number,
                "ACNT_PRDT_CD": self.account_code,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }
            
            response = self._request(
                method='GET',
                endpoint='/uapi/domestic-stock/v1/trading/inquire-balance',
                tr_id=tr_id,
                params=params
            )
            
            output2 = response.get('output2', [{}])[0] if response.get('output2') else {}
            
            return {
                'total_eval': float(output2.get('scts_evlu_amt', 0)),
                'total_profit': float(output2.get('evlu_pfls_smtl_amt', 0)),
                'cash': float(output2.get('prvs_rcdl_excc_amt', 0)),
                'available_cash': float(output2.get('nxdy_excc_amt', 0)),
                'raw_response': response,
            }
        
        except Exception as e:
            logger.error(f"잔고 조회 에러: {e}")
            return {
                'total_eval': 0,
                'total_profit': 0,
                'cash': 0,
                'available_cash': 0,
            }
    
    def get_positions(self) -> List[Position]:
        """
        보유 종목 조회
        
        Returns:
            Position 리스트
        """
        if self.dry_run:
            positions = []
            for code, data in self._mock_positions.items():
                positions.append(Position(
                    stock_code=code,
                    stock_name=f"종목{code}",
                    quantity=data['quantity'],
                    avg_price=data['avg_price'],
                    current_price=data['avg_price'],
                    profit_loss=0,
                    profit_pct=0,
                ))
            return positions
        
        try:
            tr_id = TR_IDS['balance_mock'] if self.environment == 'V' else TR_IDS['balance']
            
            params = {
                "CANO": self.account_number,
                "ACNT_PRDT_CD": self.account_code,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }
            
            response = self._request(
                method='GET',
                endpoint='/uapi/domestic-stock/v1/trading/inquire-balance',
                tr_id=tr_id,
                params=params
            )
            
            positions = []
            
            for item in response.get('output1', []):
                quantity = int(item.get('hldg_qty', 0))
                if quantity <= 0:
                    continue
                
                positions.append(Position(
                    stock_code=item.get('pdno', ''),
                    stock_name=item.get('prdt_name', ''),
                    quantity=quantity,
                    avg_price=float(item.get('pchs_avg_pric', 0)),
                    current_price=float(item.get('prpr', 0)),
                    profit_loss=float(item.get('evlu_pfls_amt', 0)),
                    profit_pct=float(item.get('evlu_pfls_rt', 0)),
                ))
            
            return positions
        
        except Exception as e:
            logger.error(f"보유종목 조회 에러: {e}")
            return []
    
    def get_pending_orders(self) -> List[PendingOrder]:
        """
        미체결 주문 조회
        
        Returns:
            PendingOrder 리스트
        """
        if self.dry_run:
            return []
        
        try:
            tr_id = TR_IDS['pending_mock'] if self.environment == 'V' else TR_IDS['pending']
            
            params = {
                "CANO": self.account_number,
                "ACNT_PRDT_CD": self.account_code,
                "INQR_STRT_DT": "",
                "INQR_END_DT": "",
                "SLL_BUY_DVSN_CD": "00",  # 전체
                "INQR_DVSN": "00",
                "PDNO": "",
                "CCLD_DVSN": "01",  # 미체결
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }
            
            response = self._request(
                method='GET',
                endpoint='/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl',
                tr_id=tr_id,
                params=params
            )
            
            orders = []
            
            for item in response.get('output', []):
                pending_qty = int(item.get('psbl_qty', 0))
                if pending_qty <= 0:
                    continue
                
                orders.append(PendingOrder(
                    order_id=item.get('odno', ''),
                    stock_code=item.get('pdno', ''),
                    stock_name=item.get('prdt_name', ''),
                    side='buy' if item.get('sll_buy_dvsn_cd') == '02' else 'sell',
                    order_type=item.get('ord_dvsn_cd', ''),
                    order_qty=int(item.get('ord_qty', 0)),
                    filled_qty=int(item.get('tot_ccld_qty', 0)),
                    pending_qty=pending_qty,
                    order_price=float(item.get('ord_unpr', 0)),
                    order_time=item.get('ord_tmd', ''),
                ))
            
            return orders
        
        except Exception as e:
            logger.error(f"미체결 조회 에러: {e}")
            return []
    
    def get_current_price(self, stock_code: str) -> float:
        """
        현재가 조회
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            현재가 (0이면 조회 실패)
        """
        try:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
            }
            
            response = self._request(
                method='GET',
                endpoint='/uapi/domestic-stock/v1/quotations/inquire-price',
                tr_id=TR_IDS['price'],
                params=params
            )
            
            return float(response.get('output', {}).get('stck_prpr', 0))
        
        except Exception as e:
            logger.error(f"현재가 조회 에러 ({stock_code}): {e}")
            return 0
    
    def get_stock_info(self, stock_code: str) -> Dict:
        """
        종목 상세 정보 조회
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            종목 정보 딕셔너리
        """
        try:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
            }
            
            response = self._request(
                method='GET',
                endpoint='/uapi/domestic-stock/v1/quotations/inquire-price',
                tr_id=TR_IDS['price'],
                params=params
            )
            
            output = response.get('output', {})
            
            return {
                'stock_code': stock_code,
                'stock_name': output.get('stck_shrn_iscd', ''),
                'current_price': float(output.get('stck_prpr', 0)),
                'change': float(output.get('prdy_vrss', 0)),
                'change_pct': float(output.get('prdy_ctrt', 0)),
                'open': float(output.get('stck_oprc', 0)),
                'high': float(output.get('stck_hgpr', 0)),
                'low': float(output.get('stck_lwpr', 0)),
                'volume': int(output.get('acml_vol', 0)),
                'trade_amount': int(output.get('acml_tr_pbmn', 0)),
            }
        
        except Exception as e:
            logger.error(f"종목 정보 조회 에러 ({stock_code}): {e}")
            return {}
    
    def get_index_price(self, index_code: str = '0001') -> Dict:
        """
        지수 현재가 조회
        
        Args:
            index_code: 지수 코드
                - 0001: 코스피
                - 1001: 코스닥
                - 2001: 코스피200
        
        Returns:
            지수 정보 딕셔너리
            {
                'price': float,        # 지수
                'change': float,       # 전일대비
                'change_pct': float,   # 등락률 (%)
            }
        """
        try:
            params = {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": index_code,
            }
            
            response = self._request(
                method='GET',
                endpoint='/uapi/domestic-stock/v1/quotations/inquire-index-price',
                tr_id=TR_IDS['index'],
                params=params
            )
            
            output = response.get('output', {})
            
            return {
                'price': float(output.get('bstp_nmix_prpr', 0)),
                'change': float(output.get('bstp_nmix_prdy_vrss', 0)),
                'change_pct': float(output.get('bstp_nmix_prdy_ctrt', 0)),
            }
        
        except Exception as e:
            logger.error(f"지수 조회 에러 ({index_code}): {e}")
            return {'price': 0, 'change': 0, 'change_pct': 0}
    
    # =========================================================================
    # 일봉 데이터 조회
    # =========================================================================
    
    def get_daily_ohlcv(
        self,
        stock_code: str,
        period: int = 100,
    ) -> List[Dict]:
        """
        일봉 데이터 조회
        
        Args:
            stock_code: 종목 코드
            period: 조회 기간 (일)
        
        Returns:
            일봉 데이터 리스트
        """
        try:
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            }
            
            response = self._request(
                method='GET',
                endpoint='/uapi/domestic-stock/v1/quotations/inquire-daily-price',
                tr_id=TR_IDS['daily_ohlcv'],
                params=params
            )
            
            ohlcv_list = []
            
            for item in response.get('output', [])[:period]:
                ohlcv_list.append({
                    'date': item.get('stck_bsop_date', ''),
                    'open': float(item.get('stck_oprc', 0)),
                    'high': float(item.get('stck_hgpr', 0)),
                    'low': float(item.get('stck_lwpr', 0)),
                    'close': float(item.get('stck_clpr', 0)),
                    'volume': int(item.get('acml_vol', 0)),
                    'change_pct': float(item.get('prdy_ctrt', 0)),
                })
            
            return ohlcv_list
        
        except Exception as e:
            logger.error(f"일봉 조회 에러 ({stock_code}): {e}")
            return []
    
    def get_index_daily(
        self,
        index_code: str = '0001',
        period: int = 60,
    ) -> List[float]:
        """
        지수 일봉 종가 데이터 조회 (MA 계산용)
        
        FinanceDataReader를 사용하여 코스피/코스닥 지수의 일봉 종가를 가져옵니다.
        
        Args:
            index_code: 지수 코드
                - 0001: 코스피 → KS11
                - 1001: 코스닥 → KQ11
            period: 조회 기간 (일)
        
        Returns:
            종가 리스트 (오래된 순)
        """
        try:
            import FinanceDataReader as fdr
            from datetime import datetime, timedelta
            
            # 지수 코드 매핑
            fdr_code_map = {
                '0001': 'KS11',   # 코스피
                '1001': 'KQ11',   # 코스닥
                '2001': 'KS200',  # 코스피200
            }
            
            fdr_code = fdr_code_map.get(index_code, 'KS11')
            
            # 조회 기간 설정 (여유있게 +30일)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=period + 30)
            
            # FinanceDataReader로 데이터 조회
            df = fdr.DataReader(fdr_code, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))
            
            if df is None or df.empty:
                logger.warning(f"지수 일봉 데이터 없음 ({index_code})")
                return []
            
            # 종가 리스트 추출 (오래된 순)
            close_prices = df['Close'].tolist()[-period:]
            
            logger.info(f"지수 일봉 조회 완료: {index_code} ({len(close_prices)}일)")
            return close_prices
        
        except ImportError:
            logger.warning("FinanceDataReader가 설치되어 있지 않습니다. pip install FinanceDataReader")
            return []
        
        except Exception as e:
            logger.error(f"지수 일봉 조회 에러 ({index_code}): {e}")
            return []
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def get_stats(self) -> Dict:
        """브로커 통계 조회"""
        return {
            **self._stats,
            'dry_run': self.dry_run,
            'environment': self.environment,
        }
    
    def health_check(self) -> bool:
        """
        API 연결 상태 확인
        
        Returns:
            True: 정상, False: 비정상
        """
        try:
            # 토큰 갱신으로 연결 확인
            self._get_token()
            
            # 지수 조회로 데이터 확인
            index = self.get_index_price('0001')
            
            return index.get('price', 0) > 0
        
        except Exception as e:
            logger.error(f"Health check 실패: {e}")
            return False


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("KIS Broker 테스트 (DRY RUN 모드)")
    print("=" * 60)
    
    # 테스트 설정 (dry_run 모드)
    test_config = {
        'app_key': 'TEST_APP_KEY',
        'app_secret': 'TEST_APP_SECRET',
        'account_number': '12345678',
        'account_code': '01',
        'base_url': 'https://openapi.koreainvestment.com:9443',
        'environment': 'P',
    }
    
    # 브로커 생성 (dry_run 모드)
    broker = KISBroker(test_config, dry_run=True)
    
    # 1. 호가단위 테스트
    print("\n1. 호가단위 테스트:")
    test_prices = [500, 3000, 7000, 25000, 75000, 300000, 800000]
    for price in test_prices:
        tick = get_tick_size(price)
        rounded = round_price(price + tick / 2, 'round')
        print(f"   {price:>8}원 → 호가단위: {tick:>4}원, 반올림: {rounded:>8}원")
    
    # 2. 가상 매수 테스트
    print("\n2. 가상 매수 테스트:")
    result = broker.buy_market("005930", 10)
    print(f"   결과: {'성공' if result.success else '실패'}")
    print(f"   주문번호: {result.order_id}")
    
    result = broker.buy_limit("000660", 5, 85000)
    print(f"   지정가 매수: {'성공' if result.success else '실패'}")
    
    # 3. 보유 종목 조회
    print("\n3. 보유 종목 조회:")
    positions = broker.get_positions()
    for pos in positions:
        print(f"   {pos.stock_code}: {pos.quantity}주 @ {pos.avg_price:,.0f}원")
    
    # 4. 가상 매도 테스트
    print("\n4. 가상 매도 테스트:")
    result = broker.sell_market("005930", 5)
    print(f"   결과: {'성공' if result.success else '실패'}")
    
    # 5. 잔고 조회
    print("\n5. 잔고 조회:")
    balance = broker.get_balance()
    print(f"   총 평가금액: {balance['total_eval']:,.0f}원")
    print(f"   예수금: {balance['cash']:,.0f}원")
    
    # 6. 통계
    print("\n6. 브로커 통계:")
    stats = broker.get_stats()
    for key, value in stats.items():
        print(f"   {key}: {value}")
    
    print("\n" + "=" * 60)
    print("테스트 완료 (DRY RUN 모드)")
    print("=" * 60)
