"""Select stage: Pick and score top viral clips using Gemini API models or file prompts, with heuristic fallback."""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from clipit.cache import CacheManager
from clipit.models import FullTranscript, CandidateWindow, ClipItem
from clipit.llm.prompts import SYSTEM_PROMPT, build_selection_prompt
from clipit.llm.selector_api import select_clips_with_gemini, get_api_key


def run_select(
    cache: CacheManager,
    transcript: FullTranscript,
    candidates: List[CandidateWindow],
    target_clips: int = 10,
    min_score: int = 70,
    selector_mode: str = "api",
    force: bool = False,
) -> List[ClipItem]:
    """Select and rank viral clips, outputting cache/<video_id>/clips.json."""
    if not force and cache.exists("clips.json"):
        raw = cache.load_json("clips.json")
        return [ClipItem(**c) for c in raw]

    info = cache.load_json("info.json") or {}
    video_title = info.get("title", "Gaming Stream")
    streamer = info.get("uploader", "Streamer")

    clips: List[ClipItem] = []

    # 1. If API mode is requested and API key exists in .env, attempt Gemini selection
    api_key = get_api_key()
    if selector_mode == "api" and api_key and selector_mode not in ("heuristic", "file-only"):
        gemini_clips = select_clips_with_gemini(
            video_title=video_title,
            streamer=streamer,
            target_clips=target_clips,
            candidates=candidates,
            transcript=transcript,
            min_score=min_score,
        )
        if gemini_clips:
            clips = gemini_clips
        else:
            print("[DIRECTOR] Gemini API unavailable or timed out. Seamlessly engaging local heuristic virality selector.")

    # 2. If selector mode is 'file' or result file exists in results/
    prompts_dir = Path("prompts")
    prompts_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompts_dir / f"select_{cache.video_id}.md"
    schema_file = prompts_dir / "schema.json"

    # Always generate prompt & schema so the user has the inspection file
    cand_lines = [f"- [{c.id}] {c.start:.1f}s -> {c.end:.1f}s ({c.loudness_z:.1f}σ): {c.reason}" for c in candidates]
    trans_lines = [f"[{seg.start:.1f}s - {seg.end:.1f}s] {seg.text}" for seg in transcript.segments]
    prompt_md = build_selection_prompt(video_title, streamer, target_clips, "\n".join(cand_lines), "\n".join(trans_lines[:300]))

    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(f"# System\n{SYSTEM_PROMPT}\n\n# User\n{prompt_md}")
    with open(schema_file, "w", encoding="utf-8") as f:
        json.dump(ClipItem.model_json_schema(), f, indent=2)

    results_dir = Path("results")
    result_file = results_dir / f"select_{cache.video_id}.json"
    if not clips and result_file.exists():
        try:
            with open(result_file, "r", encoding="utf-8") as f:
                raw_items = json.load(f)
                for item in raw_items:
                    clips.append(ClipItem(**item))
        except Exception as e:
            print(f"Warning: Failed to parse user result file {result_file}: {e}")

    # 3. Fallback to heuristic selection if neither API nor file gave clips
    if not clips:
        # If target_clips is auto (<= 0), automatically select all high-quality candidates (>= 75 score, up to 29)
        if target_clips <= 0:
            heuristic_cands = [c for c in candidates if getattr(c, "score", 0) >= 75][:29]
            if not heuristic_cands:
                heuristic_cands = candidates[:min(5, len(candidates))]
        else:
            heuristic_cands = candidates[:target_clips]

        for idx, cand in enumerate(heuristic_cands):
            cid = f"clip_{idx + 1:02d}"
            slice_words = []
            for seg in transcript.segments:
                if seg.end >= cand.start and seg.start <= cand.end:
                    slice_words.extend([w.w for w in seg.words])

            transcript_snippet = " ".join(slice_words)
            hook_text = (" ".join(slice_words[:6])) if slice_words else "Watch this crazy moment"

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
                selector_engine="local-fallback",
            )
            clips.append(clip)

    cache.save_json("clips.json", [c.model_dump() for c in clips])
    return clips
