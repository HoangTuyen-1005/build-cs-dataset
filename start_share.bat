@echo off
title Multi-User Review App + Cloudflare Tunnel
echo ========================================================
echo   ASR Multi-User Review App - Cloudflare Tunnel Share
echo ========================================================
echo.

:: Uu tien su dung Python trong venv neu co
set PYTHON_CMD=python
if exist "venv\Scripts\python.exe" (
    set PYTHON_CMD=venv\Scripts\python.exe
)

echo [1/2] Dang khoi dong Review App tai http://127.0.0.1:8765 ...
start "ASR Review App Server" %PYTHON_CMD% review_app.py

timeout /t 2 /nobreak >nul

echo [2/2] Dang tao duong link Cloudflare Tunnel cong khai ...
echo Hay copy link https://...trycloudflare.com xuat hien ben duoi de gui cho nhom:
echo ========================================================
echo.

cloudflared tunnel --url http://127.0.0.1:8765
pause
