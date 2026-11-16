"""Unit tests for ClipForge models, refine, deduplication, and captions."""

from pathlib import Path
from clipforge.models import FullTranscript, TranscriptSegment, WordTimestamp, ClipItem, CandidateWindow
from clipforge.stages.refine import deduplicate_clips, snap_boundaries_to_words
from clipforge.stages.captions import generate_clip_ass, format_ass_time


def test_ass_time_formatting():
    assert format_ass_time(0.0) == "0:00:00.00"
    assert format_ass_time(65.5) == "0:01:05.50"
    assert format_ass_time(3600.0) == "1:00:00.00"


def test_deduplication():
    c1 = ClipItem(
        id="c1",
        start=10.0,
        end=30.0,
        score=95,
        title="Clip 1",
        hook_line="Hook 1",
        why_viral="Explosive moment with high energy reaction from streamer.",
    )
    # Overlaps significantly with c1, but lower score
    c2 = ClipItem(
        id="c2",
        start=15.0,
        end=35.0,
        score=80,
        title="Clip 2",
        hook_line="Hook 2",
        why_viral="Secondary reaction from the same game section.",
    )
    # Disjoint clip
    c3 = ClipItem(
        id="c3",
        start=60.0,
        end=80.0,
        score=90,
        title="Clip 3",
        hook_line="Hook 3",
        why_viral="Clutch gaming play that surprised viewers.",
    )

    deduped = deduplicate_clips([c1, c2, c3], overlap_threshold=0.30)
    ids = [c.id for c in deduped]
    assert "c1" in ids
    assert "c2" not in ids
    assert "c3" in ids


def test_caption_ass_generation(tmp_path: Path):
    clip = ClipItem(
        id="test_01",
        start=5.0,
        end=15.0,
        score=90,
        title="Test Title",
        hook_line="Test Hook",
        why_viral="This clip has rapid pacing and great comedic payoff.",
    )

    words = [
        WordTimestamp(w="Hello", start=5.5, end=6.0),
        WordTimestamp(w="Vice", start=6.1, end=6.5),
        WordTimestamp(w="City", start=6.6, end=7.2),
    ]
    seg = TranscriptSegment(start=5.0, end=8.0, text="Hello Vice City", words=words)
    transcript = FullTranscript(video_id="test", duration=20.0, language="en", segments=[seg])

    out_ass = tmp_path / "test.ass"
    generate_clip_ass(clip, transcript, out_ass)

    assert out_ass.exists()
    content = out_ass.read_text()
    assert "[Script Info]" in content
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "VICE" in content or "Vice" in content
