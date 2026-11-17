"""Ingest stage: Download or copy source video, extract 16kHz audio, and parse live chat replay."""

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
from clipit.cache import CacheManager


def parse_youtube_live_chat(chat_file: Path) -> List[Dict[str, Any]]:
    """Parse raw yt-dlp .live_chat.json file into structured [{t, text, user}]."""
    if not chat_file.exists():
        return []

    messages = []
    with open(chat_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                replay_item = data.get("replayChatItemAction", {})
                offset_msec = replay_item.get("videoOffsetTimeMsec")
                if offset_msec is None:
                    continue
                t_sec = float(offset_msec) / 1000.0

                actions = replay_item.get("actions", [])
                for action in actions:
                    chat_item = action.get("addChatItemAction", {}).get("item", {})
                    renderer = chat_item.get("liveChatTextMessageRenderer") or chat_item.get("liveChatPaidMessageRenderer")
                    if renderer:
                        runs = renderer.get("message", {}).get("runs", [])
                        text = "".join(r.get("text", "") for r in runs).strip()
                        author = renderer.get("authorName", {}).get("simpleText", "User")
                        if text:
                            messages.append({"t": round(t_sec, 2), "text": text, "user": author})
            except Exception:
                continue

    return sorted(messages, key=lambda m: m["t"])


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
    now_ts = time.time()
    meta: Dict[str, Any] = {
        "source": url_or_path,
        "video_id": cache.video_id,
        "is_url": is_url,
        "title": Path(url_or_path).stem if not is_url else "Online Stream",
        "created_at": now_ts,
        "created_iso": datetime.now(timezone.utc).isoformat(),
    }

    if is_url:
        # Download best video + audio and live chat replay using yt-dlp
        cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "--merge-output-format", "mp4",
            "--write-info-json",
            "--write-subs",
            "--sub-langs", "live_chat",
            "-o", str(source_mp4),
            url_or_path,
        ]
        if start and end:
            cmd.extend(["--download-sections", f"*{start}-{end}"])
        subprocess.run(cmd, check=True)

        # Extract video info from downloaded info.json (fast, zero extra network latency)
        info_json_file = cache.path("source.info.json")
        if not info_json_file.exists():
            for cand in cache.dir.glob("*.info.json"):
                if cand.name != "info.json":
                    info_json_file = cand
                    break

        if info_json_file.exists():
            try:
                with open(info_json_file, "r", encoding="utf-8") as f:
                    ytdl_meta = json.load(f)
                    meta["title"] = ytdl_meta.get("title", meta["title"])
                    meta["uploader"] = ytdl_meta.get("uploader", "")
                    meta["duration"] = ytdl_meta.get("duration", 0)
            except Exception:
                pass
        else:
            # Fallback to remote metadata query only if info.json was not created
            info_cmd = ["yt-dlp", "--dump-json", "--no-download", url_or_path]
            try:
                res = subprocess.run(info_cmd, capture_output=True, text=True, check=True)
                ytdl_meta = json.loads(res.stdout)
                meta["title"] = ytdl_meta.get("title", meta["title"])
                meta["uploader"] = ytdl_meta.get("uploader", "")
                meta["duration"] = ytdl_meta.get("duration", 0)
            except Exception:
                pass

        # Check if live chat file was downloaded
        live_chat_file = cache.path("source.live_chat.json")
        if not live_chat_file.exists():
            # Check for alternative yt-dlp naming
            for cand in cache.dir.glob("*.live_chat.json"):
                live_chat_file = cand
                break

        if live_chat_file.exists():
            chat_msgs = parse_youtube_live_chat(live_chat_file)
            if chat_msgs:
                cache.save_json("chat.json", chat_msgs)
                print(f"✔ Extracted {len(chat_msgs)} live chat messages")

    else:
        # Local file
        src_path = Path(url_or_path)
        if not src_path.exists():
            raise FileNotFoundError(f"Source video not found: {src_path}")
        if start or end:
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

    # Probe resolution & duration via ffprobe
    if "duration" not in meta or not meta.get("duration") or "width" not in meta or "height" not in meta:
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json",
            str(source_mp4),
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True)
        if res.returncode == 0:
            try:
                p_data = json.loads(res.stdout)
                streams = p_data.get("streams", [])
                if streams:
                    meta.setdefault("width", int(streams[0].get("width", 1920)))
                    meta.setdefault("height", int(streams[0].get("height", 1080)))
                fmt = p_data.get("format", {})
                if "duration" not in meta or not meta["duration"]:
                    meta["duration"] = float(fmt.get("duration", 0.0))
            except Exception:
                meta.setdefault("duration", 0.0)
                meta.setdefault("width", 1920)
                meta.setdefault("height", 1080)
        else:
            meta.setdefault("duration", 0.0)
            meta.setdefault("width", 1920)
            meta.setdefault("height", 1080)

    cache.save_json("info.json", meta)
    return meta
