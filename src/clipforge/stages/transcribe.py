"""Transcribe stage: Local Metal-accelerated speech-to-text with word-level timestamps using MLX Whisper."""

import time
from pathlib import Path
from typing import Optional, Dict, Any, List
import mlx_whisper
from clipforge.cache import CacheManager
from clipforge.models import FullTranscript, TranscriptSegment, WordTimestamp


DEFAULT_GLOSSARY = (
    "GTA 6, Grand Theft Auto VI, Rockstar Games, Vice City, Lucia, Jason, Leonida, "
    "trailer, stream, chat, gameplay, clip, Twitch, YouTube Shorts"
)


def run_transcribe(
    cache: CacheManager,
    model_name: str = "mlx-community/whisper-large-v3-turbo",
    glossary: Optional[str] = None,
    language: Optional[str] = None,
) -> FullTranscript:
    """Run local Metal transcription on cache/<video_id>/audio.wav."""
    if cache.exists("transcript.json"):
        raw = cache.load_json("transcript.json")
        return FullTranscript(**raw)

    audio_path = cache.path("audio.wav")
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file missing for transcription: {audio_path}")

    initial_prompt = glossary or DEFAULT_GLOSSARY
    start_time = time.time()

    transcribe_kwargs: Dict[str, Any] = {
        "audio": str(audio_path),
        "path_or_hf_repo": model_name,
        "word_timestamps": True,
        "initial_prompt": initial_prompt,
        "verbose": False,
    }
    if language:
        transcribe_kwargs["language"] = language

    result = mlx_whisper.transcribe(**transcribe_kwargs)
    duration_s = time.time() - start_time

    detected_lang = result.get("language", "en")
    raw_segments = result.get("segments", [])

    segments: List[TranscriptSegment] = []
    total_words = 0

    for seg in raw_segments:
        text = seg.get("text", "").strip()
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        words_list: List[WordTimestamp] = []

        for w_data in seg.get("words", []):
            w_text = w_data.get("word", "").strip()
            if not w_text:
                continue
            words_list.append(
                WordTimestamp(
                    w=w_text,
                    start=float(w_data.get("start", start)),
                    end=float(w_data.get("end", end)),
                    prob=float(w_data.get("probability", 1.0)),
                )
            )
            total_words += 1

        segments.append(
            TranscriptSegment(
                start=start,
                end=end,
                text=text,
                words=words_list,
            )
        )

    info = cache.load_json("info.json") or {}
    video_dur = info.get("duration", (segments[-1].end if segments else 0.0))
    rtf = (video_dur / duration_s) if duration_s > 0 else 0.0

    full = FullTranscript(
        video_id=cache.video_id,
        duration=video_dur,
        language=detected_lang,
        segments=segments,
    )

    cache.save_json("transcript.json", full.model_dump())

    # Log metrics
    metrics = cache.load_json("metrics.json") or {}
    metrics["transcription_time_sec"] = round(duration_s, 2)
    metrics["audio_duration_sec"] = round(video_dur, 2)
    metrics["real_time_factor"] = round(rtf, 2)
    metrics["total_words"] = total_words
    cache.save_json("metrics.json", metrics)

    return full
