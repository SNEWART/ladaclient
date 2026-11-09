@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Вход в Telegram
echo ============================================================
echo.

REM ─── Ищем python из venv ───
set "PY_EXE="
if exist "venv\Scripts\python.exe"  set "PY_EXE=%CD%\venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PY_EXE=%CD%\.venv\Scripts\python.exe"

if "%PY_EXE%"=="" (
    echo [ERROR] venv не найден.
    echo.
    echo Искал:
    echo     venv\Scripts\python.exe
    echo     .venv\Scripts\python.exe
    echo.
    echo Создай venv в папке проекта:
    echo     python -m venv venv
    echo     venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo Python: %PY_EXE%
echo.

REM ─── Проверяем telethon ───
"%PY_EXE%" -c "import telethon" 2>nul
if errorlevel 1 (
    echo [ERROR] В venv нет telethon.
    echo Установи:
    echo     "%PY_EXE%" -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM ─── Запускаем login.py ───
"%PY_EXE%" -X utf8 -u login.py

echo.
echo ============================================================
pause