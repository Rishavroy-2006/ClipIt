"""Retention and clip deletion lifecycle manager for ClipIt.

Provides:
1. Permanent deletion of rejected clips and their rendered video assets.
2. 30-day automated lifecycle cleanup for source VODs and rendered clips starting from creation date.
"""

import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional


def get_project_creation_timestamp(video_id: str, cache_base: Path = Path("cache"), out_base: Path = Path("out")) -> float:
    """Return project creation timestamp in epoch seconds from info.json or file metadata."""
    cache_dir = cache_base / video_id
    info_file = cache_dir / "info.json"

    if info_file.exists():
        try:
            with open(info_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "created_at" in data and isinstance(data["created_at"], (int, float)):
                    return float(data["created_at"])
        except Exception:
            pass

    # Fallback to filesystem creation or modification timestamp
    candidates = []
    if info_file.exists():
        candidates.append(info_file.stat().st_mtime)
        candidates.append(info_file.stat().st_ctime)

    source_file = cache_dir / "source.mp4"
    if source_file.exists():
        candidates.append(source_file.stat().st_mtime)

    out_dir = out_base / video_id
    if out_dir.exists():
        candidates.append(out_dir.stat().st_mtime)

    if candidates:
        return min(candidates)

    return time.time()


def get_project_age_days(video_id: str, cache_base: Path = Path("cache"), out_base: Path = Path("out")) -> float:
    """Return age of project in fractional days since creation."""
    created_at = get_project_creation_timestamp(video_id, cache_base, out_base)
    return max(0.0, (time.time() - created_at) / 86400.0)


def enforce_retention_policy(
    cache_base: Path = Path("cache"),
    out_base: Path = Path("out"),
    max_age_days: int = 30,
    clean_source: bool = True,
    clean_clips: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Scan all projects and delete source VODs and rendered clips exceeding the 30-day lifecycle."""
    now = time.time()
    cache_projects = {p.name for p in cache_base.iterdir() if p.is_dir()} if cache_base.exists() else set()
    out_projects = {p.name for p in out_base.iterdir() if p.is_dir()} if out_base.exists() else set()
    all_projects = sorted(cache_projects | out_projects)

    scanned = len(all_projects)
    purged_projects = []
    total_freed_bytes = 0

    for vid in all_projects:
        created_at = get_project_creation_timestamp(vid, cache_base, out_base)
        age_days = (now - created_at) / 86400.0

        if age_days < max_age_days:
            continue

        proj_freed = 0
        deleted_files = []

        # 1. Clean source VOD assets in cache/<vid>/
        if clean_source:
            cache_dir = cache_base / vid
            for heavy_name in ("source.mp4", "audio.wav"):
                f = cache_dir / heavy_name
                if f.exists():
                    sz = f.stat().st_size
                    proj_freed += sz
                    deleted_files.append(str(f))
                    if not dry_run:
                        try:
                            f.unlink()
                        except Exception as e:
                            print(f"[RETENTION] Error removing {f}: {e}")

        # 2. Clean rendered clips in out/<vid>/*.mp4
        if clean_clips:
            out_dir = out_base / vid
            if out_dir.exists():
                for mp4 in out_dir.glob("*.mp4"):
                    sz = mp4.stat().st_size
                    proj_freed += sz
                    deleted_files.append(str(mp4))
                    if not dry_run:
                        try:
                            mp4.unlink()
                        except Exception as e:
                            print(f"[RETENTION] Error removing {mp4}: {e}")

        if deleted_files or proj_freed > 0:
            total_freed_bytes += proj_freed
            created_dt = datetime.fromtimestamp(created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            purged_projects.append({
                "video_id": vid,
                "age_days": round(age_days, 1),
                "created_at": created_dt,
                "freed_bytes": proj_freed,
                "freed_mb": round(proj_freed / (1024 * 1024), 2),
                "deleted_files_count": len(deleted_files),
            })

            # Mark metadata as retention purged
            info_p = cache_base / vid / "info.json"
            if info_p.exists() and not dry_run:
                try:
                    with open(info_p, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    meta["retention_purged"] = True
                    meta["retention_purged_at"] = now
                    with open(info_p, "w", encoding="utf-8") as f:
                        json.dump(meta, f, indent=2)
                except Exception:
                    pass

    return {
        "scanned_projects": scanned,
        "max_age_days": max_age_days,
        "purged_count": len(purged_projects),
        "freed_bytes": total_freed_bytes,
        "freed_gb": round(total_freed_bytes / (1024**3), 3),
        "purged_projects": purged_projects,
    }


def delete_single_clip(
    video_id: str,
    clip_id: str,
    cache_base: Path = Path("cache"),
    out_base: Path = Path("out"),
    ready_base: Path = Path("ready_to_post"),
) -> Dict[str, Any]:
    """Permanently delete a specific clip and all its rendered variants from disk and manifests."""
    out_dir = out_base / video_id
    cache_dir = cache_base / video_id
    ready_dir = ready_base / video_id

    deleted_files = []
    freed_bytes = 0

    # 1. Remove rendered video files and sidecars in out/<vid>/
    if out_dir.exists():
        for pattern in (f"{clip_id}.mp4", f"{clip_id}_*.mp4", f"{clip_id}.json"):
            for f in out_dir.glob(pattern):
                try:
                    freed_bytes += f.stat().st_size
                    f.unlink()
                    deleted_files.append(str(f))
                except Exception as e:
                    print(f"Error removing {f}: {e}")

    # 2. Remove subtitle and vision analysis files in cache/<vid>/
    if cache_dir.exists():
        for pattern in (f"{clip_id}.ass", f"{clip_id}_*.ass", f"vision_{clip_id}*"):
            for f in cache_dir.glob(pattern):
                try:
                    freed_bytes += f.stat().st_size
                    f.unlink()
                    deleted_files.append(str(f))
                except Exception as e:
                    print(f"Error removing {f}: {e}")

    # 3. Update approved.json map
    app_file = cache_dir / "approved.json"
    if app_file.exists():
        try:
            with open(app_file, "r", encoding="utf-8") as f:
                approved_map = json.load(f)
            if clip_id in approved_map:
                del approved_map[clip_id]
            with open(app_file, "w", encoding="utf-8") as f:
                json.dump(approved_map, f, indent=2)
        except Exception:
            pass

    # 4. Remove from refined_clips.json
    refined_file = cache_dir / "refined_clips.json"
    remaining_clips = []
    deleted_clip_meta = None
    if refined_file.exists():
        try:
            with open(refined_file, "r", encoding="utf-8") as f:
                clips = json.load(f)
            for c in clips:
                cid = c.get("id") or c.get("clip_id")
                if cid == clip_id:
                    deleted_clip_meta = c
                else:
                    remaining_clips.append(c)
            with open(refined_file, "w", encoding="utf-8") as f:
                json.dump(remaining_clips, f, indent=2)
        except Exception:
            pass

    # 5. Remove from clips.json if present
    clips_file = cache_dir / "clips.json"
    if clips_file.exists():
        try:
            with open(clips_file, "r", encoding="utf-8") as f:
                base_clips = json.load(f)
            filtered = [c for c in base_clips if (c.get("id") or c.get("clip_id")) != clip_id]
            with open(clips_file, "w", encoding="utf-8") as f:
                json.dump(filtered, f, indent=2)
        except Exception:
            pass

    # 6. If matching exports exist in ready_to_post, remove them
    if ready_dir.exists() and deleted_clip_meta:
        title = deleted_clip_meta.get("title", "")
        # Remove any non-alphanumeric for clean search
        clean_stem = "".join(c for c in title if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        if clean_stem and len(clean_stem) > 4:
            for f in ready_dir.glob(f"*{clean_stem}*.mp4"):
                try:
                    freed_bytes += f.stat().st_size
                    f.unlink()
                    deleted_files.append(str(f))
                except Exception:
                    pass

    # 7. Re-generate review.html if clips remain
    try:
        from clipit.stages.review import generate_review_html
        from clipit.models import ClipItem
        from clipit.cache import CacheManager
        c_mgr = CacheManager(video_id, base_dir=cache_base)
        valid_keys = ClipItem.model_fields.keys()
        pydantic_clips = []
        for c in remaining_clips:
            filtered_c = {k: v for k, v in c.items() if k in valid_keys}
            if not filtered_c.get("why_viral") or len(filtered_c["why_viral"].split()) < 5:
                filtered_c["why_viral"] = "High energy viral moment from stream."
            pydantic_clips.append(ClipItem(**filtered_c))
        generate_review_html(pydantic_clips, c_mgr, out_dir)
    except Exception as e:
        print(f"Notice: review.html update: {e}")

    return {
        "success": True,
        "clip_id": clip_id,
        "deleted_files_count": len(deleted_files),
        "freed_bytes": freed_bytes,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "remaining_count": len(remaining_clips),
    }


def delete_rejected_clips(
    video_id: str,
    cache_base: Path = Path("cache"),
    out_base: Path = Path("out"),
    ready_base: Path = Path("ready_to_post"),
) -> Dict[str, Any]:
    """Find all rejected clips in a project and delete them permanently."""
    cache_dir = cache_base / video_id
    app_file = cache_dir / "approved.json"

    rejected_ids = []
    if app_file.exists():
        try:
            with open(app_file, "r", encoding="utf-8") as f:
                approved_map = json.load(f)
            rejected_ids = [cid for cid, status in approved_map.items() if status == "rejected"]
        except Exception:
            rejected_ids = []

    total_freed = 0
    deleted_clips = []

    for cid in rejected_ids:
        res = delete_single_clip(video_id, cid, cache_base, out_base, ready_base)
        total_freed += res.get("freed_bytes", 0)
        deleted_clips.append(cid)

    # Get final remaining count
    refined_file = cache_dir / "refined_clips.json"
    rem_count = 0
    if refined_file.exists():
        try:
            with open(refined_file, "r", encoding="utf-8") as f:
                rem_count = len(json.load(f))
        except Exception:
            pass

    return {
        "success": True,
        "deleted_count": len(deleted_clips),
        "deleted_clip_ids": deleted_clips,
        "freed_bytes": total_freed,
        "freed_mb": round(total_freed / (1024 * 1024), 2),
        "remaining_count": rem_count,
    }
