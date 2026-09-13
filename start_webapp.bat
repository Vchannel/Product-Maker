@echo off
chcp 65001 >nul
rem Windows: double-click de chay Product Maker.
cd /d "%~dp0"

rem Tim Python: uu tien "py -3" (Python Launcher), roi "python" that (khong phai ban gia cua Microsoft Store).
set "PYCMD="
where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYCMD=python"
)
if not defined PYCMD (
    echo Chua cai Python 3.10+ . Tai tai https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^).
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Lan chay dau: dang tao moi truong Python ^(.venv^)...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo Khong tao duoc .venv.
        pause
        exit /b 1
    )
)

rem Cai / cap nhat thu vien khi requirements.txt thay doi.
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
    echo Dang cai thu vien can thiet...
    ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt && copy /y requirements.txt ".venv\requirements.installed" >nul
)

".venv\Scripts\python.exe" webapp.py %*
pause
