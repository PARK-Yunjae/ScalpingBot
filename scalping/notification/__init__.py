#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================================
ScalpingBot v2.4 - Notification 모듈
============================================================================
알림 및 리포트 전송

모듈:
- discord_bot: Discord 웹훅 알림
============================================================================
"""

from scalping.notification.discord_bot import DiscordNotifier

__all__ = [
    'DiscordNotifier',
]
