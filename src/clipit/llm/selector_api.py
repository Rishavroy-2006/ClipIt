"""API-based intelligent clip selector using Google Gemini with automatic fallback chain."""

import os
import json
import re
import time
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import ValidationError
from clipit.models import ClipItem, CandidateWindow, FullTranscript
from clipit.llm.prompts import SYSTEM_PROMPT, build_selection_prompt

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


# Sorted strictly Best-to-Worst according to user capability and quota profile
DEFAULT_MODEL_CHAIN = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


def get_api_key() -> Optional[str]:
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key.strip()
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def select_clips_with_gemini(
    video_title: str,
    streamer: str,
    target_clips: int,
    candidates: List[CandidateWindow],
    transcript: FullTranscript,
    min_score: int = 70,
    model_chain: Optional[List[str]] = None,
) -> Optional[List[ClipItem]]:
    """Query Gemini API with automatic model fallback to generate high-CTR clips."""
    if not GENAI_AVAILABLE:
        print("Warning: google-genai library not installed.")
        return None

    api_key = get_api_key()
    if not api_key:
        print("Warning: GEMINI_API_KEY not found in environment or .env.")
        return None

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=8000))
    models = model_chain or DEFAULT_MODEL_CHAIN

    # Build prompt input
    cand_lines = []
    cand_slice_limit = min(40, len(candidates)) if target_clips <= 0 else target_clips * 2
    for c in candidates[:cand_slice_limit]:
        cand_lines.append(f"- [{c.id}] {c.start:.1f}s -> {c.end:.1f}s (Loudness {c.loudness_z:.1f}σ): {c.reason}")
    candidates_text = "\n".join(cand_lines)

    # Format transcript with timestamps
    trans_lines = []
    for seg in transcript.segments:
        trans_lines.append(f"[{seg.start:.1f}s - {seg.end:.1f}s] {seg.text}")
    transcript_text = "\n".join(trans_lines[:400])

    user_prompt = build_selection_prompt(video_title, streamer, target_clips, candidates_text, transcript_text)

    system_instruction = (
        f"{SYSTEM_PROMPT}\n"
        "Return a JSON array of clip objects with fields:\n"
        "- id: string (e.g. 'c01')\n"
        "- start: float (seconds)\n"
        "- end: float (seconds)\n"
        "- score: integer (0-100)\n"
        "- title: punchy short-form title with emojis or quotes\n"
        "- title_alts: array of 2 alternative viral titles\n"
        "- hook_line: text shown in first 2-3 seconds\n"
        "- hooks: array of strings\n"
        "- why_viral: explanation (minimum 15 words) of the viral retention hook\n"
        "- category: 'reaction' | 'funny' | 'fail' | 'clutch' | 'story' | 'rage' | 'news'\n"
        "- risk_flags: array of strings\n"
        "- layout: 'blur-fill'\n"
    )

    full_prompt = f"{system_instruction}\n\n{user_prompt}"

    # Strict maximum 12s overall deadline for Gemini API before auto-falling back
    start_time = time.time()
    max_total_timeout = 12.0

    # Limit to top 2 model candidates, 1 attempt each to prevent any pipeline stall
    for model_name in models[:2]:
        if time.time() - start_time > max_total_timeout:
            print("Notice: Gemini API overall deadline reached (12s). Falling back to local heuristic selection.")
            break
        try:
            print(f"Calling Gemini Director API ({model_name}, 8s timeout)...")
            resp = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.4,
                ),
            )
            raw_text = resp.text.strip()
            # Clean markdown fences if any
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```[a-zA-Z]*\n", "", raw_text)
                raw_text = re.sub(r"\n```$", "", raw_text)

            parsed = json.loads(raw_text)
            if isinstance(parsed, dict) and "clips" in parsed:
                parsed = parsed["clips"]
            if not isinstance(parsed, list):
                parsed = [parsed]

            clips: List[ClipItem] = []
            for item in parsed:
                try:
                    # Fill defaults if needed
                    if "id" not in item:
                        item["id"] = f"c{len(clips) + 1:02d}"
                    if "score" not in item:
                        item["score"] = 85
                    if "layout" not in item:
                        item["layout"] = "blur-fill"
                    if "source_permission" not in item:
                        item["source_permission"] = "clip-policy"
                    if "why_viral" not in item or len(item["why_viral"].split()) < 5:
                        item["why_viral"] = "Strong emotional reaction with instant viewer hook and high short-form completion rate."
                    clip = ClipItem(**item)
                    clips.append(clip)
                except ValidationError as ve:
                    print(f"Skipping invalid item: {ve}")

            if clips:
                # Sort clips by score descending
                clips.sort(key=lambda x: getattr(x, "score", 0), reverse=True)
                # Enforce user constraints: auto is min 1, strictly < 30
                if target_clips <= 0:
                    selected_clips = clips[:29]
                    print(f"✔ Successfully selected {len(selected_clips)} viral highlight clips (Auto Mode) with {model_name}")
                    return selected_clips
                else:
                    selected_clips = clips[:target_clips]
                    print(f"✔ Successfully selected {len(selected_clips)} clips with {model_name}")
                    return selected_clips

        except Exception as e:
            print(f"Notice: Gemini API model {model_name} failed: {e}. Moving to next option...")

    print("Gemini API models unavailable or timed out. Instantly falling back to local heuristic selection.")
    return None
