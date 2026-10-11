@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist "data" mkdir data
if not exist "data\logs" mkdir data\logs

REM ─── Ищем python из venv ───
set "PY_EXE="
if exist "venv\Scripts\python.exe"  set "PY_EXE=%CD%\venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PY_EXE=%CD%\.venv\Scripts\python.exe"

if "%PY_EXE%"=="" (
    echo [ERROR] venv не найден.
    echo Ожидается venv\Scripts\python.exe или .venv\Scripts\python.exe
    pause
    exit /b 1
)

echo Использую Python: %PY_EXE%
"%PY_EXE%" -c "import sys; print('  python:', sys.executable); print('  version:', sys.version.split()[0])"

REM ─── Проверка зависимостей ───
"%PY_EXE%" -c "from watchdog.observers import Observer" 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] В venv нет watchdog.
    echo Установи в venv:
    echo     "%PY_EXE%" -m pip install watchdog
    echo.
    pause
    exit /b 1
)

"%PY_EXE%" -c "import ollama" 2>nul
if errorlevel 1 (
    echo.
    echo [ERROR] В venv нет ollama.
    echo     "%PY_EXE%" -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM ─── Уже запущено? ───
if exist "data\run.pid" (
    set /p OLD_PID=<data\run.pid
    tasklist /FI "PID eq %OLD_PID%" 2>nul | find "%OLD_PID%" >nul
    if not errorlevel 1 (
        echo Уже запущено ^(PID %OLD_PID%^). Открываю панель.
        start "" http://127.0.0.1:8080
        exit /b 0
    )
    del /q "data\run.pid" 2>nul
)

REM ─── Запуск в отдельном окне, python по полному пути ───
start "LadaSupervisor" cmd /k ""%PY_EXE%" -X utf8 -u run.py"

echo Ожидание запуска...
set /a TRIES=0
:wait_loop
timeout /t 1 /nobreak >nul
if exist "data\run.pid" goto started
set /a TRIES+=1
if %TRIES% lss 60 goto wait_loop
echo [ERROR] run.py не создал PID-файл за 60 секунд.
echo Смотри окно "LadaSupervisor" — там ошибка.
pause
exit /b 1

:started
echo OK, супервизор запущен. Открываю панель.
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8080
exit /b 0