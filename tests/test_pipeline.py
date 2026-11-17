"""Unit tests for ClipIt models, refine, deduplication, and captions."""

from pathlib import Path
from clipit.models import FullTranscript, TranscriptSegment, WordTimestamp, ClipItem, CandidateWindow
from clipit.stages.refine import deduplicate_clips, snap_boundaries_to_words
from clipit.stages.captions import generate_clip_ass, format_ass_time


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


def test_emoji_sanitization():
    from clipit.stages.captions import strip_unsupported_ass_chars
    raw = "Oh my god! I'm in the game! 🎮🔥💀"
    cleaned = strip_unsupported_ass_chars(raw)
    assert cleaned == "Oh my god! I'm in the game!"
    assert "🎮" not in cleaned
    assert "🔥" not in cleaned


def test_stack_filter_generation():
    from clipit.stages.layout import build_reference_stack_filter, get_layout_filter
    vision_info = {
        "streamer_box": [500, 0, 1000, 400],
        "content_focus": [100, 500, 800, 950],
        "content_alignment": "right",
        "streamer_pct": 38,
        "content_pct": 62,
    }
    filter_graph, seam_y = build_reference_stack_filter(vision_info, source_w=1920, source_h=1080)
    assert "[top]" in filter_graph
    assert "[bottom]" in filter_graph
    assert "vstack=inputs=2[base]" in filter_graph
    assert seam_y == int(1920 * 0.38) - (int(1920 * 0.38) % 2)

    # Test get_layout_filter with stack layout
    graph2, seam2 = get_layout_filter("stack", vision_info=vision_info)
    assert graph2 == filter_graph
    assert seam2 == seam_y

    # Test custom streamer_box positioning (e.g. Speed's Bottom-Left webcam vs Top-Right)
    vision_speed_bl = {
        "streamer_box": [400, 0, 950, 280],  # Bottom-Left
        "content_focus_x": 650,               # Panned to right content
        "streamer_pct": 38,
        "has_facecam": True,
    }
    graph_bl, seam_bl = build_reference_stack_filter(vision_speed_bl, source_w=1920, source_h=1080)
    assert "[top]" in graph_bl
    assert "[bottom]" in graph_bl
    # Top tier must crop near left (x=0) where streamer webcam is located
    assert "crop=" in graph_bl
    # Verify that content_focus_x (650) did not force the top tier facecam to the right
    top_part = graph_bl.split(";")[0]
    assert ":0:" in top_part or ":10:" in top_part or ":20:" in top_part  # crop_x is at or near 0 on left


def test_variant_filter_generation():
    from clipit.stages.layout import (
        build_blur_fill_filter,
        build_reframed_crop_filter,
        get_layout_filter,
    )
    vision_info = {
        "has_facecam": True,
        "streamer_box": [500, 0, 1000, 400],
        "content_focus": [100, 500, 800, 950],
        "content_letterbox": [108, 948],
    }

    # Blur fill (Letterbox / Black canvas reference)
    letterbox_graph = build_blur_fill_filter(vision_info, source_w=1920, source_h=1080, background="black")
    assert "pad=1080:1920:0:(1920-ih)/2:color=black[base]" in letterbox_graph

    # Blur fill (dimmed background blur mode)
    blur_graph = build_blur_fill_filter(vision_info, source_w=1920, source_h=1080, background="blur")
    assert "boxblur=" in blur_graph
    assert "[bg][fg]overlay=" in blur_graph

    # Reframed 3:4 crop (Default reference letterbox style)
    crop_graph = build_reframed_crop_filter(vision_info, source_w=1920, source_h=1080)
    assert "scale=1080:1440" in crop_graph
    assert "pad=1080:1920:0:240:color=black[base]" in crop_graph
    assert "crop=" in crop_graph

    # Reframed 9:16 full-bleed crop
    v_916 = {**vision_info, "content_ratio": "9:16"}
    crop_916 = build_reframed_crop_filter(v_916, source_w=1920, source_h=1080)
    assert "scale=1080:1920[base]" in crop_916

    # Reframed 1:1 square crop
    v_11 = {**vision_info, "content_ratio": "1:1"}
    crop_11 = build_reframed_crop_filter(v_11, source_w=1920, source_h=1080)
    assert "scale=1080:1080" in crop_11
    assert "pad=1080:1920:0:420:color=black[base]" in crop_11

    # Reframed 4:5 portrait crop
    v_45 = {**vision_info, "content_ratio": "4:5"}
    crop_45 = build_reframed_crop_filter(v_45, source_w=1920, source_h=1080)
    assert "scale=1080:1352" in crop_45
    assert "pad=1080:1920:0:284:color=black[base]" in crop_45

    # get_layout_filter tests
    g_blur, s_blur = get_layout_filter("blur-fill", vision_info=vision_info)
    assert s_blur is None
    assert "pad=1080:1920:0:(1920-ih)/2:color=black[base]" in g_blur

    g_crop, s_crop = get_layout_filter("reframed", vision_info=vision_info)
    assert s_crop is None
    assert "scale=1080:1440" in g_crop


