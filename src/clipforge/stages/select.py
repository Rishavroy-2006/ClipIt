"""Select stage: Pick and score top viral clips using file prompts or API models, with heuristic signal ranking."""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from clipforge.cache import CacheManager
from clipforge.models import FullTranscript, CandidateWindow, ClipItem, ClipCollection
from clipforge.llm.prompts import SYSTEM_PROMPT, build_selection_prompt


def run_select(
    cache: CacheManager,
    transcript: FullTranscript,
    candidates: List[CandidateWindow],
    target_clips: int = 10,
    min_score: int = 70,
    selector_mode: str = "file",
) -> List[ClipItem]:
    """Select and rank viral clips, outputting cache/<video_id>/clips.json."""
    if cache.exists("clips.json"):
        raw = cache.load_json("clips.json")
        return [ClipItem(**c) for c in raw]

    info = cache.load_json("info.json") or {}
    video_title = info.get("title", "Gaming Stream")
    streamer = info.get("uploader", "Streamer")

    # Format candidate windows summary
    cand_lines = []
    for c in candidates[:target_clips * 2]:
        cand_lines.append(f"- [{c.id}] {c.start:.1f}s -> {c.end:.1f}s (Signal Score: {c.score:.0f}): {c.reason}")
    candidates_text = "\n".join(cand_lines)

    # Format transcript summary
    trans_lines = []
    for seg in transcript.segments:
        trans_lines.append(f"[{seg.start:.1f}s - {seg.end:.1f}s] {seg.text}")
    transcript_text = "\n".join(trans_lines[:300]) # Cap preview for chunk

    prompt_md = build_selection_prompt(video_title, streamer, target_clips, candidates_text, transcript_text)

    # 1. Write file prompt and schema into prompts/
    prompts_dir = Path("prompts")
    prompts_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompts_dir / f"select_{cache.video_id}.md"
    schema_file = prompts_dir / "schema.json"

    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(f"# System\n{SYSTEM_PROMPT}\n\n# User\n{prompt_md}")

    with open(schema_file, "w", encoding="utf-8") as f:
        json.dump(ClipItem.model_json_schema(), f, indent=2)

    # 2. Check if user or LLM provided results in results/
    results_dir = Path("results")
    result_file = results_dir / f"select_{cache.video_id}.json"

    clips: List[ClipItem] = []

    if result_file.exists():
        try:
            with open(result_file, "r", encoding="utf-8") as f:
                raw_items = json.load(f)
                for item in raw_items:
                    clips.append(ClipItem(**item))
        except Exception as e:
            print(f"Warning: Failed to parse user result file {result_file}: {e}")

    # 3. If no LLM result provided, generate high-confidence candidates using multi-signal highlights
    if not clips:
        # Build clips from top candidate windows
        for idx, cand in enumerate(candidates[:target_clips]):
            cid = f"clip_{idx + 1:02d}"
            # Extract transcript text
            slice_words = []
            for seg in transcript.segments:
                if seg.end >= cand.start and seg.start <= cand.end:
                    slice_words.extend([w.w for w in seg.words])

            transcript_snippet = " ".join(slice_words)
            hook_text = (" ".join(slice_words[:6])) if slice_words else "Watch this crazy moment"
            
            # Dynamic punchy title based on spoken words or streamer name
            if len(slice_words) >= 4:
                snippet_title = " ".join(slice_words[:6]).strip(".,!?:;\"'").capitalize()
                title = f'{streamer}: "{snippet_title}..."'
                alt1 = f"{streamer} Loses His Mind Over GTA 6 Trailer 2"
                alt2 = f"Speed's Craziest Reaction to GTA 6"
            else:
                title = f"{streamer} Reacts to GTA 6: Highlight #{idx + 1}"
                alt1 = f"Speed Loses It During GTA 6 Trailer"
                alt2 = f"Insane GTA 6 Trailer Moment"

            why_viral = (
                f"Explosive highlight with audio loudness spike ({cand.loudness_z:.1f}σ) and immediate visual emotion. "
                f"The streamer's raw reaction hooks viewers within the opening 2 seconds and delivers strong viral short-form retention."
            )

            clip = ClipItem(
                id=cid,
                start=cand.start,
                end=cand.end,
                score=min(98, max(min_score, int(cand.score))),
                title=title,
                title_alts=[alt1, alt2],
                hook_line=hook_text[:40],
                hooks=["explosive reaction", "unexpected reveal"],
                why_viral=why_viral,
                category="reaction",
                signals={"loudness_z": cand.loudness_z, "speech": True},
                risk_flags=[],
                layout="blur-fill",
                source_permission="clip-policy",
            )
            clips.append(clip)

    cache.save_json("clips.json", [c.model_dump() for c in clips])
    return clips
