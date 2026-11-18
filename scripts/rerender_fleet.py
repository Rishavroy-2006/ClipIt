import os
import sys
import json
import shutil
from pathlib import Path
from clipit.cache import CacheManager
from clipit.models import ClipItem, FullTranscript
from clipit.config import VideoConfig
from clipit.stages.render import render_single_clip

CLIPS_MAP = [
    ("pt01_c04", "yt_qSyo5d-dVzA", "part_01"),
    ("pt03_c02", "yt_qSyo5d-dVzA_pt03", "part_03"),
    ("pt03_c04", "yt_qSyo5d-dVzA_pt03", "part_03"),
    ("pt03_c06", "yt_qSyo5d-dVzA_pt03", "part_03"),
    ("pt05_c02", "yt_qSyo5d-dVzA_pt05", "part_05"),
    ("pt05_c03", "yt_qSyo5d-dVzA_pt05", "part_05"),
    ("pt05_c05", "yt_qSyo5d-dVzA_pt05", "part_05"),
    ("pt06_c01", "yt_qSyo5d-dVzA_pt06", "part_06"),
    ("pt06_c02", "yt_qSyo5d-dVzA_pt06", "part_06"),
    ("pt06_c03", "yt_qSyo5d-dVzA_pt06", "part_06"),
    ("pt06_c04", "yt_qSyo5d-dVzA_pt06", "part_06"),
    ("pt06_c05", "yt_qSyo5d-dVzA_pt06", "part_06"),
    ("pt06_c06", "yt_qSyo5d-dVzA_pt06", "part_06"),
]

def main():
    cfg = VideoConfig(crf=20, fps=30)
    out_dir = Path("out/yt_qSyo5d-dVzA")
    
    total = len(CLIPS_MAP)
    print(f"Starting batch re-render of {total} clips using dual-PTS engine...")
    
    for idx, (cid, cache_id, part_name) in enumerate(CLIPS_MAP, 1):
        json_path = out_dir / f"{cid}.json"
        if not json_path.exists():
            print(f"[{idx}/{total}] Skipping {cid}: {json_path} does not exist.")
            continue
            
        print(f"\n==================================================")
        print(f"[{idx}/{total}] Rendering {cid} from {cache_id}...")
        print(f"==================================================")
        
        cache = CacheManager(cache_id)
        source_video = cache.path("source.mp4")
        transcript_dict = cache.load_json("transcript.json")
        transcript = FullTranscript(**transcript_dict)
        
        with open(json_path) as f:
            clip_dict = json.load(f)
        clip = ClipItem(**clip_dict)
        
        render_single_clip(
            clip=clip,
            source_video=source_video,
            transcript=transcript,
            cache=cache,
            output_dir=out_dir,
            video_config=cfg,
            codec="auto",
            force=True
        )
        
        # Synchronize into part folder if it exists
        part_dir = out_dir / part_name
        if part_dir.exists():
            for suffix in ["", "_blur", "_reframed"]:
                src_mp4 = out_dir / f"{cid}{suffix}.mp4"
                dst_mp4 = part_dir / f"{cid}{suffix}.mp4"
                if src_mp4.exists():
                    shutil.copy2(src_mp4, dst_mp4)
            src_json = out_dir / f"{cid}.json"
            if src_json.exists():
                shutil.copy2(src_json, part_dir / f"{cid}.json")
                
        print(f"✔ Completed {cid} ({idx}/{total})")

    print("\n🎉 All clips successfully re-rendered with dual-PTS synchronization!")

if __name__ == "__main__":
    main()
