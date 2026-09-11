@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python webapp.py
) else (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" webapp.py
)
pause
