"""Candidates stage: Peak finding on multimodal highlight score curve and candidate window generation."""

import numpy as np
from typing import List, Dict, Any
from clipforge.cache import CacheManager
from clipforge.models import FullTranscript, CandidateWindow


def run_candidates(
    cache: CacheManager,
    transcript: FullTranscript,
    signals: Dict[str, Any],
    target_clips: int = 10,
    min_length: float = 15.0,
    max_length: float = 60.0,
    preferred_length: float = 35.0,
) -> List[CandidateWindow]:
    """Find peak windows in composite highlight score and generate candidate windows."""
    if cache.exists("candidates.json"):
        raw = cache.load_json("candidates.json")
        return [CandidateWindow(**c) for c in raw]

    composite = np.array(signals.get("composite_score", []), dtype=np.float32)
    duration = len(composite)
    if duration == 0:
        return []

    # Smooth the curve with a moving average filter
    kernel_size = 5
    kernel = np.ones(kernel_size) / kernel_size
    smoothed = np.convolve(composite, kernel, mode="same")

    # Find peaks above median
    threshold = np.percentile(smoothed, 70) if len(smoothed) > 10 else 0.1
    peaks = []
    for i in range(1, duration - 1):
        if smoothed[i] > smoothed[i - 1] and smoothed[i] > smoothed[i + 1] and smoothed[i] >= threshold:
            peaks.append((i, float(smoothed[i])))

    # Sort peaks by score descending
    peaks.sort(key=lambda x: x[1], reverse=True)

    candidates: List[CandidateWindow] = []
    half_window = preferred_length / 2.0

    for idx, (peak_sec, score) in enumerate(peaks):
        start = max(0.0, peak_sec - half_window)
        end = min(float(duration), peak_sec + half_window)

        # Enforce bounds
        if end - start < min_length:
            end = min(float(duration), start + min_length)

        # Check overlap with existing candidates
        overlap = False
        for c in candidates:
            # Overlap check
            overlap_start = max(start, c.start)
            overlap_end = min(end, c.end)
            if overlap_end > overlap_start:
                overlap_dur = overlap_end - overlap_start
                if overlap_dur / (end - start) > 0.4:
                    overlap = True
                    break

        if not overlap:
            cid = f"cand_{len(candidates) + 1:02d}"
            # Extract transcript text within this candidate
            cand_text_snippets = []
            for seg in transcript.segments:
                if seg.end >= start and seg.start <= end:
                    cand_text_snippets.append(seg.text)

            reason = f"Peak composite highlight score {score:.2f} near {int(peak_sec)}s."
            if cand_text_snippets:
                reason += f" Audio: '{cand_text_snippets[0][:60]}...'"

            candidates.append(
                CandidateWindow(
                    id=cid,
                    start=round(start, 2),
                    end=round(end, 2),
                    score=round(score * 100, 1),
                    loudness_z=signals.get("loudness_z", [0])[int(peak_sec)] if int(peak_sec) < duration else 0.0,
                    chat_rate=signals.get("chat_rate", [0])[int(peak_sec)] if int(peak_sec) < duration else 0.0,
                    speech_rate=signals.get("speech_rate", [0])[int(peak_sec)] if int(peak_sec) < duration else 0.0,
                    reason=reason,
                )
            )

        if len(candidates) >= target_clips * 2:
            break

    # If video had very few peaks, create default time slices
    if not candidates and duration >= min_length:
        step = max(min_length, min(preferred_length, float(duration)))
        curr = 0.0
        while curr + min_length <= duration and len(candidates) < target_clips:
            cid = f"cand_{len(candidates) + 1:02d}"
            candidates.append(
                CandidateWindow(
                    id=cid,
                    start=round(curr, 2),
                    end=round(min(float(duration), curr + step), 2),
                    score=75.0,
                    reason="Interval candidate based on steady stream pacing.",
                )
            )
            curr += step

    cache.save_json("candidates.json", [c.model_dump() for c in candidates])
    return candidates
