"""Refine stage: Boundary snapping to word timestamps, length enforcement, de-repetition, and deduplication."""

from typing import List
from clipforge.cache import CacheManager
from clipforge.models import FullTranscript, ClipItem


def snap_boundaries_to_words(
    clip: ClipItem,
    transcript: FullTranscript,
    min_length: float = 15.0,
    max_length: float = 60.0,
) -> ClipItem:
    """Snap clip start and end to natural word and pause boundaries."""
    # Collect all words in or near window
    all_words = []
    for seg in transcript.segments:
        all_words.extend(seg.words)

    if not all_words:
        return clip

    # Find closest word to start
    start_candidates = [w for w in all_words if abs(w.start - clip.start) <= 2.0]
    snapped_start = clip.start
    if start_candidates:
        first_w = min(start_candidates, key=lambda w: abs(w.start - clip.start))
        snapped_start = max(0.0, first_w.start - 0.3)

    # Find closest word to end
    end_candidates = [w for w in all_words if abs(w.end - clip.end) <= 2.0]
    snapped_end = clip.end
    if end_candidates:
        last_w = min(end_candidates, key=lambda w: abs(w.end - clip.end))
        snapped_end = last_w.end + 0.5

    # Enforce duration bounds
    dur = snapped_end - snapped_start
    if dur < min_length:
        snapped_end = snapped_start + min_length
    elif dur > max_length:
        snapped_end = snapped_start + max_length

    clip.start = round(snapped_start, 2)
    clip.end = round(snapped_end, 2)
    return clip


def deduplicate_clips(clips: List[ClipItem], overlap_threshold: float = 0.30) -> List[ClipItem]:
    """Remove overlapping clips, keeping the one with higher virality score."""
    sorted_clips = sorted(clips, key=lambda c: c.score, reverse=True)
    kept: List[ClipItem] = []

    for c in sorted_clips:
        has_overlap = False
        c_dur = max(0.1, c.end - c.start)

        for existing in kept:
            overlap_start = max(c.start, existing.start)
            overlap_end = min(c.end, existing.end)
            if overlap_end > overlap_start:
                inter = overlap_end - overlap_start
                if inter / c_dur >= overlap_threshold:
                    has_overlap = True
                    break

        if not has_overlap:
            kept.append(c)

    return sorted(kept, key=lambda c: c.start)


def run_refine(
    cache: CacheManager,
    transcript: FullTranscript,
    clips: List[ClipItem],
    min_length: float = 15.0,
    max_length: float = 60.0,
) -> List[ClipItem]:
    """Refine boundaries, deduplicate, and validate clips."""
    refined = []
    for c in clips:
        snapped = snap_boundaries_to_words(c, transcript, min_length=min_length, max_length=max_length)
        refined.append(snapped)

    deduped = deduplicate_clips(refined, overlap_threshold=0.30)

    # Renumber IDs
    for idx, c in enumerate(deduped):
        c.id = f"c{idx + 1:02d}"

    cache.save_json("refined_clips.json", [c.model_dump() for c in deduped])
    return deduped