def test_model_chain_ordering():
    from clipit.llm.selector_api import DEFAULT_MODEL_CHAIN
    from clipit.stages.vision_reframing import FALLBACK_MODELS

    expected_order = [
        "gemini-3.1-flash-lite",  # 15 RPM, 500 RPD (fastest & highest free-tier quota)
        "gemini-3.5-flash-lite",  # 15 RPM, 500 RPD
        "gemma-4-26b-a4b-it",     # 30 RPM, 14.4K RPD
        "gemini-3.8-flash",       # 5 RPM, 20 RPD
        "gemini-3.6-flash",       # 5 RPM, 20 RPD
        "gemini-3-flash",
        "gemini-2.5-flash",
    ]
    assert DEFAULT_MODEL_CHAIN == expected_order
    assert FALLBACK_MODELS == expected_order


def test_bouncing_caption_scale_tags(tmp_path: Path):
    clip = ClipItem(
        id="c_bounce",
        start=0.0,
        end=5.0,
        score=95,
        title="Bounce Test",
        hook_line="LOOK AT THIS",
        why_viral="Explosive moment with high energy reaction from streamer that drives strong viewer retention.",
    )
    words = [
        WordTimestamp(w="LOOK", start=0.5, end=1.0),
        WordTimestamp(w="AT", start=1.1, end=1.5),
        WordTimestamp(w="THIS", start=1.6, end=2.0),
    ]
    seg = TranscriptSegment(start=0.0, end=3.0, text="LOOK AT THIS", words=words)
    transcript = FullTranscript(video_id="test", duration=10.0, language="en", segments=[seg])

    out_ass = tmp_path / "bounce.ass"
    generate_clip_ass(clip, transcript, out_ass)

    content = out_ass.read_text()
    # Check that dynamic spring bounce scale tags are present
    assert "\\fscx126\\fscy126" in content
    assert "\\fscx100\\fscy100" in content


def test_quality_sentinel(tmp_path: Path):
    from clipit.cache import CacheManager
    from clipit.stages.review import run_quality_sentinel

    cache = CacheManager(base_dir=tmp_path, video_id="test_vid")
    clip = ClipItem(
        id="c01",
        start=10.0,
        end=35.0,
        score=92,
        title="Epic GTA 6 Moment",
        hook_line="I CANNOT BELIEVE THIS",
        why_viral="Intense emotional reaction with instant hook that keeps viewer attention.",
    )

    report = run_quality_sentinel([clip], cache)
    assert report["overall_pass"] is True
    assert len(report["audits"]) == 1
    assert report["audits"][0]["verdict"] == "PASS"
    assert report["audits"][0]["duration_valid"] is True
    assert report["audits"][0]["has_hook"] is True


def test_delete_rejected_clips(tmp_path: Path):
    import json
    from clipit.retention import delete_rejected_clips

    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "out"
    ready_dir = tmp_path / "ready"
    vid = "test_vid_del"

    c_dir = cache_dir / vid
    o_dir = out_dir / vid
    c_dir.mkdir(parents=True)
    o_dir.mkdir(parents=True)

    # Setup clips in out
    (o_dir / "c01.mp4").write_text("dummy-c01")
    (o_dir / "c02.mp4").write_text("dummy-c02-rejected")
    (o_dir / "c02_blur.mp4").write_text("dummy-c02-blur")

    # Setup manifests
    (c_dir / "approved.json").write_text(json.dumps({"c01": "approved", "c02": "rejected"}))
    (c_dir / "refined_clips.json").write_text(json.dumps([
        {"id": "c01", "start": 0.0, "end": 10.0, "score": 90, "title": "C1", "hook_line": "H1", "why_viral": "Viral moment one here."},
        {"id": "c02", "start": 15.0, "end": 25.0, "score": 70, "title": "C2", "hook_line": "H2", "why_viral": "Viral moment two here."},
    ]))

    res = delete_rejected_clips(vid, cache_base=cache_dir, out_base=out_dir, ready_base=ready_dir)

    assert res["success"] is True
    assert res["deleted_count"] == 1
    assert "c02" in res["deleted_clip_ids"]
    assert not (o_dir / "c02.mp4").exists()
    assert not (o_dir / "c02_blur.mp4").exists()
    assert (o_dir / "c01.mp4").exists()

    with open(c_dir / "approved.json") as f:
        app = json.load(f)
    assert "c02" not in app
    assert app.get("c01") == "approved"

    with open(c_dir / "refined_clips.json") as f:
        ref = json.load(f)
    assert len(ref) == 1
    assert ref[0]["id"] == "c01"


