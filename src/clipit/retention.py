"""Retention and clip deletion lifecycle manager for ClipIt.

Provides:
1. Permanent deletion of rejected clips and their rendered video assets.
2. 30-day automated lifecycle cleanup for source VODs and rendered clips starting from creation date.
"""

import os
import re
import shutil
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

    # Determine part number if clip_id references a part (e.g. pt05_c01)
    m_part = re.search(r"pt0*(\d+)", clip_id)
    part_num = int(m_part.group(1)) if m_part else None
    stripped_cid = re.sub(r"^pt\d+_", "", clip_id)

    # 1. Target output directories: out/<vid>/ AND all out/<vid>/part_*/
    target_out_dirs = [out_dir]
    if out_dir.exists():
        for pdir in out_dir.glob("part_*"):
            if pdir.is_dir():
                target_out_dirs.append(pdir)

    for od in target_out_dirs:
        patterns = [
            f"{clip_id}.mp4", f"{clip_id}_*.mp4", f"{clip_id}.json",
        ]
        if stripped_cid != clip_id:
            patterns.extend([
                f"{stripped_cid}.mp4", f"{stripped_cid}_*.mp4", f"{stripped_cid}.json",
            ])
        for pattern in patterns:
            for f in od.glob(pattern):
                try:
                    freed_bytes += f.stat().st_size
                    f.unlink()
                    deleted_files.append(str(f))
                except Exception as e:
                    print(f"Error removing {f}: {e}")

    # 2. Target cache directories: cache/<vid>/ AND all cache/<vid>_pt*/
    target_cache_dirs = [cache_dir]
    if cache_base.exists():
        for pdir in cache_base.glob(f"{video_id}_pt*"):
            if pdir.is_dir():
                target_cache_dirs.append(pdir)

    for cd in target_cache_dirs:
        patterns = [
            f"{clip_id}.ass", f"{clip_id}_*.ass", f"vision_{clip_id}*", f"{clip_id}.mp4", f"{clip_id}_*.mp4",
        ]
        if stripped_cid != clip_id:
            patterns.extend([
                f"{stripped_cid}.ass", f"{stripped_cid}_*.ass", f"vision_{stripped_cid}*", f"{stripped_cid}.mp4", f"{stripped_cid}_*.mp4",
            ])
        for pattern in patterns:
            for f in cd.glob(pattern):
                try:
                    freed_bytes += f.stat().st_size
                    f.unlink()
                    deleted_files.append(str(f))
                except Exception as e:
                    print(f"Error removing {f}: {e}")

    # 3. Update live_session.json in both cache and out
    for sess_file in (cache_dir / "live_session.json", out_dir / "live_session.json"):
        if sess_file.exists():
            try:
                with open(sess_file, "r", encoding="utf-8") as f:
                    sess_d = json.load(f)
                changed = False
                for pt in sess_d.get("parts", []):
                    pt_clips = pt.get("clips", [])
                    filtered_pt = [c for c in pt_clips if c.get("id") not in (clip_id, stripped_cid)]
                    if len(filtered_pt) != len(pt_clips):
                        pt["clips"] = filtered_pt
                        changed = True
                top_clips = sess_d.get("clips", [])
                filtered_top = [c for c in top_clips if c.get("id") not in (clip_id, stripped_cid)]
                if len(filtered_top) != len(top_clips):
                    sess_d["clips"] = filtered_top
                    changed = True
                if changed:
                    with open(sess_file, "w", encoding="utf-8") as f:
                        json.dump(sess_d, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

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
    delete_unapproved: bool = False,
) -> Dict[str, Any]:
    """Find all rejected clips in a project and delete them permanently."""
    cache_dir = cache_base / video_id
    out_dir = out_base / video_id
    app_file = cache_dir / "approved.json"

    approved_map = {}
    if app_file.exists():
        try:
            with open(app_file, "r", encoding="utf-8") as f:
                approved_map = json.load(f)
        except Exception:
            approved_map = {}

    rejected_ids = set([cid for cid, status in approved_map.items() if status == "rejected"])

    if delete_unapproved:
        # Discover all clip IDs across manifests and directories
        all_cids = set()
        if out_dir.exists():
            for p in out_dir.glob("*.json"):
                if p.name not in ("info.json", "live_session.json", "approved.json", "project.json", "review.json"):
                    all_cids.add(p.stem)
            for pdir in out_dir.glob("part_*"):
                if pdir.is_dir():
                    for p in pdir.glob("*.json"):
                        if p.name not in ("info.json", "live_session.json", "approved.json"):
                            all_cids.add(p.stem)
        for sess_file in (cache_dir / "live_session.json", out_dir / "live_session.json"):
            if sess_file.exists():
                try:
                    with open(sess_file, "r", encoding="utf-8") as f:
                        sess_d = json.load(f)
                        for pt in sess_d.get("parts", []):
                            for c in pt.get("clips", []):
                                if c.get("id"):
                                    all_cids.add(c["id"])
                        for c in sess_d.get("clips", []):
                            if c.get("id"):
                                all_cids.add(c["id"])
                except Exception:
                    pass

        for cid in all_cids:
            stripped = re.sub(r"^pt\d+_", "", cid)
            if approved_map.get(cid) != "approved" and approved_map.get(stripped) != "approved":
                rejected_ids.add(cid)

    total_freed = 0
    deleted_clips = []

    for cid in sorted(rejected_ids):
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


