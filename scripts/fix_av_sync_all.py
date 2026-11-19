#!/usr/bin/env python3
"""
Batch stream-copy remux all rendered clips to fix the 21ms video/audio start offset.
No re-encoding — pure lossless container remux at ~2000x speed.

Usage: python3 scripts/fix_av_sync_all.py
"""
import subprocess
import json
import sys
import time
from pathlib import Path

OUT_ROOT = Path("out/yt_qSyo5d-dVzA_overnight")
THRESHOLD = 0.005  # only fix if offset > 5ms

METADATA_SCRUB = [
    "-map_metadata", "-1",
    "-map_chapters", "-1",
    "-map_metadata:s:v", "-1",
    "-map_metadata:s:a", "-1",
]


def get_av_offset(mp4: Path) -> float:
    """Return video_start_time - audio_start_time in seconds."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(mp4)],
            capture_output=True, text=True, timeout=10
        )
        if res.returncode != 0:
            return 0.0
        data = json.loads(res.stdout)
        v_st = a_st = 0.0
        for s in data.get("streams", []):
            try:
                if s.get("codec_type") == "video":
                    v_st = float(s.get("start_time", 0))
                elif s.get("codec_type") == "audio":
                    a_st = float(s.get("start_time", 0))
            except (ValueError, TypeError):
                pass
        return v_st - a_st
    except Exception:
        return 0.0


def fix_offset(mp4: Path, offset: float) -> bool:
    """Stream-copy remux to align audio with video start. Returns True on success."""
    tmp = mp4.with_suffix(".tmp_sync.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(mp4),
        "-itsoffset", f"{offset:.6f}",
        "-i", str(mp4),
        "-map", "0:v", "-map", "1:a",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
    ] + METADATA_SCRUB + [str(tmp)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 100_000:
            tmp.replace(mp4)
            return True
        else:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            print(f"    ⚠️  remux failed for {mp4.name}: {result.stderr[-200:]}")
            return False
    except Exception as e:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        print(f"    ❌ exception for {mp4.name}: {e}")
        return False


def main():
    # Collect all mp4 clips (all variants: reaction, blur, reframed)
    all_mp4s = []
    for part_dir in sorted(OUT_ROOT.glob("part_*")):
        for mp4 in sorted(part_dir.glob("pt*_c*.mp4")):
            all_mp4s.append(mp4)
    # Also check top-level (non-part) files
    for mp4 in sorted(OUT_ROOT.glob("pt*_c*.mp4")):
        all_mp4s.append(mp4)

    print(f"Found {len(all_mp4s)} clip files to check")
    total = len(all_mp4s)
    fixed = 0
    skipped = 0
    failed = 0
    start_t = time.time()

    for i, mp4 in enumerate(all_mp4s, 1):
        offset = get_av_offset(mp4)
        tag = mp4.relative_to(OUT_ROOT)
        if abs(offset) < THRESHOLD:
            skipped += 1
            print(f"[{i}/{total}] ✅ {tag}  (already synced, offset={offset*1000:.1f}ms)")
            continue

        print(f"[{i}/{total}] 🔧 {tag}  offset={offset*1000:.1f}ms → fixing...", end=" ", flush=True)
        ok = fix_offset(mp4, offset)
        if ok:
            # Verify
            new_offset = get_av_offset(mp4)
            if abs(new_offset) < THRESHOLD:
                fixed += 1
                print(f"✅ fixed (now {new_offset*1000:.2f}ms)")
            else:
                failed += 1
                print(f"⚠️  still {new_offset*1000:.2f}ms after fix")
        else:
            failed += 1

    elapsed = time.time() - start_t
    print(f"\n{'='*60}")
    print(f"Done in {elapsed:.1f}s")
    print(f"  Fixed:   {fixed}")
    print(f"  Skipped: {skipped} (already synced)")
    print(f"  Failed:  {failed}")
    if failed == 0:
        print("✅ ALL CLIPS SYNCED")
    else:
        print(f"⚠️  {failed} files could not be fixed — check output above")
        sys.exit(1)


if __name__ == "__main__":
    main()
