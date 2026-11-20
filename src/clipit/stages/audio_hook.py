"""Entry Audio Hook Generator: Synthesize attractive, punchy spoken hooks for YouTube Shorts/TikTok."""

import asyncio
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict
from clipit.models import ClipItem
from clipit.cache import CacheManager

VOICE_PRESETS: Dict[str, str] = {
    "christopher": "en-US-ChristopherNeural",  # Energetic, viral gaming narrator (Default)
    "andrew": "en-US-AndrewNeural",            # Confident, punchy modern narrator
    "guy": "en-US-GuyNeural",                  # Smooth, authentic storyteller
    "jenny": "en-US-JennyNeural",              # Upbeat, lively female voice
    "ava": "en-US-AvaNeural",                  # Warm, captivating female voice
}


def clean_spoken_hook(title: str, topic: Optional[str] = "GTA 6") -> str:
    """Clean and contextualize a clip title into an engaging, attractive spoken entry hook.

    Example:
        'Stealing a Jet in 60 Seconds! 🛩️' -> 'Stealing a Jet in GTA 6 in 60 Seconds!'
        'Speed Found His Wifey? 💍'       -> 'Speed Found His Wifey?'
        'Lucia Is A Menace!'              -> 'Lucia Is A Menace in GTA 6!'
    """
    if not title:
        return "Check this viral moment out!"

    # 1. Strip emojis, unicode variation selectors, and symbols
    t = re.sub(r"[\U00010000-\U0010ffff\u200d\ufe0f\u2600-\u27bf\u2b50]", "", title)
    # 2. Strip leading [Part X] or Part X:
    t = re.sub(r"^\[?Part\s*\d+\]?:?\s*", "", t, flags=re.IGNORECASE)
    # 3. Strip trailing hashtags (#Shorts, #GTA6, etc.)
    t = re.sub(r"#\w+", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -:!")

    # Check if topic (e.g. GTA 6) is already present in text
    has_topic = bool(re.search(r"\b(gta\s*6|gta|grand theft auto)\b", t, re.IGNORECASE))

    if not has_topic and topic:
        # Pattern like: 'Stealing a Jet in 60 Seconds' -> 'Stealing a Jet in GTA 6 in 60 Seconds'
        m = re.match(r"^(.*?)(\s+in\s+\d+\s+(?:seconds?|mins?|minutes?|hours?|days?|secs?).*)$", t, re.IGNORECASE)
        if m:
            t = f"{m.group(1)} in {topic}{m.group(2)}"
        elif len(t.split()) <= 6 and not t.endswith("?"):
            t = f"{t} in {topic}"

    # Capitalize the opening word
    if t:
        t = t[0].upper() + t[1:]

    # Ensure clean ending punctuation
    if not t.endswith(("?", "!")):
        t += "!"

    return t


def resolve_voice(voice_name: str) -> str:
    """Resolve shorthand voice names to Edge-TTS neural voice identifiers."""
    v = (voice_name or "christopher").lower().strip()
    return VOICE_PRESETS.get(v, voice_name)


def generate_audio_hook(
    text: str,
    out_wav: Path,
    voice: str = "en-US-ChristopherNeural",
    rate: str = "+15%",
) -> Optional[Path]:
    """Synthesize speech for the hook line and save as 48kHz broadcast WAV audio.

    Uses Edge-TTS neural voices with graceful fallback to local macOS speech synthesis.
    """
    if not text:
        return None

    resolved_voice = resolve_voice(voice)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    temp_mp3 = out_wav.with_suffix(".tmp.mp3")

    # 1. Try Edge-TTS (Microsoft Neural Voices: ultra-clean, viral TikTok/Shorts quality)
    try:
        import edge_tts

        async def _synth():
            comm = edge_tts.Communicate(text=text, voice=resolved_voice, rate=rate)
            await comm.save(str(temp_mp3))

        asyncio.run(_synth())

        if temp_mp3.exists() and temp_mp3.stat().st_size > 1000:
            # Convert to 48kHz stereo WAV matching the pipeline's audio configuration
            conv_cmd = [
                "ffmpeg", "-y",
                "-i", str(temp_mp3),
                "-ar", "48000",
                "-ac", "2",
                str(out_wav)
            ]
            subprocess.run(conv_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            temp_mp3.unlink(missing_ok=True)
            return out_wav
    except Exception as e:
        print(f"[AUDIO-HOOK] Edge-TTS error ({e}), trying local fallback...")
        if temp_mp3.exists():
            temp_mp3.unlink(missing_ok=True)

    # 2. Local Fallback (macOS say command)
    try:
        temp_aiff = out_wav.with_suffix(".tmp.aiff")
        # Try Daniel or Samantha voice for clean English speech
        res = subprocess.run(["say", "-v", "Daniel", text, "-o", str(temp_aiff)], capture_output=True)
        if res.returncode != 0:
            subprocess.run(["say", text, "-o", str(temp_aiff)], check=True, capture_output=True)

        if temp_aiff.exists():
            conv_cmd = [
                "ffmpeg", "-y",
                "-i", str(temp_aiff),
                "-ar", "48000",
                "-ac", "2",
                str(out_wav)
            ]
            subprocess.run(conv_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            temp_aiff.unlink(missing_ok=True)
            return out_wav
    except Exception as fallback_err:
        print(f"[AUDIO-HOOK] Local speech fallback failed: {fallback_err}")

    return None


def get_hook_duration(wav_path: Path) -> float:
    """Probe audio duration in seconds."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(wav_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return 2.5


def get_or_generate_audio_hook(
    clip: ClipItem,
    cache: CacheManager,
    topic: Optional[str] = "GTA 6",
    voice: str = "en-US-ChristopherNeural",
    rate: str = "+15%",
    force: bool = False,
) -> Optional[Path]:
    """Retrieve existing cached audio hook or generate a new one for the clip."""
    hook_file = cache.path(f"hook_{clip.id}.wav")
    meta_file = cache.path(f"hook_{clip.id}.json")

    title_source = getattr(clip, "title", None) or getattr(clip, "hook_line", None) or "Viral Highlight"
    spoken_text = clean_spoken_hook(title_source, topic=topic)

    if not force and hook_file.exists() and hook_file.stat().st_size > 5000:
        return hook_file

    hook_path = generate_audio_hook(
        text=spoken_text,
        out_wav=hook_file,
        voice=voice,
        rate=rate,
    )

    if hook_path and hook_path.exists():
        dur = get_hook_duration(hook_path)
        cache.save_json(
            f"hook_{clip.id}.json",
            {
                "clip_id": clip.id,
                "text": spoken_text,
                "voice": voice,
                "duration": dur,
            }
        )
        return hook_path

    return None
