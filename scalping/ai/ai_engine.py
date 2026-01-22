#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - AI Engine (Qwen3 비동기 처리)
============================================================================
Qwen3:8b 모델을 이용한 비동기 AI 판단 엔진

핵심 기능:
- 비동기 Queue 처리 (메인 스레드 블로킹 방지)
- request_queue: 분석 요청 큐
- result_queue: 분석 결과 큐
- Qwen3 API 호출 (Ollama)
- JSON 파싱 강화 (thinking 태그 제거, fallback 로직)
- 타임아웃 10초 (실매매 환경에 적합)
- 누적 학습 연동

사용법:
    ai_engine = AIEngine(config['ai'])
    ai_engine.start()  # 워커 스레드 시작
    
    # 분석 요청 (비동기)
    ai_engine.request_analysis(stock_code, stock_name, indicators, ...)
    
    # 결과 확인 (논블로킹)
    result = ai_engine.get_result()
    if result:
        print(f"결정: {result['decision']}, 신뢰도: {result['confidence']}")
============================================================================
"""

import re
import json
import time
import logging
import requests
import threading
from queue import Queue, Empty
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

# 로거 설정
logger = logging.getLogger('ScalpingBot.AI')


# =============================================================================
# 데이터 클래스
# =============================================================================

@dataclass
class AIRequest:
    """AI 분석 요청 데이터"""
    stock_code: str                    # 종목 코드
    stock_name: str                    # 종목명
    indicators: Dict[str, Any]         # 기술적 지표
    rule_score: float                  # 규칙 기반 점수 (0~100)
    market_state: Dict[str, Any]       # 시장 상태
    current_price: float               # 현재가
    timestamp: float = field(default_factory=time.time)


@dataclass
class AIResult:
    """AI 분석 결과 데이터"""
    stock_code: str                    # 종목 코드
    stock_name: str                    # 종목명
    decision: str                      # BUY / HOLD / SELL
    confidence: float                  # 신뢰도 (0.0 ~ 1.0)
    reason: str                        # 판단 이유
    original_price: float              # 분석 시점 가격
    elapsed: float                     # AI 응답 시간 (초)
    timestamp: float = field(default_factory=time.time)


# =============================================================================
# AI 엔진 클래스
# =============================================================================

class AIEngine:
    """
    Qwen3 AI 엔진 (비동기 Queue 방식)
    
    워커 스레드가 request_queue에서 요청을 꺼내 처리하고
    결과를 result_queue에 넣습니다. 메인 스레드는 블로킹 없이
    다음 작업을 계속할 수 있습니다.
    """
    
    def __init__(self, config: dict, secrets: dict = None):
        """
        AI 엔진 초기화
        
        Args:
            config: AI 설정 딕셔너리
                - provider: AI 제공자 (ollama / gemini)
                - api_url: Ollama API 엔드포인트 (ollama 사용 시)
                - model: 사용할 모델명
                - timeout: API 타임아웃 (초)
                - max_queue_size: 최대 큐 크기
                - retry_count: 재시도 횟수
            secrets: API 키 등 비밀 설정
        """
        self.config = config
        self.secrets = secrets or {}
        
        # 🆕 AI 제공자 설정 (ollama / gemini)
        self.provider = config.get('provider', 'ollama').lower()
        
        # API 설정
        if self.provider == 'gemini':
            # Gemini API 설정
            self.model = config.get('model', 'gemini-2.0-flash-exp')
            self.api_key = self.secrets.get('gemini', {}).get('api_key', '')
            self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            if not self.api_key:
                logger.warning("⚠️ Gemini API 키가 설정되지 않았습니다. secrets.yaml을 확인하세요.")
        else:
            # Ollama API 설정 (기본)
            self.api_url = config.get('api_url', 'http://localhost:11434/api/generate')
            self.model = config.get('model', 'qwen3:8b')
            self.api_key = None
        
        self.timeout = config.get('timeout', 10)
        self.max_queue_size = config.get('max_queue_size', 50)
        self.retry_count = config.get('retry_count', 2)
        self.min_confidence = config.get('min_confidence', 0.6)
        
        # 비동기 Queue
        self.request_queue: Queue[Dict] = Queue(maxsize=self.max_queue_size)
        self.result_queue: Queue[Dict] = Queue()
        
        # 워커 스레드 관리
        self._worker: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        
        # 통계
        self._stats = {
            'total_requests': 0,
            'success_count': 0,
            'timeout_count': 0,
            'error_count': 0,
            'avg_response_time': 0.0,
        }
        
        # 누적 학습 저장소 (지연 로딩)
        self._learning_store = None
        
        provider_display = f"Gemini ({self.model})" if self.provider == 'gemini' else f"Ollama ({self.model})"
        logger.info(f"AI 엔진 초기화 완료 (제공자: {provider_display}, 타임아웃: {self.timeout}초)")
    
    # =========================================================================
    # 누적 학습 저장소
    # =========================================================================
    
    @property
    def learning_store(self):
        """누적 학습 저장소 (지연 로딩)"""
        if self._learning_store is None:
            from scalping.ai.learning_store import LearningStore
            self._learning_store = LearningStore()
        return self._learning_store
    
    # =========================================================================
    # 워커 스레드 관리
    # =========================================================================
    
    def start(self):
        """
        AI 워커 스레드 시작
        
        이미 실행 중이면 무시합니다.
        """
        with self._lock:
            if self._running:
                logger.warning("AI 워커가 이미 실행 중입니다.")
                return
            
            self._running = True
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="AI-Worker",
                daemon=True  # 메인 스레드 종료 시 함께 종료
            )
            self._worker.start()
            logger.info("🧠 AI 워커 스레드 시작")
    
    def stop(self):
        """
        AI 워커 스레드 중지
        
        현재 처리 중인 요청은 완료될 때까지 대기합니다.
        """
        with self._lock:
            if not self._running:
                return
            
            self._running = False
            logger.info("AI 워커 중지 요청...")
            
            # 워커 스레드 종료 대기 (최대 5초)
            if self._worker and self._worker.is_alive():
                self._worker.join(timeout=5)
            
            logger.info("🛑 AI 워커 스레드 중지됨")
    
    def is_running(self) -> bool:
        """워커 실행 상태 확인"""
        return self._running
    
    # =========================================================================
    # 분석 요청/결과 인터페이스
    # =========================================================================
    
    def request_analysis(
        self,
        stock_code: str,
        stock_name: str,
        indicators: Dict[str, Any],
        rule_score: float,
        market_state: Dict[str, Any],
        current_price: float,
    ) -> bool:
        """
        AI 분석 요청 (비동기)
        
        요청을 큐에 넣고 즉시 반환합니다.
        메인 스레드는 블로킹 없이 다음 작업을 계속할 수 있습니다.
        
        Args:
            stock_code: 종목 코드 (예: "005930")
            stock_name: 종목명 (예: "삼성전자")
            indicators: 기술적 지표 딕셔너리
                - cci: CCI 값
                - change_pct: 등락률 (%)
                - distance_ma20: 20일선 이격도 (%)
                - volume_ratio: 거래량 비율
                - consec_bullish: 연속 상승일
            rule_score: 규칙 기반 점수 (0~100)
            market_state: 시장 상태
                - mode: NORMAL / CONSERVATIVE / EMERGENCY
                - change: 코스피 등락률
                - above_ma20: MA20 위 여부
            current_price: 현재가
        
        Returns:
            True: 요청 성공
            False: 큐가 가득 참
        """
        if not self._running:
            logger.warning("AI 워커가 실행 중이 아닙니다.")
            return False
        
        # 큐가 가득 찼는지 확인
        if self.request_queue.full():
            logger.warning(f"AI 요청 큐가 가득 참 ({self.max_queue_size}개)")
            return False
        
        # 요청 데이터 생성
        request = {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'indicators': indicators,
            'rule_score': rule_score,
            'market_state': market_state,
            'current_price': current_price,
            'timestamp': time.time(),
        }
        
        try:
            self.request_queue.put_nowait(request)
            self._stats['total_requests'] += 1
            logger.debug(f"AI 분석 요청: {stock_code} {stock_name}")
            return True
        except Exception as e:
            logger.error(f"AI 요청 큐 추가 실패: {e}")
            return False
    
    def get_result(self, timeout: float = 0) -> Optional[Dict]:
        """
        AI 분석 결과 가져오기 (논블로킹)
        
        결과 큐에서 가장 오래된 결과를 가져옵니다.
        큐가 비어있으면 None을 반환합니다.
        
        Args:
            timeout: 대기 시간 (초). 0이면 즉시 반환.
        
        Returns:
            결과 딕셔너리 또는 None
            {
                'stock_code': str,
                'stock_name': str,
                'decision': str,  # BUY / HOLD / SELL
                'confidence': float,  # 0.0 ~ 1.0
                'reason': str,
                'original_price': float,
                'elapsed': float,
            }
        """
        try:
            if timeout > 0:
                return self.result_queue.get(timeout=timeout)
            else:
                return self.result_queue.get_nowait()
        except Empty:
            return None
    
    def get_all_results(self) -> list:
        """
        모든 대기 중인 결과 가져오기
        
        Returns:
            결과 딕셔너리 리스트
        """
        results = []
        while True:
            result = self.get_result()
            if result is None:
                break
            results.append(result)
        return results
    
    def clear_queues(self):
        """
        요청/결과 큐 비우기
        
        비상 모드 진입 시 호출합니다.
        """
        cleared_requests = 0
        cleared_results = 0
        
        # 요청 큐 비우기
        while not self.request_queue.empty():
            try:
                self.request_queue.get_nowait()
                cleared_requests += 1
            except Empty:
                break
        
        # 결과 큐 비우기
        while not self.result_queue.empty():
            try:
                self.result_queue.get_nowait()
                cleared_results += 1
            except Empty:
                break
        
        logger.info(f"AI 큐 비움 (요청: {cleared_requests}, 결과: {cleared_results})")
    
    # =========================================================================
    # 워커 루프 (내부)
    # =========================================================================
    
    def _worker_loop(self):
        """
        AI 워커 메인 루프
        
        별도 스레드에서 실행되며, request_queue에서 요청을 꺼내
        Qwen3 API를 호출하고 결과를 result_queue에 넣습니다.
        """
        logger.info("AI 워커 루프 시작")
        
        while self._running:
            try:
                # 요청 큐에서 가져오기 (1초 타임아웃)
                try:
                    request = self.request_queue.get(timeout=1)
                except Empty:
                    continue
                
                # 요청이 너무 오래됐으면 스킵 (30초 이상)
                age = time.time() - request.get('timestamp', 0)
                if age > 30:
                    logger.warning(f"오래된 AI 요청 스킵: {request['stock_code']} ({age:.1f}초 경과)")
                    continue
                
                # AI 분석 실행
                result = self._process_request(request)
                
                # 결과 큐에 넣기
                if result:
                    self.result_queue.put(result)
                
            except Exception as e:
                logger.exception(f"AI 워커 루프 에러: {e}")
                self._stats['error_count'] += 1
        
        logger.info("AI 워커 루프 종료")
    
    def _process_request(self, request: Dict) -> Optional[Dict]:
        """
        단일 AI 요청 처리
        
        Args:
            request: 요청 딕셔너리
        
        Returns:
            결과 딕셔너리 또는 None (실패 시)
        """
        stock_code = request['stock_code']
        stock_name = request['stock_name']
        
        logger.debug(f"AI 분석 시작: {stock_code} {stock_name}")
        
        start_time = time.time()
        
        try:
            # 프롬프트 생성
            prompt = self._build_prompt(request)
            
            # 🆕 API 호출 (provider에 따라 분기)
            response_text = self._call_api_with_retry(prompt)
            
            # 🆕 원본 응답 로깅 (디버깅용)
            logger.debug(f"AI 원본 응답 ({stock_code}): {response_text[:500]}...")
            
            # 응답 파싱
            parsed = self._parse_response(response_text)
            
            # 🆕 파싱 결과 로깅
            logger.debug(f"AI 파싱 결과 ({stock_code}): {parsed}")
            
            elapsed = time.time() - start_time
            
            # 통계 업데이트
            self._stats['success_count'] += 1
            self._update_avg_response_time(elapsed)
            
            result = {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'decision': parsed['decision'],
                'confidence': parsed['confidence'],
                'reason': parsed['reason'],
                'original_price': request['current_price'],
                'rule_score': request.get('rule_score', 0),  # 🆕 점수 추가
                'indicators': request.get('indicators', {}),  # 🆕 지표 추가 (CCI 포함)
                'elapsed': elapsed,
                'timestamp': time.time(),
            }
            
            logger.info(
                f"AI 분석 완료: {stock_code} → {parsed['decision']} "
                f"(신뢰도: {parsed['confidence']:.2f}, {elapsed:.1f}초)"
            )
            
            return result
            
        except TimeoutError:
            self._stats['timeout_count'] += 1
            logger.warning(f"AI 분석 타임아웃: {stock_code} ({self.timeout}초 초과)")
            return None
            
        except Exception as e:
            self._stats['error_count'] += 1
            logger.error(f"AI 분석 에러: {stock_code} - {e}")
            return None
    
    # =========================================================================
    # 프롬프트 생성
    # =========================================================================
    
    def _build_prompt(self, request: Dict) -> str:
        """
        AI 프롬프트 생성
        
        영어로 작성하여 모델 성능 최적화.
        JSON 출력을 강제하여 파싱 안정성 확보.
        🆕 학습 데이터 패턴 통계 포함
        
        Args:
            request: 요청 딕셔너리
        
        Returns:
            프롬프트 문자열
        """
        indicators = request.get('indicators', {})
        market_state = request.get('market_state', {})
        rule_score = request.get('rule_score', 0)
        stock_code = request.get('stock_code', '')
        
        # 시장 상태 해석
        market_mode = market_state.get('mode', 'NORMAL')
        market_change = market_state.get('change', 0)
        above_ma20 = market_state.get('above_ma20', True)
        market_status = "BULLISH" if above_ma20 else "BEARISH"
        
        # 지표값 추출
        cci = indicators.get('cci', 0)
        change_pct = indicators.get('change_pct', 0)
        distance_ma20 = indicators.get('distance_ma20', 0)
        volume_ratio = indicators.get('volume_ratio', 1.0)
        consec_bullish = indicators.get('consec_bullish', 0)
        candle_score = indicators.get('candle_score', 0)
        
        # 🆕 학습 데이터에서 패턴별 통계 가져오기
        try:
            stats = self.learning_store.get_stats()
            winrate = stats.get('winrate', 50)
            total_trades = stats.get('total_trades', 0)
            
            # 패턴별 통계
            pattern_stats = self.learning_store.get_pattern_stats()
            
            # CCI 구간 판단 및 해당 구간 승률
            if cci < -100:
                cci_zone = 'oversold'
            elif cci > 100:
                cci_zone = 'overbought'
            else:
                cci_zone = 'neutral'
            cci_zone_stats = pattern_stats.get('cci_stats', {}).get(cci_zone, {})
            cci_winrate = cci_zone_stats.get('winrate', 50)
            cci_trades = cci_zone_stats.get('total', 0)
            
            # 점수 구간 판단 및 해당 구간 승률
            if rule_score >= 80:
                score_zone = 'high'
            elif rule_score >= 70:
                score_zone = 'medium'
            else:
                score_zone = 'low'
            score_zone_stats = pattern_stats.get('score_stats', {}).get(score_zone, {})
            score_winrate = score_zone_stats.get('winrate', 50)
            score_trades = score_zone_stats.get('total', 0)
            
            # 종목별 통계
            stock_stats = self.learning_store.get_stock_stats(stock_code)
            stock_winrate = stock_stats.get('winrate', 50)
            stock_trades = stock_stats.get('total_trades', 0)
            
        except Exception as e:
            logger.debug(f"학습 데이터 로드 실패: {e}")
            winrate = 50
            total_trades = 0
            cci_zone = 'neutral'
            cci_winrate = 50
            cci_trades = 0
            score_zone = 'medium'
            score_winrate = 50
            score_trades = 0
            stock_winrate = 50
            stock_trades = 0
        
        # 🆕 패턴 기반 경고 메시지 생성
        warnings = []
        if cci_trades >= 5 and cci_winrate < 40:
            warnings.append(f"⚠️ CCI {cci_zone} zone has {cci_winrate:.0f}% win rate")
        if score_trades >= 5 and score_winrate < 40:
            warnings.append(f"⚠️ Score {score_zone} zone has {score_winrate:.0f}% win rate")
        if stock_trades >= 3 and stock_winrate < 40:
            warnings.append(f"⚠️ This stock has {stock_winrate:.0f}% win rate")
        warning_text = "\n".join(warnings) if warnings else "No pattern warnings"
        
        # 프롬프트 구성 (영어, JSON 강제)
        # 🆕 Gemini용으로 /no_think 제거 (Ollama 전용 지시어)
        prompt = f"""You are a conservative Korean stock scalping AI. Analyze indicators and decide BUY or HOLD.

