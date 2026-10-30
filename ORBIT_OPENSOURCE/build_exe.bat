@echo off
REM Build a single-file Windows executable: dist\ORBIT.exe
setlocal
cd /d "%~dp0"

echo [ORBIT] 1/4  Checking the virtual environment...
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 goto :error
)

echo [ORBIT] 2/4  Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo [ORBIT] 3/4  Generating the application icon...
".venv\Scripts\python.exe" tools\make_icon.py
if errorlevel 1 goto :error

echo [ORBIT] 4/4  Packaging with PyInstaller...
".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --name ORBIT ^
    --icon assets\orbit.ico ^
    --hidden-import mutagen ^
    --hidden-import mutagen.id3 ^
    --hidden-import mutagen.flac ^
    --hidden-import psutil ^
    --hidden-import pycaw.pycaw ^
    --hidden-import pycaw.constants ^
    --hidden-import comtypes ^
    --hidden-import comtypes.client ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtWebEngineQuick ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick3D ^
    --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.QtCharts ^
    --exclude-module PySide6.QtDataVisualization ^
    --exclude-module PySide6.QtMultimediaWidgets ^
    --exclude-module PySide6.QtPdf ^
    --exclude-module PySide6.QtDesigner ^
    main.py
if errorlevel 1 goto :error

echo.
echo [ORBIT] Done. The executable is dist\ORBIT.exe
echo         Run it, then press your activation shortcut (default Alt+Space).
goto :eof

:error
echo.
echo [ORBIT] Build failed. Read the messages above.
exit /b 1