def test_retention_policy_30_days(tmp_path: Path):
    import json
    import time
    from clipit.retention import enforce_retention_policy

    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "out"

    # 1. Fresh project (1 day old)
    fresh_vid = "fresh_proj"
    f_cache = cache_dir / fresh_vid
    f_out = out_dir / fresh_vid
    f_cache.mkdir(parents=True)
    f_out.mkdir(parents=True)
    (f_cache / "info.json").write_text(json.dumps({"created_at": time.time() - (1 * 86400)}))
    (f_cache / "source.mp4").write_text("fresh-source")
    (f_out / "c01.mp4").write_text("fresh-clip")

    # 2. Expired project (35 days old)
    old_vid = "old_proj"
    o_cache = cache_dir / old_vid
    o_out = out_dir / old_vid
    o_cache.mkdir(parents=True)
    o_out.mkdir(parents=True)
    (o_cache / "info.json").write_text(json.dumps({"created_at": time.time() - (35 * 86400)}))
    (o_cache / "source.mp4").write_text("old-source-to-delete")
    (o_cache / "audio.wav").write_text("old-audio-to-delete")
    (o_out / "c01.mp4").write_text("old-clip-to-delete")

    res = enforce_retention_policy(cache_base=cache_dir, out_base=out_dir, max_age_days=30)

    assert res["scanned_projects"] == 2
    assert res["purged_count"] == 1
    assert res["purged_projects"][0]["video_id"] == old_vid

    # Verify old source and clips are deleted
    assert not (o_cache / "source.mp4").exists()
    assert not (o_cache / "audio.wav").exists()
    assert not (o_out / "c01.mp4").exists()

    # Verify fresh project remains intact
    assert (f_cache / "source.mp4").exists()
    assert (f_out / "c01.mp4").exists()


def test_storage_breakdown_and_bulk_deletion(tmp_path):
    import json
    from clipit.retention import get_storage_breakdown, delete_all_clips, delete_source_vod

    cache_dir = tmp_path / "cache"
    out_dir = tmp_path / "out"
    ready_dir = tmp_path / "ready_to_post"
    vid = "test_bulk_vid"

    p_cache = cache_dir / vid
    p_out = out_dir / vid
    p_cache.mkdir(parents=True)
    p_out.mkdir(parents=True)

    (p_cache / "source.mp4").write_text("bulk-source-data-12345")
    (p_cache / "audio.wav").write_text("bulk-audio-data-67890")
    (p_cache / "info.json").write_text(json.dumps({"title": "Bulk Test Video"}))
    (p_cache / "approved.json").write_text(json.dumps({"c01": "approved", "c02": "pending"}))
    (p_cache / "refined_clips.json").write_text(json.dumps([{"id": "c01", "title": "Clip One"}, {"id": "c02", "title": "Clip Two"}]))

    (p_out / "c01.mp4").write_text("c01-video")
    (p_out / "c01_blur.mp4").write_text("c01-blur-video")
    (p_out / "c02.mp4").write_text("c02-video")

    # 1. Test get_storage_breakdown
    details = get_storage_breakdown(cache_base=cache_dir, out_base=out_dir, ready_base=ready_dir)
    assert details["success"] is True
    assert details["total_projects"] == 1
    assert details["total_clips"] == 2
    proj = details["projects"][0]
    assert proj["video_id"] == vid
    assert proj["title"] == "Bulk Test Video"
    assert proj["has_source"] is True
    assert len(proj["clips"]) == 2

    # 2. Test delete_source_vod
    src_res = delete_source_vod(vid, cache_base=cache_dir)
    assert src_res["success"] is True
    assert "source.mp4" in src_res["deleted_files"]
    assert "audio.wav" in src_res["deleted_files"]
    assert not (p_cache / "source.mp4").exists()
    assert not (p_cache / "audio.wav").exists()
    # Clips should still exist
    assert (p_out / "c01.mp4").exists()

    # 3. Test delete_all_clips
    del_res = delete_all_clips(vid, cache_base=cache_dir, out_base=out_dir, ready_base=ready_dir)
    assert del_res["success"] is True
    assert del_res["deleted_clips_count"] == 3
    assert not (p_out / "c01.mp4").exists()
    assert not (p_out / "c02.mp4").exists()




