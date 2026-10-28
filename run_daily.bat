@echo off
REM Daily scan for Windows Task Scheduler. Sends the digest and alerts to Telegram.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python scan.py >> scan_log.txt 2>&1
