#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v3.2 - Funnel Log (퍼널 로그)
============================================================================
"왜 0매매인지" 단계별 추적

퍼널 단계:
1. 유니버스 (TV100)
2. Hard Firewall (거래량, 당일급등)
3. 전략 점수
4. Soft Scoring (CCI, RSI, VWAP, EMA 감점/가점)
5. min_score 통과
6. 매수 실행

각 단계에서 탈락한 종목 수와 이유를 기록
============================================================================
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger('ScalpingBot.FunnelLog')


@dataclass
class CandidateInfo:
    """후보 종목 정보"""
    stock_code: str
    stock_name: str
    score: float
    breakdown: Dict[str, float] = field(default_factory=dict)
    action: str = "HOLD"  # BUY / HOLD / SKIP
    skip_reason: str = ""
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.stock_code,
            'name': self.stock_name,
            'score': self.score,
            'action': self.action,
            'skip_reason': self.skip_reason,
            'breakdown': self.breakdown,
            'warnings': self.warnings,
        }


@dataclass
class FunnelStats:
    """퍼널 통계"""
    cycle_count: int = 0
    universe_count: int = 0
    hard_firewall_passed: int = 0
    hard_firewall_failed: int = 0
    score_above_min: int = 0
    score_below_min: int = 0
    buy_signals: int = 0
    
    # 탈락 사유별 카운트
    skip_reasons: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    
    def reset(self):
        """통계 리셋"""
        self.cycle_count = 0
        self.universe_count = 0
        self.hard_firewall_passed = 0
        self.hard_firewall_failed = 0
        self.score_above_min = 0
        self.score_below_min = 0
        self.buy_signals = 0
        self.skip_reasons = defaultdict(int)


