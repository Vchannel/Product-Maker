@echo off
chcp 65001 >nul
rem Windows: double-click de chay Product Maker.
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul || set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"

if not exist ".venv\Scripts\python.exe" (
    echo Lan chay dau: dang tao moi truong Python ^(.venv^)...
    "%PY%" -m venv .venv
    if errorlevel 1 (
        echo Khong tao duoc .venv - kiem tra da cai Python 3 chua.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -c "import flask, waitress, anthropic, PIL, numpy, lxml, bs4, dotenv" >nul 2>nul
if errorlevel 1 (
    echo Dang cai thu vien can thiet...
    ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)

".venv\Scripts\python.exe" webapp.py %*
pause
