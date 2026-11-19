"""Punch-in zoom stage: Dynamic attention-resetting zoom pushes on high-energy vocal peaks."""

from typing import List, Tuple, Optional
import numpy as np


def _smoothstep(t: float) -> float:
    """Smoothstep easing function on [0, 1]."""
    clamped = max(0.0, min(1.0, t))
    return clamped * clamped * (3.0 - 2.0 * clamped)


def find_emphasis_times(
    signals_data: List[dict],
    clip_start: float,
    clip_end: float,
    min_gap_seconds: float = 15.0,
    z_score_threshold: float = 1.8,
) -> List[float]:
    """Identify energetic emphasis points (seconds relative to clip_start) suitable for punch-in zoom."""
    clip_signals = [
        s for s in signals_data
        if clip_start <= s.get("timestamp", -1.0) <= clip_end
    ]
    if not clip_signals:
        return []

    # Sort candidates by loudness or composite virality score
    candidates = []
    for s in clip_signals:
        z = s.get("loudness_z", 0.0)
        c_score = s.get("composite_score", 0.0)
        t_rel = s.get("timestamp", 0.0) - clip_start
        if z >= z_score_threshold or c_score >= 80:
            candidates.append((t_rel, max(z, c_score / 40.0)))

    # Deduplicate with min_gap_seconds
    candidates.sort(key=lambda x: x[1], reverse=True)
    accepted_times: List[float] = []

    for t_rel, strength in candidates:
        # Don't punch in the very first second or very last 2 seconds of the clip
        if t_rel < 1.0 or t_rel > (clip_end - clip_start - 2.0):
            continue
        if all(abs(t_rel - existing) >= min_gap_seconds for existing in accepted_times):
            accepted_times.append(t_rel)

    return sorted(accepted_times)


def build_punch_in_terms(
    emphasis_times: List[float],
    fps: float = 30.0,
    max_zoom: float = 1.10,
    rise_seconds: float = 0.25,
    hold_seconds: float = 1.20,
    fall_seconds: float = 0.50,
) -> List[str]:
    """Generate additive zoom expressions for FFmpeg zoompan filter."""
    terms = []
    strength = max_zoom - 1.0

    for t in emphasis_times:
        start_frame = int(round(t * fps))
        span_rise = max(1, int(round(rise_seconds * fps)))
        span_hold = max(1, int(round(hold_seconds * fps)))
        span_fall = max(1, int(round(fall_seconds * fps)))

        f_rise_end = start_frame + span_rise
        f_hold_end = f_rise_end + span_hold
        f_fall_end = f_hold_end + span_fall

        # Gate expression between start_frame and f_fall_end
        gate = f"between(on,{start_frame},{f_fall_end})"

        # Eased rise -> hold -> eased fall
        # When on is in hold window: full strength
        # When on is in rise window: linear/smoothstep approximation
        rise_term = f"({strength:.4f}*clip((on-{start_frame})/{span_rise},0,1)*between(on,{start_frame},{f_rise_end}))"
        hold_term = f"({strength:.4f}*between(on,{f_rise_end},{f_hold_end}))"
        fall_term = f"({strength:.4f}*(1-clip((on-{f_hold_end})/{span_fall},0,1))*between(on,{f_hold_end},{f_fall_end}))"

        terms.append(f"({rise_term}+{hold_term}+{fall_term})")

    return terms