class FunnelLog:
    """
    퍼널 로그 관리자
    
    매 사이클마다 종목별 탈락 이유를 추적하고,
    상위 N개 후보를 항상 표시
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Args:
            config: funnel_log 설정
        """
        self.config = config or {}
        self.enabled = self.config.get('enabled', True)
        self.log_interval_minutes = self.config.get('log_interval_minutes', 10)
        self.show_top_candidates = self.config.get('show_top_candidates', 5)
        self.discord_notify = self.config.get('discord_notify', True)
        
        # 현재 사이클 데이터
        self._current_cycle_candidates: List[CandidateInfo] = []
        
        # 누적 통계
        self._stats = FunnelStats()
        self._last_summary_time = datetime.now()
        
        # 최근 후보 히스토리 (디버깅용)
        self._recent_top_candidates: List[List[CandidateInfo]] = []
        self._max_history = 10
        
        logger.info(f"FunnelLog 초기화 (enabled={self.enabled}, "
                   f"interval={self.log_interval_minutes}분)")
    
    def start_cycle(self, universe_count: int = 0):
        """새 사이클 시작"""
        self._current_cycle_candidates = []
        self._stats.cycle_count += 1
        self._stats.universe_count = universe_count
    
    def record_candidate(
        self,
        stock_code: str,
        stock_name: str,
        score: float,
        action: str,
        breakdown: Dict[str, float] = None,
        skip_reason: str = "",
        warnings: List[str] = None,
    ):
        """후보 기록"""
        if not self.enabled:
            return
        
        candidate = CandidateInfo(
            stock_code=stock_code,
            stock_name=stock_name,
            score=score,
            breakdown=breakdown or {},
            action=action,
            skip_reason=skip_reason,
            warnings=warnings or [],
        )
        self._current_cycle_candidates.append(candidate)
        
        # 통계 업데이트
        if action == "SKIP":
            self._stats.hard_firewall_failed += 1
            if skip_reason:
                # [HARD] 접두사 제거 후 카운트
                reason_key = skip_reason.replace("[HARD] ", "").split(" (")[0]
                self._stats.skip_reasons[reason_key] += 1
        else:
            self._stats.hard_firewall_passed += 1
            
            if action == "BUY":
                self._stats.score_above_min += 1
                self._stats.buy_signals += 1
            else:  # HOLD
                self._stats.score_below_min += 1
    
    def end_cycle(self) -> List[CandidateInfo]:
        """사이클 종료 및 상위 후보 반환"""
        if not self.enabled:
            return []
        
        # 점수 기준 정렬 (높은 순)
        sorted_candidates = sorted(
            self._current_cycle_candidates,
            key=lambda x: x.score,
            reverse=True
        )
        
        # 상위 N개 추출
        top_candidates = sorted_candidates[:self.show_top_candidates]
        
        # 히스토리 저장
        if top_candidates:
            self._recent_top_candidates.append(top_candidates)
            if len(self._recent_top_candidates) > self._max_history:
                self._recent_top_candidates.pop(0)
        
        return top_candidates
    
    def should_log_summary(self) -> bool:
        """요약 로그 출력 시점인지"""
        if not self.enabled:
            return False
        
        elapsed = (datetime.now() - self._last_summary_time).total_seconds() / 60
        return elapsed >= self.log_interval_minutes
    
    def get_summary(self) -> Dict[str, Any]:
        """퍼널 요약 반환"""
        stats = self._stats
        
        # 통과율 계산
        total_evaluated = stats.hard_firewall_passed + stats.hard_firewall_failed
        firewall_pass_rate = (stats.hard_firewall_passed / total_evaluated * 100
                             if total_evaluated > 0 else 0)
        
        total_scored = stats.score_above_min + stats.score_below_min
        min_score_pass_rate = (stats.score_above_min / total_scored * 100
                              if total_scored > 0 else 0)
        
        # 상위 탈락 사유
        top_skip_reasons = sorted(
            stats.skip_reasons.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        return {
            'cycle_count': stats.cycle_count,
            'universe_count': stats.universe_count,
            'hard_firewall': {
                'passed': stats.hard_firewall_passed,
                'failed': stats.hard_firewall_failed,
                'pass_rate': firewall_pass_rate,
            },
            'min_score': {
                'passed': stats.score_above_min,
                'failed': stats.score_below_min,
                'pass_rate': min_score_pass_rate,
            },
            'buy_signals': stats.buy_signals,
            'top_skip_reasons': top_skip_reasons,
        }
    
    def log_summary(self):
        """요약 로그 출력"""
        if not self.enabled:
            return
        
        summary = self.get_summary()
        
        logger.info("\n" + "="*60)
        logger.info(f"📊 퍼널 요약 (최근 {self.log_interval_minutes}분)")
        logger.info("="*60)
        logger.info(f"사이클: {summary['cycle_count']}회")
        logger.info(f"유니버스: {summary['universe_count']}종목")
        logger.info(f"")
        logger.info(f"[Hard Firewall]")
        logger.info(f"  통과: {summary['hard_firewall']['passed']}건 "
                   f"({summary['hard_firewall']['pass_rate']:.1f}%)")
        logger.info(f"  탈락: {summary['hard_firewall']['failed']}건")
        logger.info(f"")
        logger.info(f"[점수 필터]")
        logger.info(f"  min_score 이상: {summary['min_score']['passed']}건 "
                   f"({summary['min_score']['pass_rate']:.1f}%)")
        logger.info(f"  min_score 미만: {summary['min_score']['failed']}건")
        logger.info(f"")
        logger.info(f"[매수 시그널]")
        logger.info(f"  발생: {summary['buy_signals']}건")
        
        if summary['top_skip_reasons']:
            logger.info(f"")
            logger.info(f"[주요 탈락 사유]")
            for reason, count in summary['top_skip_reasons']:
                logger.info(f"  - {reason}: {count}건")
        
        logger.info("="*60 + "\n")
        
        self._last_summary_time = datetime.now()
    
    def log_top_candidates(self, candidates: List[CandidateInfo], min_score: int):
        """상위 후보 로그 출력"""
        if not candidates:
            return
        
        logger.info(f"\n📈 상위 {len(candidates)}개 후보 (min_score: {min_score})")
        logger.info("-" * 50)
        
        for i, c in enumerate(candidates, 1):
            status = "✅" if c.action == "BUY" else "⬜"
            logger.info(f"{i}. {status} {c.stock_name}({c.stock_code}) "
                       f"| {c.score:.0f}점 | {c.action}")
            
            if c.skip_reason:
                logger.info(f"   └─ 사유: {c.skip_reason}")
            elif c.action == "HOLD" and c.score > 0:
                logger.info(f"   └─ 점수 미달 ({c.score:.0f} < {min_score})")
            
            # 세부 점수 (상위 3개만)
            if c.breakdown:
                top_scores = sorted(
                    c.breakdown.items(),
                    key=lambda x: abs(x[1]),
                    reverse=True
                )[:3]
                scores_str = ", ".join(f"{k}:{v:+.0f}" for k, v in top_scores)
                logger.info(f"   └─ 주요: {scores_str}")
        
        logger.info("-" * 50)
    
    def reset_stats(self):
        """통계 리셋 (일일 리셋용)"""
        self._stats.reset()
        self._last_summary_time = datetime.now()
        logger.info("FunnelLog 통계 리셋")
    
    def get_discord_message(self) -> str:
        """Discord 알림용 메시지 생성"""
        summary = self.get_summary()
        
        msg = f"📊 **퍼널 요약** (최근 {self.log_interval_minutes}분)\n"
        msg += f"```\n"
        msg += f"사이클: {summary['cycle_count']}회\n"
        msg += f"Hard Firewall 통과율: {summary['hard_firewall']['pass_rate']:.1f}%\n"
        msg += f"min_score 통과율: {summary['min_score']['pass_rate']:.1f}%\n"
        msg += f"매수 시그널: {summary['buy_signals']}건\n"
        
        if summary['top_skip_reasons']:
            msg += f"\n주요 탈락 사유:\n"
            for reason, count in summary['top_skip_reasons'][:3]:
                msg += f"  - {reason}: {count}건\n"
        
        msg += f"```"
        
        return msg
