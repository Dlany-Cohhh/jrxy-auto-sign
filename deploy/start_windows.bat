@echo off
rem ============================================================
rem  Start web service (foreground console).
rem  For hidden background startup: register as Windows service with
rem  NSSM (nssm install jrxy-sign "%ROOT%\web-server\.venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000)
rem  or use Task Scheduler with "Run whether user is logged on or not".
rem ============================================================
setlocal
set ROOT=%~dp0..
cd /d "%ROOT%\web-server"
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