def get_storage_breakdown(
    cache_base: Path = Path("cache"),
    out_base: Path = Path("out"),
    ready_base: Path = Path("ready_to_post"),
) -> Dict[str, Any]:
    """Calculate detailed storage metrics for all projects, source VODs, and individual clips."""
    import shutil

    now = time.time()
    cache_vids = {p.name for p in cache_base.iterdir() if p.is_dir() and not p.name.startswith(".")} if cache_base.exists() else set()
    out_vids = {p.name for p in out_base.iterdir() if p.is_dir() and not p.name.startswith(".")} if out_base.exists() else set()
    all_vids = sorted(cache_vids | out_vids)

    projects_list = []
    grand_total_bytes = 0
    grand_total_clips = 0

    for vid in all_vids:
        c_dir = cache_base / vid
        o_dir = out_base / vid
        r_dir = ready_base / vid

        # Metadata
        title = vid
        info_file = c_dir / "info.json"
        created_at = get_project_creation_timestamp(vid, cache_base, out_base)
        age_days = (now - created_at) / 86400.0
        days_left = max(0.0, 30.0 - age_days)

        if info_file.exists():
            try:
                with open(info_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    title = meta.get("title", vid)
            except Exception:
                pass

        # 1. Source video size (cache/source.mp4 + audio.wav)
        source_bytes = 0
        source_files = []
        for sf in ("source.mp4", "audio.wav"):
            f = c_dir / sf
            if f.exists():
                sz = f.stat().st_size
                source_bytes += sz
                source_files.append({"name": sf, "size_mb": round(sz / (1024 * 1024), 2)})

        # 2. Approved map
        app_map = {}
        app_file = c_dir / "approved.json"
        if app_file.exists():
            try:
                with open(app_file, "r", encoding="utf-8") as f:
                    app_map = json.load(f)
            except Exception:
                pass

        # 3. Refined clips metadata
        refined_titles = {}
        refined_file = c_dir / "refined_clips.json"
        if refined_file.exists():
            try:
                with open(refined_file, "r", encoding="utf-8") as f:
                    for c in json.load(f):
                        cid = c.get("id") or c.get("clip_id")
                        if cid:
                            refined_titles[cid] = c.get("title", cid)
            except Exception:
                pass

        # 4. Individual clip breakdown in out/
        clips_dict: Dict[str, Dict[str, Any]] = {}
        out_clips_bytes = 0

        if o_dir.exists():
            for mp4 in o_dir.glob("*.mp4"):
                sz = mp4.stat().st_size
                out_clips_bytes += sz
                # Extract clip ID (e.g. c01 from c01.mp4, c01_blur.mp4, c01_reframed.mp4)
                stem = mp4.stem
                cid = stem.split("_")[0]
                if cid not in clips_dict:
                    clips_dict[cid] = {
                        "clip_id": cid,
                        "title": refined_titles.get(cid, f"Clip {cid}"),
                        "status": app_map.get(cid, "pending"),
                        "size_bytes": 0,
                        "variants_count": 0,
                        "files": [],
                    }
                clips_dict[cid]["size_bytes"] += sz
                clips_dict[cid]["variants_count"] += 1
                clips_dict[cid]["files"].append(mp4.name)

        # Convert clips dict to list sorted by clip_id
        clips_list = []
        for cid in sorted(clips_dict.keys()):
            c_info = clips_dict[cid]
            c_info["size_mb"] = round(c_info["size_bytes"] / (1024 * 1024), 2)
            clips_list.append(c_info)

        # 5. Ready-to-post exports size
        ready_bytes = 0
        if r_dir.exists():
            ready_bytes = sum(f.stat().st_size for f in r_dir.glob("*.mp4"))

        proj_total_bytes = source_bytes + out_clips_bytes + ready_bytes
        grand_total_bytes += proj_total_bytes
        grand_total_clips += len(clips_list)

        created_dt = datetime.fromtimestamp(created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        projects_list.append({
            "video_id": vid,
            "title": title,
            "created_at": created_dt,
            "age_days": round(age_days, 1),
            "days_until_retention": round(days_left, 1),
            "is_expired": age_days >= 30,
            "has_source": source_bytes > 0,
            "source_bytes": source_bytes,
            "source_mb": round(source_bytes / (1024 * 1024), 2),
            "clips_count": len(clips_list),
            "clips_bytes": out_clips_bytes,
            "clips_mb": round(out_clips_bytes / (1024 * 1024), 2),
            "ready_bytes": ready_bytes,
            "ready_mb": round(ready_bytes / (1024 * 1024), 2),
            "total_bytes": proj_total_bytes,
            "total_mb": round(proj_total_bytes / (1024 * 1024), 2),
            "clips": clips_list,
        })

    # Host disk space
    try:
        disk = shutil.disk_usage(Path.cwd().anchor or "/")
        disk_free_gb = round(disk.free / (1024**3), 1)
        disk_total_gb = round(disk.total / (1024**3), 1)
    except Exception:
        disk_free_gb = 0.0
        disk_total_gb = 0.0

    return {
        "success": True,
        "total_projects": len(projects_list),
        "total_clips": grand_total_clips,
        "total_bytes": grand_total_bytes,
        "total_mb": round(grand_total_bytes / (1024 * 1024), 2),
        "total_gb": round(grand_total_bytes / (1024**3), 3),
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "projects": projects_list,
    }


def delete_all_clips(
    video_id: Optional[str] = None,
    cache_base: Path = Path("cache"),
    out_base: Path = Path("out"),
    ready_base: Path = Path("ready_to_post"),
) -> Dict[str, Any]:
    """Permanently delete all rendered clips and subtitle artifacts for a video (or all videos)."""
    if video_id and video_id != "all":
        target_vids = [video_id]
    else:
        out_vids = {p.name for p in out_base.iterdir() if p.is_dir()} if out_base.exists() else set()
        cache_vids = {p.name for p in cache_base.iterdir() if p.is_dir()} if cache_base.exists() else set()
        target_vids = sorted(out_vids | cache_vids)

    total_freed = 0
    deleted_clips_count = 0

    for vid in target_vids:
        o_dir = out_base / vid
        c_dir = cache_base / vid

        # Delete all .mp4 files in out/<vid>/
        if o_dir.exists():
            for f in list(o_dir.glob("*.mp4")) + list(o_dir.glob("*.json")):
                try:
                    total_freed += f.stat().st_size
                    f.unlink()
                    if f.suffix == ".mp4":
                        deleted_clips_count += 1
                except Exception as e:
                    print(f"Error unlinking {f}: {e}")

        # Delete subtitle and vision files in cache/<vid>/
        if c_dir.exists():
            for f in list(c_dir.glob("*.ass")) + list(c_dir.glob("vision_*")):
                try:
                    total_freed += f.stat().st_size
                    f.unlink()
                except Exception as e:
                    print(f"Error unlinking {f}: {e}")

            # Reset manifests
            ref_f = c_dir / "refined_clips.json"
            if ref_f.exists():
                try:
                    with open(ref_f, "w", encoding="utf-8") as f:
                        json.dump([], f)
                except Exception:
                    pass

            app_f = c_dir / "approved.json"
            if app_f.exists():
                try:
                    with open(app_f, "w", encoding="utf-8") as f:
                        json.dump({}, f)
                except Exception:
                    pass

    return {
        "success": True,
        "video_id": video_id or "all",
        "deleted_clips_count": deleted_clips_count,
        "freed_bytes": total_freed,
        "freed_mb": round(total_freed / (1024 * 1024), 2),
        "freed_gb": round(total_freed / (1024**3), 3),
    }


def delete_source_vod(
    video_id: str,
    cache_base: Path = Path("cache"),
) -> Dict[str, Any]:
    """Delete raw source.mp4 and audio.wav for a project while keeping all clips and transcripts."""
    c_dir = cache_base / video_id
    freed = 0
    deleted_files = []

    if c_dir.exists():
        for fname in ("source.mp4", "audio.wav"):
            f = c_dir / fname
            if f.exists():
                sz = f.stat().st_size
                freed += sz
                try:
                    f.unlink()
                    deleted_files.append(fname)
                except Exception as e:
                    print(f"Error unlinking {f}: {e}")

    return {
        "success": True,
        "video_id": video_id,
        "deleted_files": deleted_files,
        "freed_bytes": freed,
        "freed_mb": round(freed / (1024 * 1024), 2),
    }
