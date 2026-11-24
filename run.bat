@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0" || exit /b 1

if exist "backend\.venv\Scripts\python.exe" (
    "backend\.venv\Scripts\python.exe" dev.py
) else (
    python dev.py
)
set "result=%errorlevel%"

popd
if not "%result%"=="0" pause
exit /b %result%
