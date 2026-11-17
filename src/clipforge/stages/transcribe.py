"""Transcribe stage: Local speech-to-text with word-level timestamps.
Supports MLX Whisper on Apple Silicon and faster-whisper on Windows / Linux / CUDA / CPU.
"""

import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

# Dynamic backend import for macOS (Apple Silicon Metal) vs Windows/Linux/CUDA
try:
    import mlx_whisper
except ImportError:
    mlx_whisper = None

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None

try:
    import whisper as openai_whisper
except ImportError:
    openai_whisper = None

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

    segments: List[TranscriptSegment] = []
    total_words = 0
    detected_lang = "en"

    if mlx_whisper is not None:
        # 1. Apple Silicon Metal Whisper
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
        detected_lang = result.get("language", "en")
        raw_segments = result.get("segments", [])

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

    elif WhisperModel is not None:
        # 2. Windows / Linux / CUDA / CPU faster-whisper
        fw_model = model_name
        if "mlx-community" in model_name or "/" in model_name:
            if "turbo" in model_name.lower():
                fw_model = "large-v3-turbo"
            elif "large" in model_name.lower():
                fw_model = "large-v3"
            elif "medium" in model_name.lower():
                fw_model = "medium"
            else:
                fw_model = "base"

        # Auto-detect CUDA if available, else CPU
        device = "auto"
        compute_type = "auto"
        model = WhisperModel(fw_model, device=device, compute_type=compute_type)
        transcribe_kwargs = {
            "word_timestamps": True,
            "initial_prompt": initial_prompt,
        }
        if language:
            transcribe_kwargs["language"] = language

        seg_gen, info = model.transcribe(str(audio_path), **transcribe_kwargs)
        detected_lang = info.language or "en"

        for seg in seg_gen:
            text = seg.text.strip()
            start = float(seg.start)
            end = float(seg.end)
            words_list: List[WordTimestamp] = []

            if seg.words:
                for w in seg.words:
                    w_text = w.word.strip()
                    if not w_text:
                        continue
                    words_list.append(
                        WordTimestamp(
                            w=w_text,
                            start=float(w.start),
                            end=float(w.end),
                            prob=float(w.probability),
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

    elif openai_whisper is not None:
        # 3. Standard openai-whisper
        model = openai_whisper.load_model("base")
        result = model.transcribe(str(audio_path), initial_prompt=initial_prompt, word_timestamps=True)
        detected_lang = result.get("language", "en")
        for seg in result.get("segments", []):
            text = seg.get("text", "").strip()
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            words_list = []
            for w in seg.get("words", []):
                words_list.append(
                    WordTimestamp(
                        w=w.get("word", "").strip(),
                        start=float(w.get("start", start)),
                        end=float(w.get("end", end)),
                        prob=float(w.get("probability", 1.0)),
                    )
                )
                total_words += 1
            segments.append(TranscriptSegment(start=start, end=end, text=text, words=words_list))

    else:
        raise RuntimeError(
            "No Whisper backend available.\n"
            "• On Apple Silicon (macOS): pip install mlx-whisper\n"
            "• On Windows / Linux / CUDA: pip install faster-whisper"
        )

    duration_s = time.time() - start_time

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
