@echo off
chcp 65001 > nul

echo [%date% %time%] ScalpingBot 시작 >> C:\Coding\ScalpingBot\logs\startup.log

cd /d C:\Coding\ScalpingBot
call venv\Scripts\activate.bat

echo [%date% %time%] 가상환경 활성화 >> C:\Coding\ScalpingBot\logs\startup.log

python main.py --mode LIVE_DATA_ONLY --dry-run --debug

echo [%date% %time%] ScalpingBot 종료 >> C:\Coding\ScalpingBot\logs\startup.log