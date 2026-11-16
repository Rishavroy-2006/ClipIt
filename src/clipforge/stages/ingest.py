"""Ingest stage: Download or copy source video and extract 16kHz mono audio for ASR."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
from clipforge.cache import CacheManager, get_video_id


def run_ingest(
    url_or_path: str,
    cache: CacheManager,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    """Ingest a video from URL or local path into cache/<video_id>/."""
    source_mp4 = cache.path("source.mp4")
    audio_wav = cache.path("audio.wav")
    info_json = cache.path("info.json")

    # If already ingested, return existing
    if source_mp4.exists() and audio_wav.exists() and info_json.exists():
        return cache.load_json("info.json")

    is_url = url_or_path.startswith("http://") or url_or_path.startswith("https://")
    meta: Dict[str, Any] = {
        "source": url_or_path,
        "video_id": cache.video_id,
        "is_url": is_url,
        "title": Path(url_or_path).stem if not is_url else "Online Stream",
    }

    if is_url:
        # Download using yt-dlp
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "--merge-output-format", "mp4",
            "-o", str(source_mp4),
            url_or_path,
        ]
        if start and end:
            cmd.extend(["--download-sections", f"*{start}-{end}"])
        subprocess.run(cmd, check=True)

        # Try to extract video info
        info_cmd = ["yt-dlp", "--dump-json", "--no-download", url_or_path]
        try:
            res = subprocess.run(info_cmd, capture_output=True, text=True, check=True)
            ytdl_meta = json.loads(res.stdout)
            meta["title"] = ytdl_meta.get("title", meta["title"])
            meta["uploader"] = ytdl_meta.get("uploader", "")
            meta["duration"] = ytdl_meta.get("duration", 0)
        except Exception:
            pass
    else:
        # Local file
        src_path = Path(url_or_path)
        if not src_path.exists():
            raise FileNotFoundError(f"Source video not found: {src_path}")
        if start or end:
            # Trim section using ffmpeg
            trim_cmd = ["ffmpeg", "-y"]
            if start:
                trim_cmd.extend(["-ss", start])
            if end:
                trim_cmd.extend(["-to", end])
            trim_cmd.extend(["-i", str(src_path), "-c", "copy", str(source_mp4)])
            subprocess.run(trim_cmd, check=True)
        else:
            shutil.copy2(src_path, source_mp4)

    # Extract 16kHz mono WAV for Whisper transcription
    wav_cmd = [
        "ffmpeg", "-y",
        "-i", str(source_mp4),
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(audio_wav),
    ]
    subprocess.run(wav_cmd, check=True)

    # Get duration if not already set
    if "duration" not in meta or not meta["duration"]:
        dur_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(source_mp4),
        ]
        res = subprocess.run(dur_cmd, capture_output=True, text=True)
        try:
            meta["duration"] = float(res.stdout.strip())
        except ValueError:
            meta["duration"] = 0.0

    cache.save_json("info.json", meta)
    return meta
