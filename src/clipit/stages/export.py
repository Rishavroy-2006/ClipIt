"""Export stage: Package approved clips into ready_to_post folder with clean filenames and copy-paste packs."""

import os
import re
import json
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional
from clipit.cache import CacheManager
from clipit.models import ClipItem


def clean_slug(title: str, max_words: int = 6) -> str:
    """Create a filesystem-friendly slug from title."""
    words = title.strip().split()[:max_words]
    slug = "_".join(words)
    # Remove emojis and punctuation for filename
    clean = re.sub(r"[^\w\-_]", "", slug)
    return clean[:36] or "clip"


def generate_hashtags(clip: ClipItem, streamer: str = "", game: str = "GTA 6") -> List[str]:
    """Generate high-performing short-form tags."""
    tags = ["#Shorts", "#YouTubeShorts", "#Gaming"]
    if game:
        clean_game = "".join(c for c in game if c.isalnum())
        tags.extend([f"#{clean_game}", "#GTA6Trailer", "#GrandTheftAutoVI"])
    if streamer:
        clean_streamer = "".join(c for c in streamer if c.isalnum())
        if clean_streamer and clean_streamer.lower() not in ("streamer", "livespeedy"):
            tags.append(f"#{clean_streamer}")
        elif "speed" in streamer.lower():
            tags.append("#iShowSpeed")
    if clip.category:
        tags.append(f"#{clip.category.capitalize()}")
    return list(dict.fromkeys(tags)) # Deduplicate preserving order


