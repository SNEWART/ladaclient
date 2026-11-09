@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   Диагностика venv
echo ============================================================
echo.
echo Текущая папка: %CD%
echo.

echo --- Файлы в корне проекта ---
if exist "requirements.txt" echo   [OK] requirements.txt
if exist "main.py" echo   [OK] main.py
if exist "login.py" echo   [OK] login.py
echo.

echo --- Папка venv ---
if exist "venv" (
    echo   [OK] venv\
) else (
    echo   [--] venv\ — нет
)
if exist ".venv" (
    echo   [OK] .venv\
) else (
    echo   [--] .venv\ — нет
)
echo.

echo --- Ищем python.exe в venv ---
if exist "venv\Scripts\python.exe" (
    echo   [OK] venv\Scripts\python.exe
    "venv\Scripts\python.exe" --version
) else (
    echo   [--] venv\Scripts\python.exe — нет
)
if exist ".venv\Scripts\python.exe" (
    echo   [OK] .venv\Scripts\python.exe
    ".venv\Scripts\python.exe" --version
) else (
    echo   [--] .venv\Scripts\python.exe — нет
)
echo.

echo --- Проверка пакетов в .venv ---
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import telethon; print('telethon OK', telethon.__version__)" 2>&1
    ".venv\Scripts\python.exe" -c "import ollama; print('ollama OK')" 2>&1
    ".venv\Scripts\python.exe" -c "import fastapi; print('fastapi OK')" 2>&1
)
echo.

pause