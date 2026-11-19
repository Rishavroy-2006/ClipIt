"""Systematic ground-truth synchronization and re-renderer for any Part.

Extracts pristine 16:9 footage, aligns transcript words with microsecond audio accuracy
using local Metal/CUDA Whisper, applies smart phrase chunking with smooth non-flickering
continuous highlights, and renders broadcast-quality 9:16 vertical Shorts.
"""

import sys
import os
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import shutil
import subprocess

from clipit.models import ClipItem, FullTranscript
from clipit.stages.aligner import align_transcript_to_clip_audio
from clipit.stages.render import render_single_clip
from clipit.config import load_config
from clipit.cache import CacheManager
from scripts.audit_all_parts import audit_clip


def sync_part(part_num: int, video_id: str = "yt_qSyo5d-dVzA_overnight"):
    out_root = Path("out") / video_id
    part_dir = out_root / f"part_{part_num:02d}"
    if not part_dir.exists():
        part_dir = out_root / f"part_{part_num}"
    if not part_dir.exists():
        print(f"Error: {part_dir} does not exist.")
        return

    cache_name = f"{video_id}_pt{part_num:02d}"
    cache_dir = Path("cache") / cache_name
    if not cache_dir.exists():
        cache_name = f"{video_id}_pt{part_num}"
        cache_dir = Path("cache") / cache_name
    if not cache_dir.exists():
        cache_name = video_id
        cache_dir = Path("cache") / video_id

    cache = CacheManager(cache_name)
    t_data = cache.load_json("transcript.json")
    if not t_data and cache_name != video_id:
        root_cache = CacheManager(video_id)
        t_data = root_cache.load_json("transcript.json")
    orig_transcript = FullTranscript(**t_data) if t_data else None

    cfg = load_config()

    # Load live_session to ensure original timestamps are accurate
    ls_path = out_root / "live_session.json"
    ls_clips = {}
    if ls_path.exists():
        try:
            ls_data = json.loads(ls_path.read_text())
            for part in ls_data.get("parts", []):
                for c in part.get("clips", []):
                    ls_clips[c["id"]] = c
        except Exception:
            pass

    # Find sidecar json files for clips in this part
    sidecars = sorted(part_dir.glob("pt*.json"))
    if not sidecars:
        print(f"No clip sidecars found in {part_dir}")
        return

    print(f"\n================ SYNCING PART {part_num:02d} ({len(sidecars)} clips) ================")
    for sc in sidecars:
        clip_dict = json.loads(sc.read_text())
        clip_id = clip_dict["id"]
        if clip_id in ls_clips and (clip_dict.get("start", 0.0) == 0.0 or clip_dict.get("end", 0.0) <= clip_dict.get("start", 0.0)):
            clip_dict["start"] = ls_clips[clip_id]["start"]
            clip_dict["end"] = ls_clips[clip_id]["end"]

        orig_clip = ClipItem(**clip_dict)
        orig_dur = max(0.1, orig_clip.end - orig_clip.start)

        blur_mp4 = part_dir / f"{clip_id}_blur.mp4"
        if not blur_mp4.exists():
            blur_mp4 = out_root / f"{clip_id}_blur.mp4"

        clip_src = cache.path(f"clip_source_{clip_id}.mp4")

        # 1. Extract pristine 16:9 source with video advance
        raw_source = cache_dir / "source.mp4"
        force_reextract = True  # Guarantee video advance is applied to eliminate any legacy video lag

        if raw_source.exists():
            print(f"[{clip_id}] Extracting directly from raw 16:9 source.mp4 (start={orig_clip.start:.2f}s, dur={orig_dur:.2f}s)...")
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{orig_clip.start:.3f}",
                "-i", str(raw_source),
                "-t", f"{orig_dur:.3f}",
                "-c:v", "h264_videotoolbox", "-b:v", "8000k",
                "-c:a", "aac", "-b:a", "192k",
                "-avoid_negative_ts", "make_zero",
                str(clip_src)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                cmd[cmd.index("h264_videotoolbox")] = "libx264"
                subprocess.run(cmd, check=True, capture_output=True)
        elif blur_mp4.exists() and (force_reextract or not clip_src.exists()):
            print(f"[{clip_id}] Extracting clean 16:9 stream footage from {blur_mp4.name} with 0.50s video advance...")
            # Shift video earlier by 0.50s relative to audio to permanently eliminate legacy video delay
            cmd = [
                "ffmpeg", "-y",
                "-ss", "0.50", "-i", str(blur_mp4),
                "-ss", "0.00", "-i", str(blur_mp4),
                "-filter_complex", "[0:v]crop=1080:608:0:656,scale=1920:1080[v]",
                "-map", "[v]", "-map", "1:a",
                "-c:v", "h264_videotoolbox", "-b:v", "8000k",
                "-c:a", "copy",
                "-avoid_negative_ts", "make_zero",
                str(clip_src)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                cmd[cmd.index("h264_videotoolbox")] = "libx264"
                subprocess.run(cmd, check=True, capture_output=True)
        elif not clip_src.exists():
            print(f"[{clip_id}] Skipped: no blur_mp4 or source available.")
            continue

        # 2. Ground-truth alignment on clip_src audio
        print(f"[{clip_id}] Aligning transcript to ground-truth audio...")
        aligned_transcript = align_transcript_to_clip_audio(
            media_path=clip_src,
            orig_transcript=orig_transcript,
            clip_start=orig_clip.start,
            clip_end=orig_clip.end,
            clip_dur=orig_dur
        )

        # 3. Render all 3 vertical layouts from clip_src
        clip_for_render = ClipItem(**clip_dict)
        clip_for_render.start = 0.0
        clip_for_render.end = orig_dur

        print(f"[{clip_id}] Rendering 3 vertical layouts via VideoToolbox Metal hardware encoder...")
        render_single_clip(
            clip=clip_for_render,
            source_video=clip_src,
            output_dir=part_dir,
            transcript=aligned_transcript,
            cache=cache,
            video_config=cfg.video,
            codec="h264_videotoolbox",
            render_variants=True,
            force=True
        )

        # 4. Copy newly generated ASS files from cache to part_dir and out_root
        for ext in (".ass", "_blur-fill.ass", "_reframed.ass"):
            c_ass = cache.path(f"{clip_id}{ext}")
            if c_ass.exists():
                shutil.copy2(c_ass, part_dir / f"{clip_id}{ext}")
                shutil.copy2(c_ass, out_root / f"{clip_id}{ext}")

        # Synchronize top-level out_root with part_dir for MP4 variants
        for ext in (".mp4", "_blur.mp4", "_reframed.mp4"):
            src_f = part_dir / f"{clip_id}{ext}"
            dst_f = out_root / f"{clip_id}{ext}"
            if src_f.exists():
                shutil.copy2(src_f, dst_f)

        # 5. Restore original metadata in sidecar
        new_sc = json.loads((part_dir / f"{clip_id}.json").read_text())
        clip_dict["variants"] = new_sc.get("variants", clip_dict.get("variants", []))
        clip_dict["start"] = orig_clip.start
        clip_dict["end"] = orig_clip.end
        with open(part_dir / f"{clip_id}.json", "w", encoding="utf-8") as f:
            json.dump(clip_dict, f, indent=2, ensure_ascii=False)
        shutil.copy2(part_dir / f"{clip_id}.json", out_root / f"{clip_id}.json")

        # 6. Verify with audit_clip
        rendered_mp4 = part_dir / f"{clip_id}.mp4"
        ass_file = part_dir / f"{clip_id}.ass"
        med, msg = audit_clip(rendered_mp4, ass_file)
        status = "✅ SYNCED" if med is not None and abs(med) <= 0.10 else "❌ OUT OF SYNC"
        print(f"[{clip_id}] Result: {status} ({msg})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/sync_part.py <part_num>")
        sys.exit(1)
    p_num = int(sys.argv[1])
    sync_part(p_num)
