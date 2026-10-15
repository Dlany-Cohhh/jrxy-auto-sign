@echo off
rem ============================================================
rem  Today Campus Auto Sign - Windows one-click deploy
rem  Installs engine venv + web venv, then prints start command.
rem ============================================================
setlocal
set ROOT=%~dp0..
echo [1/4] Creating engine virtual env (jrxy-engine\.venv)...
if not exist "%ROOT%\jrxy-engine\.venv" (
  python -m venv "%ROOT%\jrxy-engine\.venv" || goto :fail
)
echo [2/4] Installing engine dependencies (large, incl. OCR libs, a few minutes)...
"%ROOT%\jrxy-engine\.venv\Scripts\python.exe" -m pip install -q --upgrade pip
"%ROOT%\jrxy-engine\.venv\Scripts\python.exe" -m pip install -q -r "%ROOT%\jrxy-engine\requirements.txt" || goto :fail
echo [3/4] Creating web venv (web-server\.venv)...
if not exist "%ROOT%\web-server\.venv" (
  python -m venv "%ROOT%\web-server\.venv" || goto :fail
)
"%ROOT%\web-server\.venv\Scripts\python.exe" -m pip install -q --upgrade pip
"%ROOT%\web-server\.venv\Scripts\python.exe" -m pip install -q -r "%ROOT%\web-server\requirements.txt" || goto :fail
echo [4/4] Done.
echo.
echo Start the web service:
echo   cd /d "%ROOT%\web-server"
echo   .venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
echo.
echo Open http://127.0.0.1:8000 in browser.
echo For background/no-console running, see deploy\start_windows.bat (NSSM or Task Scheduler).
pause
exit /b 0
:fail
echo.
echo [ERROR] Install failed. Check Python 3.10+ is installed and on PATH.
pause
exit /b 1