[MARKET]
- KOSPI: {market_change:+.2f}% | Mode: {market_mode} | Trend: {market_status}

[STOCK]
- Score: {rule_score:.1f}/100
- CCI(14): {cci:.1f}
- Change: {change_pct:+.2f}%
- MA20 Distance: {distance_ma20:+.2f}%
- Volume: {volume_ratio:.2f}x
- Bullish Days: {consec_bullish}

[LEARNING DATA - YOUR PAST PERFORMANCE]
- Overall: {winrate:.1f}% win rate ({total_trades} trades)
- CCI {cci_zone} zone: {cci_winrate:.1f}% win rate ({cci_trades} trades)
- Score {score_zone} zone: {score_winrate:.1f}% win rate ({score_trades} trades)
- This stock: {stock_winrate:.1f}% win rate ({stock_trades} trades)

[PATTERN WARNINGS]
{warning_text}

[RULES - BE CONSERVATIVE]
**MUST HOLD if ANY of these:**
- CCI > 200 (overbought, likely to drop)
- CCI < -100 (oversold, wait for reversal)
- Volume < 0.7x (low interest)
- Change > +5% (already pumped today)
- Market mode is EMERGENCY or CONSERVATIVE
- Pattern win rate < 40%

**BUY conditions (ALL must be true):**
- Score >= 75: confidence 0.80-0.85
- Score 70-74: confidence 0.70-0.75
- Score 65-69: confidence 0.60-0.65 (only if Volume > 1.0x AND CCI 0~150)

