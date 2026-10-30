@echo off
REM Start ORBIT in the background without a console window.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" main.py %*
    goto :eof
)
if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" main.py %*
    goto :eof
)

echo ORBIT is not installed yet.
echo Run these commands once, from this folder:
echo.
echo     py -3 -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1
