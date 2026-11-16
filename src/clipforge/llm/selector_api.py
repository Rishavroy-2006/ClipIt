"""API-based intelligent clip selector using Google Gemini with automatic fallback chain."""

import os
import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import ValidationError
from clipforge.models import ClipItem, CandidateWindow, FullTranscript
from clipforge.llm.prompts import SYSTEM_PROMPT, build_selection_prompt

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


# Recommended models prioritized by high quota (500 RPD, 15 RPM) and quality
DEFAULT_MODEL_CHAIN = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
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

    client = genai.Client(api_key=api_key)
    models = model_chain or DEFAULT_MODEL_CHAIN

    # Build prompt input
    cand_lines = []
    for c in candidates[:target_clips * 2]:
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

    for model_name in models:
        try:
            print(f"Calling Gemini API ({model_name})...")
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
                print(f"✔ Successfully selected {len(clips)} clips with {model_name}")
                return clips[:target_clips]

        except Exception as e:
            print(f"Warning: Model {model_name} failed: {e}. Trying next model in chain...")

    print("All Gemini API models in chain exhausted. Falling back to local heuristic selection.")
    return None
