#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Realtime Feed (실시간 시세)
============================================================================
한투 API 웹소켓을 통한 실시간 시세 수신

핵심 기능:
- 웹소켓 연결 관리 (자동 재연결)
- 실시간 체결가 수신
- 실시간 호가 수신
- 콜백 기반 이벤트 처리

사용법:
    feed = RealtimeFeed(app_key, app_secret)
    feed.subscribe("005930", on_price=callback)
    feed.start()
============================================================================
"""

import json
import time
import logging
import threading
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
import websocket
import requests

# 로거 설정
logger = logging.getLogger('ScalpingBot.Realtime')


# =============================================================================
# 상수 및 열거형
# =============================================================================

# 웹소켓 URL
WS_URL_REAL = "ws://ops.koreainvestment.com:21000"      # 실전
WS_URL_VIRTUAL = "ws://ops.koreainvestment.com:31000"   # 모의

# API URL (승인키 발급용)
API_URL_REAL = "https://openapi.koreainvestment.com:9443"
API_URL_VIRTUAL = "https://openapivts.koreainvestment.com:29443"

# TR ID
TR_PRICE = "H0STCNT0"      # 실시간 체결가
TR_ORDERBOOK = "H0STASP0"  # 실시간 호가

# 재연결 설정
RECONNECT_DELAY = 3
MAX_RECONNECT_ATTEMPTS = 10


class FeedType(Enum):
    """피드 유형"""
    PRICE = "price"          # 체결가
    ORDERBOOK = "orderbook"  # 호가


@dataclass
class PriceTick:
    """체결가 틱"""
    stock_code: str
    price: float
    volume: int
    change: float
    change_pct: float
    trade_time: str
    ask_price: float = 0
    bid_price: float = 0
    
    def to_dict(self) -> Dict:
        return {
            'stock_code': self.stock_code,
            'price': self.price,
            'volume': self.volume,
            'change': self.change,
            'change_pct': self.change_pct,
            'trade_time': self.trade_time,
        }


@dataclass
class OrderbookTick:
    """호가 틱"""
    stock_code: str
    ask_prices: List[float]   # 매도호가 (1~10)
    bid_prices: List[float]   # 매수호가 (1~10)
    ask_volumes: List[int]    # 매도잔량
    bid_volumes: List[int]    # 매수잔량
    timestamp: str


# =============================================================================
# 실시간 피드 클래스
# =============================================================================

class RealtimeFeed:
    """
    실시간 시세 피드
    
    한투 API 웹소켓을 통해 실시간 체결가와 호가를 수신합니다.
    """
    
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        is_virtual: bool = True,
        on_price: Callable[[PriceTick], None] = None,
        on_orderbook: Callable[[OrderbookTick], None] = None,
        on_connect: Callable[[], None] = None,
        on_disconnect: Callable[[], None] = None,
    ):
        """
        초기화
        
        Args:
            app_key: 한투 API 앱 키
            app_secret: 한투 API 앱 시크릿
            is_virtual: 모의투자 여부
            on_price: 체결가 콜백
            on_orderbook: 호가 콜백
            on_connect: 연결 콜백
            on_disconnect: 연결 해제 콜백
        """
        self.app_key = app_key
        self.app_secret = app_secret
        self.is_virtual = is_virtual
        
        # 콜백
        self.on_price = on_price
        self.on_orderbook = on_orderbook
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        
        # URL 설정
        self.ws_url = WS_URL_VIRTUAL if is_virtual else WS_URL_REAL
        self.api_url = API_URL_VIRTUAL if is_virtual else API_URL_REAL
        
        # 웹소켓
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        
        # 승인키
        self._approval_key: Optional[str] = None
        
        # 구독 목록
        self._subscriptions: Dict[str, set] = {
            'price': set(),
            'orderbook': set(),
        }
        
        # 재연결
        self._reconnect_count = 0
        self._lock = threading.Lock()
        
        # 최근 가격 캐시
        self._price_cache: Dict[str, PriceTick] = {}
        
        logger.info(f"RealtimeFeed 초기화 (모의: {is_virtual})")
    
    # =========================================================================
    # 승인키 발급
    # =========================================================================
    
    def _get_approval_key(self) -> Optional[str]:
        """웹소켓 승인키 발급"""
        try:
            response = requests.post(
                f"{self.api_url}/oauth2/Approval",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "secretkey": self.app_secret,
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                key = data.get('approval_key')
                logger.info("웹소켓 승인키 발급 완료")
                return key
            else:
                logger.error(f"승인키 발급 실패: {response.status_code}")
                return None
        
        except Exception as e:
            logger.error(f"승인키 발급 에러: {e}")
            return None
    
    # =========================================================================
    # 연결 관리
    # =========================================================================
    
    def start(self):
        """웹소켓 연결 시작"""
        if self._running:
            logger.warning("이미 실행 중")
            return
        
        # 승인키 발급
        self._approval_key = self._get_approval_key()
        if not self._approval_key:
            logger.error("승인키 없이 연결 불가")
            return
        
        self._running = True
        self._reconnect_count = 0
        
        # 웹소켓 스레드 시작
        self._ws_thread = threading.Thread(
            target=self._connect,
            name="Realtime-WS",
            daemon=True
        )
        self._ws_thread.start()
        
        logger.info("RealtimeFeed 시작")
    
    def stop(self):
        """웹소켓 연결 종료"""
        self._running = False
        
        if self._ws:
            try:
                self._ws.close()
            except:
                pass
        
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        
        self._connected = False
        logger.info("RealtimeFeed 종료")
    
    def _connect(self):
        """웹소켓 연결"""
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
                
            except Exception as e:
                logger.error(f"웹소켓 연결 에러: {e}")
            
            if self._running:
                self._reconnect_count += 1
                
                if self._reconnect_count > MAX_RECONNECT_ATTEMPTS:
                    logger.error("최대 재연결 시도 초과")
                    break
                
                logger.info(f"재연결 시도 {self._reconnect_count}/{MAX_RECONNECT_ATTEMPTS}")
                time.sleep(RECONNECT_DELAY)
    
    # =========================================================================
    # 웹소켓 이벤트 핸들러
    # =========================================================================
    
    def _on_open(self, ws):
        """연결 성공"""
        logger.info("웹소켓 연결됨")
        self._connected = True
        self._reconnect_count = 0
        
        # 기존 구독 복원
        self._resubscribe_all()
        
        if self.on_connect:
            try:
                self.on_connect()
            except Exception as e:
                logger.error(f"연결 콜백 에러: {e}")
    
    def _on_message(self, ws, message):
        """메시지 수신"""
        try:
            # 첫 바이트로 암호화 여부 확인
            if message[0] in ['0', '1']:
                # 실시간 데이터
                self._parse_realtime_data(message)
            else:
                # JSON 응답 (구독 결과 등)
                data = json.loads(message)
                self._handle_response(data)
        
        except Exception as e:
            logger.debug(f"메시지 파싱 에러: {e}")
    
    def _on_error(self, ws, error):
        """에러 발생"""
        logger.error(f"웹소켓 에러: {error}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        """연결 종료"""
        logger.warning(f"웹소켓 종료: {close_status_code} - {close_msg}")
        self._connected = False
        
        if self.on_disconnect:
            try:
                self.on_disconnect()
            except Exception as e:
                logger.error(f"연결 해제 콜백 에러: {e}")
    
    # =========================================================================
    # 데이터 파싱
    # =========================================================================
    
    def _parse_realtime_data(self, message: str):
        """실시간 데이터 파싱"""
        parts = message.split('|')
        
        if len(parts) < 4:
            return
        
        encrypted = parts[0]
        tr_id = parts[1]
        count = parts[2]
        data = parts[3]
        
        if tr_id == TR_PRICE:
            self._parse_price_data(data)
        elif tr_id == TR_ORDERBOOK:
            self._parse_orderbook_data(data)
    
    def _parse_price_data(self, data: str):
        """체결가 데이터 파싱"""
        try:
            fields = data.split('^')
            
            if len(fields) < 20:
                return
            
            tick = PriceTick(
                stock_code=fields[0],
                price=float(fields[2]),
                volume=int(fields[12]),
                change=float(fields[4]),
                change_pct=float(fields[5]),
                trade_time=fields[1],
                ask_price=float(fields[6]) if fields[6] else 0,
                bid_price=float(fields[7]) if fields[7] else 0,
            )
            
            # 캐시 업데이트
            self._price_cache[tick.stock_code] = tick
            
            # 콜백 호출
            if self.on_price:
                try:
                    self.on_price(tick)
                except Exception as e:
                    logger.error(f"체결가 콜백 에러: {e}")
        
        except Exception as e:
            logger.debug(f"체결가 파싱 에러: {e}")
    
    def _parse_orderbook_data(self, data: str):
        """호가 데이터 파싱"""
        try:
            fields = data.split('^')
            
            if len(fields) < 40:
                return
            
            stock_code = fields[0]
            
            # 매도호가 (1~10)
            ask_prices = [float(fields[3 + i*2]) for i in range(10)]
            ask_volumes = [int(fields[4 + i*2]) for i in range(10)]
            
            # 매수호가 (1~10)
            bid_prices = [float(fields[23 + i*2]) for i in range(10)]
            bid_volumes = [int(fields[24 + i*2]) for i in range(10)]
            
            tick = OrderbookTick(
                stock_code=stock_code,
                ask_prices=ask_prices,
                bid_prices=bid_prices,
                ask_volumes=ask_volumes,
                bid_volumes=bid_volumes,
                timestamp=fields[1],
            )
            
            # 콜백 호출
            if self.on_orderbook:
                try:
                    self.on_orderbook(tick)
                except Exception as e:
                    logger.error(f"호가 콜백 에러: {e}")
        
        except Exception as e:
            logger.debug(f"호가 파싱 에러: {e}")
    
    def _handle_response(self, data: dict):
        """JSON 응답 처리"""
        header = data.get('header', {})
        tr_id = header.get('tr_id', '')
        msg_cd = header.get('msg_cd', '')
        
        if msg_cd == '0000':
            logger.debug(f"구독 성공: {tr_id}")
        else:
            logger.warning(f"응답: {msg_cd} - {header.get('msg1', '')}")
    
    # =========================================================================
    # 구독 관리
    # =========================================================================
    
    def subscribe_price(self, stock_code: str):
        """체결가 구독"""
        with self._lock:
            self._subscriptions['price'].add(stock_code)
        
        if self._connected:
            self._send_subscribe(TR_PRICE, stock_code, '1')
        
        logger.info(f"체결가 구독: {stock_code}")
    
    def subscribe_orderbook(self, stock_code: str):
        """호가 구독"""
        with self._lock:
            self._subscriptions['orderbook'].add(stock_code)
        
        if self._connected:
            self._send_subscribe(TR_ORDERBOOK, stock_code, '1')
        
        logger.info(f"호가 구독: {stock_code}")
    
    def unsubscribe_price(self, stock_code: str):
        """체결가 구독 해제"""
        with self._lock:
            self._subscriptions['price'].discard(stock_code)
        
        if self._connected:
            self._send_subscribe(TR_PRICE, stock_code, '2')
        
        logger.info(f"체결가 구독 해제: {stock_code}")
    
    def unsubscribe_orderbook(self, stock_code: str):
        """호가 구독 해제"""
        with self._lock:
            self._subscriptions['orderbook'].discard(stock_code)
        
        if self._connected:
            self._send_subscribe(TR_ORDERBOOK, stock_code, '2')
        
        logger.info(f"호가 구독 해제: {stock_code}")
    
    def _send_subscribe(self, tr_id: str, stock_code: str, tr_type: str):
        """구독 요청 전송"""
        if not self._ws or not self._connected:
            return
        
        message = {
            "header": {
                "approval_key": self._approval_key,
                "custtype": "P",
                "tr_type": tr_type,  # 1: 등록, 2: 해제
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": stock_code,
                }
            }
        }
        
        try:
            self._ws.send(json.dumps(message))
        except Exception as e:
            logger.error(f"구독 요청 전송 에러: {e}")
    
    def _resubscribe_all(self):
        """모든 구독 복원"""
        with self._lock:
            for code in self._subscriptions['price']:
                self._send_subscribe(TR_PRICE, code, '1')
            
            for code in self._subscriptions['orderbook']:
                self._send_subscribe(TR_ORDERBOOK, code, '1')
    
    # =========================================================================
    # 유틸리티
    # =========================================================================
    
    def is_connected(self) -> bool:
        """연결 상태"""
        return self._connected
    
    def get_last_price(self, stock_code: str) -> Optional[PriceTick]:
        """마지막 체결가 조회"""
        return self._price_cache.get(stock_code)
    
    def get_subscriptions(self) -> Dict[str, List[str]]:
        """구독 목록 조회"""
        with self._lock:
            return {
                'price': list(self._subscriptions['price']),
                'orderbook': list(self._subscriptions['orderbook']),
            }


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    print("=" * 60)
    print("RealtimeFeed 테스트 (연결 없이)")
    print("=" * 60)
    
    # 콜백 함수
    def on_price(tick: PriceTick):
        print(f"체결: {tick.stock_code} {tick.price:,.0f}원 ({tick.change_pct:+.2f}%)")
    
    def on_orderbook(tick: OrderbookTick):
        print(f"호가: {tick.stock_code} 매수1 {tick.bid_prices[0]:,.0f}")
    
    # 피드 생성 (연결하지 않음)
    feed = RealtimeFeed(
        app_key="TEST_KEY",
        app_secret="TEST_SECRET",
        is_virtual=True,
        on_price=on_price,
        on_orderbook=on_orderbook,
    )
    
    print("\n1. 구독 설정 (연결 전):")
    feed.subscribe_price("005930")
    feed.subscribe_price("000660")
    feed.subscribe_orderbook("005930")
    
    print("\n2. 구독 목록:")
    subs = feed.get_subscriptions()
    print(f"   체결가: {subs['price']}")
    print(f"   호가: {subs['orderbook']}")
    
    print("\n3. 연결 상태:")
    print(f"   연결됨: {feed.is_connected()}")
    
    print("\n" + "=" * 60)
    print("테스트 완료 (실제 연결은 API 키 필요)")
    print("=" * 60)
