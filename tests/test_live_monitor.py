"""Unit tests for Continuous Live Stream Clipper, overlap windowing, boundary deduplication, and sentinel state."""

import json
from pathlib import Path
import pytest
from clipit.models import ClipItem
from clipit.stages.live_monitor import (
    format_seconds,
    parse_time_str,
    LiveStreamMonitor,
)


def test_time_formatting_and_parsing():
    assert format_seconds(0) == "00:00:00"
    assert format_seconds(1800) == "00:30:00"
    assert format_seconds(3665) == "01:01:05"

    assert parse_time_str("00:30:00") == 1800.0
    assert parse_time_str("01:00:00") == 3600.0
    assert parse_time_str("15:30") == 930.0


def test_live_monitor_boundary_overlap_and_deduplication(tmp_path):
    cache_base = tmp_path / "cache"
    out_base = tmp_path / "out"

    monitor = LiveStreamMonitor(
        url="https://www.youtube.com/watch?v=TEST_LIVE_STREAM",
        chunk_mins=30,
        overlap_mins=5,
        target_clips=5,
        cache_base=cache_base,
        out_base=out_base,
    )

    # 1. Simulate Part 1 existing clips
    p1_clips = [
        {
            "id": "pt01_c01",
            "title": "Part 1 Early Viral Moment",
            "start": 300.0,
            "end": 340.0,
            "abs_start": 300.0,
            "abs_end": 340.0,
        },
        {
            "id": "pt01_c02",
            "title": "Part 1 Boundary Pre-Overlap Moment",
            "start": 1600.0,
            "end": 1640.0,
            "abs_start": 1600.0,
            "abs_end": 1640.0,
        },
    ]

    monitor.state["parts"] = [
        {
            "part_index": 1,
            "name": "Part 1",
            "start_sec": 0.0,
            "end_sec": 1800.0,
            "clips": p1_clips,
        }
    ]
    monitor.state["all_clips"] = list(p1_clips)
    monitor.state["last_edge_sec"] = 1800.0
    monitor.save_session()

    # Part 2 covers 1500s (1800 - 300 overlap) to 3600s
    part2_start_sec = 1500.0

    # Simulate Candidate clips found in Part 2:
    # - Clip A: relative 100s..140s -> abs 1600s..1640s (DUPLICATE of pt01_c02!)
    # - Clip B: relative 280s..330s -> abs 1780s..1830s (SPANS BOUNDARY! Part 1 ended at 1800s, so this is new/complete!)
    # - Clip C: relative 600s..640s -> abs 2100s..2140s (FRESH content in Part 2!)
    candidate_clips = [
        ClipItem(id="c01", start=100.0, end=140.0, score=85, title="Duplicate in overlap", hook_line="Overlap dup", why_viral="test viral explanation long enough here"),
        ClipItem(id="c02", start=280.0, end=330.0, score=92, title="Spans 30-min boundary", hook_line="Boundary moment", why_viral="test viral explanation long enough here"),
        ClipItem(id="c03", start=600.0, end=640.0, score=88, title="Brand new part 2 scene", hook_line="New scene", why_viral="test viral explanation long enough here"),
    ]

    deduped = monitor.deduplicate_clips(candidate_clips, chunk_start_sec=part2_start_sec)

    # Clip A should be dropped as duplicate
    # Clip B and Clip C should be kept!
    assert len(deduped) == 2
    titles = [c.title for c in deduped]
    assert "Duplicate in overlap" not in titles
    assert "Spans 30-min boundary" in titles
    assert "Brand new part 2 scene" in titles


def test_session_persistence_and_reload(tmp_path):
    cache_base = tmp_path / "cache"
    out_base = tmp_path / "out"

    m1 = LiveStreamMonitor(
        url="https://www.youtube.com/watch?v=TEST_RELOAD",
        chunk_mins=30,
        overlap_mins=5,
        cache_base=cache_base,
        out_base=out_base,
    )
    m1.state["last_edge_sec"] = 3600.0
    m1.state["parts"].append({"part_index": 1, "name": "Part 1"})
    m1.save_session()

    # Re-instantiate should reload persistent state
    m2 = LiveStreamMonitor(
        url="https://www.youtube.com/watch?v=TEST_RELOAD",
        chunk_mins=30,
        overlap_mins=5,
        cache_base=cache_base,
        out_base=out_base,
    )
    assert m2.state["last_edge_sec"] == 3600.0
    assert len(m2.state["parts"]) == 1
    assert m2.state["parts"][0]["name"] == "Part 1"
