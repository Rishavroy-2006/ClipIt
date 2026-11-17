@echo off
echo ==========================================
echo    ClipForge Windows Environment Check
echo ==========================================
echo.

where ffmpeg >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [PASS] FFmpeg found in PATH.
) else (
    echo [FAIL] FFmpeg NOT found in PATH. Please install via: winget install Gyan.FFmpeg
)

where yt-dlp >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [PASS] yt-dlp found in PATH.
) else (
    echo [WARN] yt-dlp NOT found in PATH. Please install via: winget install yt-dlp or pip install yt-dlp
)

where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [PASS] Python detected.
    python --version
) else (
    echo [FAIL] Python not found in PATH!
)

echo.
echo Running ClipForge Diagnostics...
if exist ".venv\Scripts\clipforge.exe" (
    ".venv\Scripts\clipforge.exe" check
) else (
    python -m clipforge check 2>nul || python -m clipforge.cli check 2>nul || echo Please create and activate your virtual environment: python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -e .
)

echo.
pause
