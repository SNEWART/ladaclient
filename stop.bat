@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "data\run.pid" (
    echo Не запущено.
    exit /b 0
)

set /p PID=<data\run.pid
echo Останавливаю PID %PID%...
taskkill /PID %PID% /T /F >nul 2>&1
del /q "data\run.pid" 2>nul
echo Готово.
exit /b 0