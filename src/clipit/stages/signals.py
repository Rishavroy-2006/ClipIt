"""Signals stage: Compute audio energy spikes, speech velocity, chat activity, and scene cuts."""

import math
import numpy as np
import scipy.io.wavfile as wavfile
from typing import Dict, Any, List, Optional, Tuple
from clipit.cache import CacheManager
from clipit.config import SignalConfig
from clipit.models import FullTranscript


GAMING_KEYWORDS = [
    "oh my god", "omg", "no way", "what the", "holy", "bro", "wait", "look at",
    "are you serious", "yooo", "let's go", "sheesh", "insane", "unbelievable",
    "dead", "died", "killed", "clutch", "fail", "win", "glitch", "secret"
]

VIRAL_EMOTES = [
    "kekw", "lul", "omegalul", "lulw", "w", "l", "???", "💀", "😭", "🔥",
    "pog", "poggers", "sheesh", "cap", "no cap", "bruh", "omg", "wtffff", "damn"
]


def extract_audio_loudness_and_excitement(wav_path: str, duration: int) -> Tuple[np.ndarray, np.ndarray]:
    """Compute normalized RMS loudness and vocal excitement per 1-second bin from audio.wav."""
    try:
        sample_rate, data = wavfile.read(wav_path)
        if data.ndim > 1:
            data = data.mean(axis=1)
        data = data.astype(np.float32)

        # 1-second chunks
        chunk_size = sample_rate
        total_chunks = max(1, int(math.ceil(len(data) / chunk_size)))
        rms_values = np.zeros(total_chunks, dtype=np.float32)
        excitement_values = np.zeros(total_chunks, dtype=np.float32)

        for i in range(total_chunks):
            chunk = data[i * chunk_size : (i + 1) * chunk_size]
            if len(chunk) > 0:
                rms = np.sqrt(np.mean(chunk**2))
                rms_values[i] = rms
                # High frequency / spectral difference as vocal excitement / scream proxy
                if len(chunk) > 4:
                    diffs = np.diff(chunk)
                    hf_energy = np.sqrt(np.mean(diffs**2))
                    excitement_values[i] = hf_energy * (rms / (rms + 1e-4))

        if duration > len(rms_values):
            rms_values = np.pad(rms_values, (0, duration - len(rms_values)), mode="edge")
            excitement_values = np.pad(excitement_values, (0, duration - len(excitement_values)), mode="edge")

        return rms_values[:duration], excitement_values[:duration]
    except Exception:
        return np.zeros(duration, dtype=np.float32), np.zeros(duration, dtype=np.float32)


def extract_audio_loudness_per_second(wav_path: str, duration: int) -> np.ndarray:
    rms, _ = extract_audio_loudness_and_excitement(wav_path, duration)
    return rms


def compute_z_scores(values: np.ndarray, window: int = 300) -> np.ndarray:
    """Compute local rolling z-score of an array with a sliding window."""
    n = len(values)
    z_scores = np.zeros(n, dtype=np.float32)
    half = window // 2

    for i in range(n):
        start = max(0, i - half)
        end = min(n, i + half)
        local_slice = values[start:end]
        mean = np.mean(local_slice)
        std = np.std(local_slice)
        if std > 1e-5:
            z_scores[i] = max(0.0, float((values[i] - mean) / std))
        else:
            z_scores[i] = 0.0
    return z_scores


def run_signals(
    cache: CacheManager,
    transcript: FullTranscript,
    config: SignalConfig,
) -> Dict[str, Any]:
    """Compute combined multimodal signals per second and store in cache/<video_id>/signals.json."""
    if cache.exists("signals.json"):
        return cache.load_json("signals.json")

    duration_sec = max(1, int(math.ceil(transcript.duration)))
    audio_path = str(cache.path("audio.wav"))

    # 1. Loudness & Vocal Excitement Spikes
    rms_arr, excitement_arr = extract_audio_loudness_and_excitement(audio_path, duration_sec)
    loudness_z = compute_z_scores(rms_arr, window=config.loudness_window_sec)
    excitement_z = compute_z_scores(excitement_arr, window=config.loudness_window_sec)

    # 2. Speech Rate (words per second)
    speech_rate = np.zeros(duration_sec, dtype=np.float32)
    keyword_hits = np.zeros(duration_sec, dtype=np.float32)
    keyword_matches: Dict[int, List[str]] = {}

    for seg in transcript.segments:
        text_lower = seg.text.lower()
        matched = [kw for kw in GAMING_KEYWORDS if kw in text_lower]

        for word in seg.words:
            t = int(word.start)
            if 0 <= t < duration_sec:
                speech_rate[t] += 1.0

        if matched:
            seg_start_t = max(0, min(duration_sec - 1, int(seg.start)))
            seg_end_t = max(0, min(duration_sec - 1, int(seg.end)))
            for t in range(seg_start_t, seg_end_t + 1):
                keyword_hits[t] = 1.0
                keyword_matches.setdefault(t, []).extend(matched)

    # 3. Chat Rate & Viral Emote Sentiment (if chat.json exists)
    chat_rate = np.zeros(duration_sec, dtype=np.float32)
    if cache.exists("chat.json"):
        chat_data = cache.load_json("chat.json") or []
        for msg in chat_data:
            t = int(msg.get("t", 0))
            text = str(msg.get("m", "")).lower()
            weight = 2.5 if any(em in text for em in VIRAL_EMOTES) else 1.0
            if 0 <= t < duration_sec:
                chat_rate[t] += weight
    chat_z = compute_z_scores(chat_rate, window=300)

    # 4. Normalize & combine into composite highlight score
    speech_max = np.max(speech_rate) if np.max(speech_rate) > 0 else 1.0
    norm_speech = speech_rate / speech_max

    w = config.weights
    composite_score = (
        (w.loudness_z * np.clip((loudness_z * 0.6 + excitement_z * 0.4) / 4.0, 0, 1.0))
        + (w.chat_rate * np.clip(chat_z / 4.0, 0, 1.0))
        + (w.speech_rate * norm_speech)
        + (w.keywords * keyword_hits)
    )

    signals_payload = {
        "duration": duration_sec,
        "loudness_z": [round(float(v), 2) for v in loudness_z],
        "excitement_z": [round(float(v), 2) for v in excitement_z],
        "speech_rate": [round(float(v), 2) for v in speech_rate],
        "chat_rate": [round(float(v), 2) for v in chat_rate],
        "keyword_hits": [round(float(v), 2) for v in keyword_hits],
        "composite_score": [round(float(v), 3) for v in composite_score],
    }

    cache.save_json("signals.json", signals_payload)
    return signals_payload
