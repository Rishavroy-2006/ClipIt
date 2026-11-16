#!/usr/bin/env bash
set -e

echo "=========================================="
echo "    ClipForge Environment Verification"
echo "=========================================="

echo "Architecture: $(uname -m)"
echo "macOS Version: $(sw_vers -productVersion)"
echo ""

echo "--- Disk Space ---"
df -h / | awk 'NR==1 || NR==2'
echo ""

echo "--- Media Tools ---"
which ffmpeg || { echo "ERROR: ffmpeg not found"; exit 1; }
which yt-dlp || { echo "ERROR: yt-dlp not found"; exit 1; }
which uv || { echo "ERROR: uv not found"; exit 1; }

echo "FFmpeg: $(ffmpeg -version | head -n 1)"
echo "yt-dlp: $(yt-dlp --version)"
echo "uv: $(uv --version)"
echo ""

echo "--- FFmpeg Capability Checks ---"
if ffmpeg -filters 2>&1 | grep -qE "ass|subtitles"; then
    echo "✔ libass (Subtitles filter) enabled"
else
    echo "✖ libass NOT found in ffmpeg"
fi

if ffmpeg -encoders 2>&1 | grep -q "h264_videotoolbox"; then
    echo "✔ h264_videotoolbox (Hardware acceleration) enabled"
else
    echo "✖ h264_videotoolbox NOT found"
fi
echo ""

echo "--- Python Virtual Environment ---"
if [ -d ".venv" ]; then
    echo "✔ Virtual environment found at .venv"
    source .venv/bin/activate
    python3 --version
    python3 -c "import typer, rich, pydantic, pysubs2; print('✔ Core python libraries imported successfully')"
else
    echo "Virtual environment not yet created"
fi

echo "=========================================="
echo "Environment verification complete!"
echo "=========================================="
