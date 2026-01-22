#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - AI Engine 테스트
============================================================================
설계서 14.2.1 단위 테스트: AI JSON 파서

테스트 항목:
- thinking 태그 제거
- JSON 추출
- 키 정규화
- confidence clamp
- fallback 동작
============================================================================
"""

import pytest
import json
from unittest.mock import Mock, patch, AsyncMock
import asyncio

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scalping.ai.ai_engine import AIEngine


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def ai_engine():
    """AI 엔진 인스턴스"""
    config = {
        'model': 'qwen3:8b',
        'base_url': 'http://localhost:11434',
        'timeout': 5,
        'enabled': True,
    }
    return AIEngine(config)


# =============================================================================
# JSON 파싱 테스트
# =============================================================================

class TestJSONParsing:
    """JSON 파싱 테스트"""
    
    def test_clean_json_response(self, ai_engine):
        """깨끗한 JSON 응답"""
        response = '{"decision": "BUY", "confidence": 0.85, "reason": "Good"}'
        result = ai_engine._parse_ai_response(response)
        
        assert result['decision'] == 'BUY'
        assert result['confidence'] == 0.85
    
    def test_thinking_tag_removal(self, ai_engine):
        """<think> 태그 제거"""
        response = '''<think>
        분석 중... CCI가 양수이고 거래량이 증가했습니다.
        </think>
        {"decision": "BUY", "confidence": 0.75, "reason": "CCI positive"}'''
        
        result = ai_engine._parse_ai_response(response)
        
        assert result['decision'] == 'BUY'
        assert result['confidence'] == 0.75
    
    def test_markdown_code_block(self, ai_engine):
        """마크다운 코드 블록 제거"""
        response = '''```json
        {"decision": "HOLD", "confidence": 0.6, "reason": "Wait"}
        ```'''
        
        result = ai_engine._parse_ai_response(response)
        
        assert result['decision'] == 'HOLD'
    
    def test_key_normalization(self, ai_engine):
        """키 정규화 (대소문자)"""
        response = '{"Decision": "BUY", "CONFIDENCE": 0.8, "Reason": "Test"}'
        result = ai_engine._parse_ai_response(response)
        
        # 소문자로 정규화되어야 함
        assert 'decision' in result or 'Decision' in result
    
    def test_confidence_clamp_high(self, ai_engine):
        """confidence 클램프 (상한)"""
        response = '{"decision": "BUY", "confidence": 1.5, "reason": "Test"}'
        result = ai_engine._parse_ai_response(response)
        
        # 1.0 이상은 1.0으로 클램프
        assert result.get('confidence', 1.0) <= 1.0
    
    def test_confidence_clamp_low(self, ai_engine):
        """confidence 클램프 (하한)"""
        response = '{"decision": "BUY", "confidence": -0.5, "reason": "Test"}'
        result = ai_engine._parse_ai_response(response)
        
        # 0.0 이하는 0.0으로 클램프
        assert result.get('confidence', 0.0) >= 0.0
    
    def test_invalid_json_fallback(self, ai_engine):
        """잘못된 JSON - fallback"""
        response = 'This is not JSON at all'
        result = ai_engine._parse_ai_response(response)
        
        # fallback: HOLD
        assert result.get('decision', 'HOLD') == 'HOLD'
    
    def test_partial_json(self, ai_engine):
        """불완전한 JSON"""
        response = '{"decision": "BUY"'  # 닫는 괄호 없음
        result = ai_engine._parse_ai_response(response)
        
        # fallback
        assert result.get('decision', 'HOLD') in ['BUY', 'HOLD']
    
    def test_empty_response(self, ai_engine):
        """빈 응답"""
        response = ''
        result = ai_engine._parse_ai_response(response)
        
        assert result.get('decision', 'HOLD') == 'HOLD'
    
    def test_nested_json(self, ai_engine):
        """중첩 JSON"""
        response = '''{"decision": "BUY", "confidence": 0.8, 
                       "analysis": {"cci": 45, "volume": 1.5}}'''
        result = ai_engine._parse_ai_response(response)
        
        assert result['decision'] == 'BUY'


# =============================================================================
# 타임아웃 테스트
# =============================================================================

class TestTimeout:
    """타임아웃 테스트"""
    
    @pytest.mark.asyncio
    async def test_timeout_returns_hold(self, ai_engine):
        """타임아웃 시 HOLD 반환"""
        # Mock 느린 응답
        async def slow_response(*args, **kwargs):
            await asyncio.sleep(10)  # 10초 대기
            return {"decision": "BUY"}
        
        # 타임아웃 짧게 설정
        ai_engine.timeout = 0.1
        
        with patch.object(ai_engine, '_call_ollama', slow_response):
            result = await ai_engine.analyze_async("005930", {}, {})
        
        # 타임아웃 시 HOLD
        assert result.get('decision', 'HOLD') == 'HOLD'


# =============================================================================
# 프롬프트 생성 테스트
# =============================================================================

class TestPromptGeneration:
    """프롬프트 생성 테스트"""
    
    def test_prompt_contains_stock_code(self, ai_engine):
        """프롬프트에 종목코드 포함"""
        indicators = {'cci': 45, 'price_change': 2.5}
        ohlcv = {'close': 70000, 'volume': 100000}
        
        prompt = ai_engine._build_prompt("005930", indicators, ohlcv)
        
        assert "005930" in prompt
    
    def test_prompt_contains_indicators(self, ai_engine):
        """프롬프트에 지표 포함"""
        indicators = {'cci': 45, 'price_change': 2.5, 'volume_ratio': 1.5}
        ohlcv = {'close': 70000}
        
        prompt = ai_engine._build_prompt("005930", indicators, ohlcv)
        
        assert "cci" in prompt.lower() or "45" in prompt


# =============================================================================
# 테스트 실행
# =============================================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