**Default to HOLD when uncertain.** Missing a trade is better than losing.

Output ONLY valid JSON:
{{"decision": "BUY", "confidence": 0.75, "reason": "brief"}} or {{"decision": "HOLD", "confidence": 0.5, "reason": "brief"}}

JSON:"""
        
        return prompt
    
    # =========================================================================
    # API 호출 (Provider별 분기)
    # =========================================================================
    
    def _call_api_with_retry(self, prompt: str) -> str:
        """
        AI API 호출 (재시도 포함)
        
        Args:
            prompt: 프롬프트 문자열
        
        Returns:
            응답 텍스트
        
        Raises:
            TimeoutError: 타임아웃 발생
            Exception: API 호출 실패
        """
        last_error = None
        provider_name = "Gemini" if self.provider == 'gemini' else "Ollama"
        
        for attempt in range(self.retry_count + 1):
            try:
                if self.provider == 'gemini':
                    return self._call_gemini(prompt)
                else:
                    return self._call_ollama(prompt)
            except requests.Timeout:
                last_error = TimeoutError(f"API 타임아웃 ({self.timeout}초)")
                logger.warning(f"{provider_name} 타임아웃 (시도 {attempt + 1}/{self.retry_count + 1})")
            except Exception as e:
                last_error = e
                logger.warning(f"{provider_name} API 에러 (시도 {attempt + 1}): {e}")
            
            # 재시도 전 잠시 대기
            if attempt < self.retry_count:
                time.sleep(0.5)
        
        raise last_error
    
    def _call_gemini(self, prompt: str) -> str:
        """
        Gemini API 호출
        
        Args:
            prompt: 프롬프트 문자열
        
        Returns:
            응답 텍스트
        """
        url = f"{self.api_url}?key={self.api_key}"
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 200,
                "topP": 0.9,
            },
            # 🆕 안전 설정 (BLOCK_NONE으로 설정하여 금융 관련 내용 허용)
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
        }
        
        response = requests.post(
            url,
            json=payload,
            timeout=self.timeout,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            data = response.json()
            # Gemini 응답 구조: candidates[0].content.parts[0].text
            try:
                raw_response = data['candidates'][0]['content']['parts'][0]['text']
                logger.info(f"AI 원본 응답: {raw_response[:200]}...")
                return raw_response
            except (KeyError, IndexError) as e:
                logger.error(f"Gemini 응답 파싱 에러: {data}")
                raise Exception(f"Gemini 응답 파싱 실패: {e}")
        else:
            error_msg = response.text[:200] if response.text else str(response.status_code)
            raise Exception(f"Gemini API 에러: {response.status_code} - {error_msg}")
    
    def _call_ollama(self, prompt: str) -> str:
        """
        Ollama API 호출 (Qwen3 등 로컬 모델)
        
        Args:
            prompt: 프롬프트 문자열
        
        Returns:
            응답 텍스트
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,     # 낮은 temperature로 일관된 응답
                "num_predict": 150,     # 최대 토큰 수 제한
                "top_p": 0.9,
            },
            "think": False,  # Qwen3 thinking 비활성화
        }
        
        response = requests.post(
            self.api_url,
            json=payload,
            timeout=self.timeout
        )
        
        if response.status_code == 200:
            data = response.json()
            raw_response = data.get('response', '')
            logger.info(f"AI 원본 응답: {raw_response[:200]}...")
            return raw_response
        else:
            raise Exception(f"Ollama API 응답 에러: {response.status_code}")
    
    # 🆕 기존 함수 호환성 유지
    def _call_qwen3_with_retry(self, prompt: str) -> str:
        """기존 코드 호환용 - _call_api_with_retry로 대체됨"""
        return self._call_api_with_retry(prompt)
    
    def _call_qwen3(self, prompt: str) -> str:
        """기존 코드 호환용 - _call_ollama로 대체됨"""
        return self._call_ollama(prompt)
    
    # =========================================================================
    # 응답 파싱 (강화된 버전)
    # =========================================================================
    
    def _parse_response(self, text: str) -> Dict:
        """
        AI 응답 파싱 (강화된 버전)
        
        Qwen3 모델의 다양한 응답 형식을 처리합니다:
        1. <think>...</think> 태그 제거
        2. 다양한 JSON 패턴 매칭
        3. 키 대소문자 정규화
        4. Fallback: 텍스트에서 직접 추출
        
        Args:
            text: AI 응답 텍스트
        
        Returns:
            파싱된 결과 딕셔너리
            {
                'decision': 'BUY' | 'HOLD' | 'SELL',
                'confidence': float (0.0 ~ 1.0),
                'reason': str
            }
        """
        if not text:
            return self._default_response("빈 응답")
        
        original_text = text  # 디버깅용
        
        # Step 1: <think>...</think> 태그 제거 (Qwen3 특성)
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Step 1.5: "Thinking..." ~ "...done thinking." 텍스트 제거 (Qwen3 CLI 출력)
        text = re.sub(r'Thinking\.\.\..*?\.\.\.done thinking\.', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'Thinking\.\.\..*$', '', text, flags=re.DOTALL)  # done thinking 없는 경우
        
        # Step 2: 줄바꿈/탭/공백 정리
        text = re.sub(r'[\n\r\t]+', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        # Step 3: JSON 추출 시도 (여러 패턴)
        json_patterns = [
            # 완전한 형식
            r'\{[^{}]*"decision"\s*:\s*"[^"]+"\s*,\s*"confidence"\s*:\s*[\d.]+\s*,\s*"reason"\s*:\s*"[^"]*"\s*\}',
            # decision과 confidence만 있는 경우
            r'\{[^{}]*"decision"\s*:\s*"[^"]+"\s*,\s*"confidence"\s*:\s*[\d.]+[^{}]*\}',
            # 순서가 다른 경우
            r'\{[^{}]*"confidence"[^{}]*"decision"[^{}]*\}',
            # 최소한의 JSON
            r'\{[^{}]+\}',
        ]
        
        for pattern in json_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result = self._try_parse_json(match.group())
                if result:
                    return result
        
        # Step 4: Fallback - 텍스트에서 직접 추출
        return self._extract_from_text(text, original_text)
    
    def _try_parse_json(self, json_str: str) -> Optional[Dict]:
        """
        JSON 문자열 파싱 시도
        
        Args:
            json_str: JSON 문자열
        
        Returns:
            파싱 성공 시 딕셔너리, 실패 시 None
        """
        try:
            # 키 대소문자 정규화
            normalized = json_str
            normalized = re.sub(r'"(Decision|DECISION)"', '"decision"', normalized)
            normalized = re.sub(r'"(Confidence|CONFIDENCE)"', '"confidence"', normalized)
            normalized = re.sub(r'"(Reason|REASON)"', '"reason"', normalized)
            
            # JSON 파싱
            parsed = json.loads(normalized)
            
            # 값 검증 및 정규화
            decision = str(parsed.get('decision', 'HOLD')).upper().strip()
            if decision not in ['BUY', 'HOLD', 'SELL']:
                decision = 'HOLD'
            
            confidence = float(parsed.get('confidence', 0.5))
            confidence = max(0.0, min(1.0, confidence))  # 0~1 범위 제한
            
            reason = str(parsed.get('reason', ''))[:100]  # 100자 제한
            
            return {
                'decision': decision,
                'confidence': confidence,
                'reason': reason,
            }
            
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug(f"JSON 파싱 실패: {e}, 원본: {json_str[:100]}")
            return None
    
    def _extract_from_text(self, text: str, original: str = "") -> Dict:
        """
        텍스트에서 직접 결정/신뢰도 추출 (Fallback)
        
        JSON 파싱이 실패했을 때 텍스트에서 BUY/HOLD/SELL과
        신뢰도를 직접 추출합니다.
        
        Args:
            text: 정리된 텍스트
            original: 원본 텍스트 (로깅용)
        
        Returns:
            추출된 결과 딕셔너리
        """
        # 🆕 Fallback 진입 시 경고 로그
        logger.warning(f"AI JSON 파싱 실패, Fallback 사용. 원본: {original[:200]}...")
        
        text_upper = text.upper()
        
        # 결정 추출
        decision = 'HOLD'
        if 'BUY' in text_upper:
            decision = 'BUY'
        elif 'SELL' in text_upper:
            decision = 'SELL'
        
        # 신뢰도 추출
        confidence = 0.5
        conf_patterns = [
            r'confidence["\s:]+([0-9.]+)',
            r'([0-9]\.[0-9]+)',  # 소수점 숫자
        ]
        
        for pattern in conf_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    conf_value = float(match.group(1))
                    if 0 <= conf_value <= 1:
                        confidence = conf_value
                        break
                except ValueError:
                    continue
        
        # 로깅
        logger.debug(f"Fallback 파싱: {decision}, {confidence:.2f}")
        if original:
            logger.debug(f"원본 응답 (처음 200자): {original[:200]}")
        
        return {
            'decision': decision,
            'confidence': confidence,
            'reason': 'fallback parsing',
        }
    
    def _default_response(self, reason: str = "") -> Dict:
        """기본 응답 (파싱 실패 시)"""
        return {
            'decision': 'HOLD',
            'confidence': 0.5,
            'reason': reason or 'default response',
        }
    
    # =========================================================================
    # 누적 학습 기록
    # =========================================================================
    
    def record_result(
        self,
        stock_code: str,
        decision: str,
        confidence: float,
        actual_profit: float,
    ):
        """
        매매 결과 기록 (누적 학습용)
        
        실제 매매 결과를 기록하여 이후 분석에 활용합니다.
        
        Args:
            stock_code: 종목 코드
            decision: AI 결정 (BUY/HOLD/SELL)
            confidence: AI 신뢰도
            actual_profit: 실제 수익률 (%)
        """
        try:
            self.learning_store.add_result(
                stock_code=stock_code,
                decision=decision,
                confidence=confidence,
                profit=actual_profit,
                win=actual_profit > 0,
            )
            logger.debug(f"매매 결과 기록: {stock_code}, 수익률: {actual_profit:+.2f}%")
        except Exception as e:
            logger.error(f"매매 결과 기록 실패: {e}")
    
    # =========================================================================
    # 통계 및 유틸리티
    # =========================================================================
    
    def _update_avg_response_time(self, elapsed: float):
        """평균 응답 시간 업데이트"""
        total = self._stats['success_count']
        current_avg = self._stats['avg_response_time']
        
        # 이동 평균 계산
        if total == 1:
            self._stats['avg_response_time'] = elapsed
        else:
            self._stats['avg_response_time'] = (current_avg * (total - 1) + elapsed) / total
    
    def generate(self, prompt: str, max_tokens: int = 1000, json_mode: bool = False) -> str:
        """
        프롬프트를 직접 호출하고 응답 텍스트를 반환 (동기 방식)
        
        프리마켓 분석 등 단발성 호출에 사용합니다.
        
        Args:
            prompt: 프롬프트 문자열
            max_tokens: 최대 토큰 수
            json_mode: True면 JSON 형식으로만 응답 (Gemini만 지원)
        
        Returns:
            AI 응답 텍스트
        """
        try:
            if self.provider == 'gemini':
                # Gemini 직접 호출 (max_tokens 적용)
                url = f"{self.api_url}?key={self.api_key}"
                
                generation_config = {
                    "temperature": 0.3,
                    "maxOutputTokens": max_tokens,
                    "topP": 0.9,
                }
                
                # JSON 모드 활성화 시 응답 형식 강제
                if json_mode:
                    generation_config["responseMimeType"] = "application/json"
                
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": generation_config,
                    "safetySettings": [
                        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                    ]
                }
                
                response = requests.post(
                    url,
                    json=payload,
                    timeout=self.timeout,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # 응답 완료 이유 확인
                    candidate = data['candidates'][0]
                    finish_reason = candidate.get('finishReason', 'UNKNOWN')
                    
                    if finish_reason == 'MAX_TOKENS':
                        logger.warning(f"⚠️ Gemini 응답이 max_tokens({max_tokens})에서 잘림!")
                    elif finish_reason == 'SAFETY':
                        logger.warning("⚠️ Gemini 응답이 안전 필터에 의해 차단됨")
                    elif finish_reason not in ('STOP', 'END_TURN'):
                        logger.warning(f"⚠️ Gemini 응답 종료 이유: {finish_reason}")
                    
                    text = candidate['content']['parts'][0]['text']
                    logger.debug(f"Gemini 응답 (finishReason={finish_reason}): {text[:200]}...")
                    return text
                else:
                    error_detail = response.text[:500] if response.text else "No detail"
                    raise Exception(f"Gemini API 에러: {response.status_code} - {error_detail}")
            else:
                # Ollama 호출
                return self._call_ollama(prompt)
                
        except Exception as e:
            logger.error(f"generate() 실패: {e}")
            raise
    
    async def generate_async(self, prompt: str, max_tokens: int = 1000, json_mode: bool = False) -> str:
        """
        비동기 버전의 generate (async/await 지원)
        
        Args:
            prompt: 프롬프트 문자열
            max_tokens: 최대 토큰 수
            json_mode: True면 JSON 형식으로만 응답
        
        Returns:
            AI 응답 텍스트
        """
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.generate, prompt, max_tokens, json_mode)
    
    def get_stats(self) -> Dict:
        """AI 엔진 통계 조회"""
        return {
            **self._stats,
            'queue_size': self.request_queue.qsize(),
            'result_queue_size': self.result_queue.qsize(),
            'is_running': self._running,
        }
    
    def get_queue_size(self) -> int:
        """현재 대기 중인 요청 수"""
        return self.request_queue.qsize()
    
    def health_check(self) -> bool:
        """
        AI 엔진 상태 확인
        
        Ollama API가 정상적으로 응답하는지 확인합니다.
        
        Returns:
            True: 정상
            False: 비정상
        """
        try:
            response = requests.get(
                self.api_url.replace('/api/generate', '/api/tags'),
                timeout=5
            )
            return response.status_code == 200
        except Exception:
            return False


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == '__main__':
    # 로깅 설정
    logging.basicConfig(
        level=logging.DEBUG,
        format='[%(asctime)s] %(levelname)s - %(message)s'
    )
    
    # 테스트 설정
    test_config = {
        'api_url': 'http://localhost:11434/api/generate',
        'model': 'qwen3:8b',
        'timeout': 10,
        'max_queue_size': 50,
        'retry_count': 2,
    }
    
    print("=" * 60)
    print("AI Engine 테스트")
    print("=" * 60)
    
    # AI 엔진 생성
    engine = AIEngine(test_config)
    
    # 헬스 체크
    print("\n1. 헬스 체크...")
    if engine.health_check():
        print("   ✅ Ollama API 정상")
    else:
        print("   ❌ Ollama API 연결 실패 (서버가 실행 중인지 확인하세요)")
        exit(1)
    
    # 워커 시작
    print("\n2. 워커 시작...")
    engine.start()
    time.sleep(1)
    
    # 분석 요청
    print("\n3. 분석 요청 (삼성전자)...")
    engine.request_analysis(
        stock_code="005930",
        stock_name="삼성전자",
        indicators={
            'cci': -50,
            'change_pct': 1.5,
            'distance_ma20': 2.0,
            'volume_ratio': 1.3,
            'consec_bullish': 2,
            'candle_score': 12,
        },
        rule_score=78,
        market_state={
            'mode': 'NORMAL',
            'change': 0.5,
            'above_ma20': True,
        },
        current_price=72000,
    )
    
    # 결과 대기
    print("\n4. 결과 대기 (최대 15초)...")
    result = None
    for i in range(15):
        result = engine.get_result()
        if result:
            break
        time.sleep(1)
        print(f"   대기 중... {i + 1}초")
    
    # 결과 출력
    if result:
        print("\n5. 분석 결과:")
        print(f"   종목: {result['stock_code']} {result['stock_name']}")
        print(f"   결정: {result['decision']}")
        print(f"   신뢰도: {result['confidence']:.2f}")
        print(f"   이유: {result['reason']}")
        print(f"   소요 시간: {result['elapsed']:.2f}초")
    else:
        print("\n5. ❌ 결과를 받지 못했습니다.")
    
    # 통계 출력
    print("\n6. 통계:")
    stats = engine.get_stats()
    for key, value in stats.items():
        print(f"   {key}: {value}")
    
    # 워커 중지
    print("\n7. 워커 중지...")
    engine.stop()
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)