def run_export(
    video_id: str,
    manifest_path: Optional[Path] = None,
    out_dir_base: Path = Path("ready_to_post"),
) -> Path:
    """Export approved clips with friendly names, metadata pack, and scheduler CSV."""
    cache = CacheManager(video_id)
    clips_dir = Path("out") / video_id
    if not clips_dir.exists():
        clips_dir = cache.dir

    info = cache.load_json("info.json") or {}
    streamer = info.get("uploader", "")
    game = "GTA 6"

    # 1. Discover approved clips list
    raw_approved: Any = None

    # Priority 1: User-specified manifest
    if manifest_path and manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw_approved = json.load(f)

    # Priority 2: Check inside out/<video_id>/approved.json or cache/<video_id>/approved.json
    if raw_approved is None:
        for candidate in (clips_dir / "approved.json", cache.path("approved.json")):
            if candidate.exists():
                with open(candidate, "r", encoding="utf-8") as f:
                    raw_approved = json.load(f)
                    break

    # Priority 3: Check ~/Downloads for recently downloaded approved_*.json
    if raw_approved is None:
        downloads = Path.home() / "Downloads"
        if downloads.exists():
            recent_approved = sorted(downloads.glob("approved_*.json"), key=os.path.getmtime, reverse=True)
            if recent_approved:
                with open(recent_approved[0], "r", encoding="utf-8") as f:
                    raw_approved = json.load(f)

    # Load all available clip metadata into a dictionary keyed by id
    all_clips_map: Dict[str, Dict[str, Any]] = {}
    refined_file = cache.path("refined_clips.json")
    if refined_file.exists():
        try:
            with open(refined_file, "r", encoding="utf-8") as f:
                r_clips = json.load(f)
                for c in r_clips:
                    if isinstance(c, dict) and "id" in c:
                        all_clips_map[c["id"]] = c
        except Exception:
            pass

    for json_file in sorted(clips_dir.glob("c*.json")):
        if json_file.stem != "info":
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    c_data = json.load(f)
                    if isinstance(c_data, dict) and "id" in c_data:
                        all_clips_map.setdefault(c_data["id"], c_data)
            except Exception:
                pass

    clips_to_export: List[Dict[str, Any]] = []

    if isinstance(raw_approved, dict):
        # Case A: {"c01": "approved", "c02": "rejected"}
        for cid, status in raw_approved.items():
            if status == "approved":
                if cid in all_clips_map:
                    clips_to_export.append(all_clips_map[cid])
                elif isinstance(status, dict):
                    clips_to_export.append(status)
    elif isinstance(raw_approved, list):
        # Case B: [{"id": "c01", ...}] or ["c01", "c02"]
        for item in raw_approved:
            if isinstance(item, dict):
                clips_to_export.append(item)
            elif isinstance(item, str) and item in all_clips_map:
                clips_to_export.append(all_clips_map[item])

    # Fallback: if no approved clips were specifically marked, export all rendered clips
    if not clips_to_export:
        for json_file in sorted(clips_dir.glob("c*.json")):
            if json_file.stem != "info":
                with open(json_file, "r", encoding="utf-8") as f:
                    clips_to_export.append(json.load(f))

    if not clips_to_export:
        raise FileNotFoundError(f"No rendered or approved clips found for video: {video_id}")

    # Prepare output staging directory
    dest_dir = out_dir_base / video_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    copy_pack_lines: List[str] = [
        "=" * 80,
        f" CLIPIT READY-TO-POST PUBLISHING PACK",
        f" Source: {info.get('title', video_id)}",
        f" Total Clips: {len(clips_to_export)}",
        "=" * 80,
        "",
    ]

    csv_rows: List[str] = [
        "Index,Filename,Title,Hook,Hashtags,Why_Viral",
    ]

    for idx, c_dict in enumerate(clips_to_export, 1):
        cid = c_dict.get("id", f"c{idx:02d}")
        src_mp4 = clips_dir / f"{cid}.mp4"
        if not src_mp4.exists():
            src_mp4 = cache.path(f"{cid}.mp4")

        title = c_dict.get("title", f"Clip {idx}")
        hook = c_dict.get("hook_line", "")
        why_viral = c_dict.get("why_viral", "")
        duration = float(c_dict.get("end", 0)) - float(c_dict.get("start", 0))

        slug = clean_slug(title)
        out_filename = f"{idx:02d}_{slug}.mp4"
        dest_mp4 = dest_dir / out_filename

        if src_mp4.exists():
            if not dest_mp4.exists() or dest_mp4.stat().st_size != src_mp4.stat().st_size:
                shutil.copy2(src_mp4, dest_mp4)

        # Export multi-variants if rendered
        exported_variants = [f"Primary ({out_filename})"]
        for var_suffix, var_label in [("blur", "Letterbox"), ("reframed", "Deframed")]:
            v_src = clips_dir / f"{cid}_{var_suffix}.mp4"
            if v_src.exists():
                v_dest = dest_dir / f"{idx:02d}_{slug}_{var_label}.mp4"
                if not v_dest.exists() or v_dest.stat().st_size != v_src.stat().st_size:
                    shutil.copy2(v_src, v_dest)
                exported_variants.append(f"{var_label} ({v_dest.name})")

        # Generate tags
        clip_obj = ClipItem(**c_dict)
        tags_list = generate_hashtags(clip_obj, streamer=streamer, game=game)
        tags_str = " ".join(tags_list)

        # Append to copy-paste pack
        copy_pack_lines.extend([
            f"🎬 CLIP {idx:02d}: {out_filename} ({duration:.1f}s)",
            f"🎨 VARIANTS AVAILABLE: {', '.join(exported_variants)}",
            "-" * 80,
            f"📌 TITLE (Copy-Paste into YouTube / TikTok):",
            f"{title}",
            "",
            f"📝 DESCRIPTION & HASHTAGS:",
            f"{title} 🔥",
            "",
            f"{tags_str}",
            "",
            f"💡 WHY THIS GOES VIRAL:",
            f"{why_viral}",
            "=" * 80,
            "",
        ])

        # CSV row
        clean_title_csv = title.replace('"', '""')
        clean_hook_csv = hook.replace('"', '""')
        clean_tags_csv = tags_str.replace('"', '""')
        clean_why_csv = why_viral.replace('"', '""')
        csv_rows.append(f'{idx},"{out_filename}","{clean_title_csv}","{clean_hook_csv}","{clean_tags_csv}","{clean_why_csv}"')

    # Write text pack
    pack_file = dest_dir / "copy_paste_pack.txt"
    with open(pack_file, "w", encoding="utf-8") as f:
        f.write("\n".join(copy_pack_lines))

    # Write CSV for scheduling
    csv_file = dest_dir / "schedule.csv"
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("\n".join(csv_rows) + "\n")

    return dest_dir
