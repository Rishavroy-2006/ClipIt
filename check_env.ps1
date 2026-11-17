Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   ClipForge Windows Environment Check    " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# 1. FFmpeg
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    $ffmpegVer = ffmpeg -version 2>&1 | Select-Object -First 1
    Write-Host "[PASS] FFmpeg found: $ffmpegVer" -ForegroundColor Green
} else {
    Write-Host "[FAIL] FFmpeg NOT found in PATH. Install via: winget install Gyan.FFmpeg" -ForegroundColor Red
}

# 2. yt-dlp
if (Get-Command yt-dlp -ErrorAction SilentlyContinue) {
    Write-Host "[PASS] yt-dlp found in PATH." -ForegroundColor Green
} else {
    Write-Host "[WARN] yt-dlp NOT found in PATH. Install via: winget install yt-dlp or pip install yt-dlp" -ForegroundColor Yellow
}

# 3. Python
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pyVer = python --version 2>&1
    Write-Host "[PASS] Python found: $pyVer" -ForegroundColor Green
} else {
    Write-Host "[FAIL] Python NOT found in PATH." -ForegroundColor Red
}

Write-Host ""
Write-Host "Running ClipForge Diagnostics..." -ForegroundColor Cyan

if (Test-Path ".\.venv\Scripts\clipforge.exe") {
    & ".\.venv\Scripts\clipforge.exe" check
} else {
    try {
        python -m clipforge check
    } catch {
        Write-Host "Virtual environment not initialized. Run: python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -e ." -ForegroundColor Yellow
    }
}
