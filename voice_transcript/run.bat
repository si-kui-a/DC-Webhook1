@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" run_session.py
echo.
echo (錄音已結束，視窗保持開啟方便查看結果，關閉請按任意鍵)
pause >nul
