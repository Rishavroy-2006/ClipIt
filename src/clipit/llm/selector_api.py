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


# Sorted strictly by available quota, reliability, and speed
DEFAULT_MODEL_CHAIN = [
    "gemini-3.1-flash-lite",  # 15 RPM, 500 RPD (fastest & highest free-tier quota)
    "gemini-3.5-flash-lite",  # 15 RPM, 500 RPD
    "gemma-4-26b-a4b-it",     # 30 RPM, 14.4K RPD
    "gemini-3.8-flash",       # 5 RPM, 20 RPD
    "gemini-3.6-flash",       # 5 RPM, 20 RPD
    "gemini-3-flash",
    "gemini-2.5-flash",
]


def get_api_keys() -> List[str]:
    """Retrieve all configured Gemini API keys with support for multi-key fallback.
    
    Discovers:
    - Environment variables: GEMINI_API_KEY, GEMINI_API_KEY_1, GEMINI_API_KEY_2, GEMINI_API_KEY_BACKUP, etc.
    - Comma-separated: GEMINI_API_KEYS="key1,key2"
    - .env file entries with the same patterns.
    """
    keys: List[str] = []

    # 1. Environment variables
    for var in [
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_BACKUP",
        "GEMINI_API_KEY_SECONDARY",
    ]:
        val = os.getenv(var)
        if val and val.strip() and val.strip() not in keys:
            keys.append(val.strip())

    multi = os.getenv("GEMINI_API_KEYS")
    if multi:
        for k in multi.split(","):
            k_clean = k.strip()
            if k_clean and k_clean not in keys:
                keys.append(k_clean)

    # 2. .env file
    env_file = Path(".env")
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k_name, k_val = line.split("=", 1)
                k_name = k_name.strip()
                k_val = k_val.strip().strip("'\"")
                if k_name in (
                    "GEMINI_API_KEY",
                    "GEMINI_API_KEY_1",
                    "GEMINI_API_KEY_2",
                    "GEMINI_API_KEY_3",
                    "GEMINI_API_KEY_BACKUP",
                    "GEMINI_API_KEY_SECONDARY",
                ):
                    if k_val and k_val not in keys:
                        keys.append(k_val)
                elif k_name == "GEMINI_API_KEYS":
                    for k in k_val.split(","):
                        k_clean = k.strip()
                        if k_clean and k_clean not in keys:
                            keys.append(k_clean)
        except Exception:
            pass

    return keys


def get_api_key() -> Optional[str]:
    """Get the primary Gemini API key."""
    keys = get_api_keys()
    return keys[0] if keys else None



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

    api_keys = get_api_keys()
    if not api_keys:
        print("Warning: GEMINI_API_KEY not found in environment or .env.")
        return None

    models = model_chain or DEFAULT_MODEL_CHAIN

    # Build prompt input with targeted candidate transcript segments
    cand_lines = []
    cand_slice_limit = min(20, len(candidates)) if target_clips <= 0 else max(5, target_clips * 2)
    selected_cands = candidates[:cand_slice_limit]
    relevant_segments = set()
    for c in selected_cands:
        cand_lines.append(f"- [{c.id}] {c.start:.1f}s -> {c.end:.1f}s (Loudness {c.loudness_z:.1f}σ): {c.reason}")
        for seg in transcript.segments:
            if seg.end >= c.start - 2.0 and seg.start <= c.end + 2.0:
                relevant_segments.add((seg.start, seg.end, seg.text))

    candidates_text = "\n".join(cand_lines)
    sorted_segs = sorted(list(relevant_segments), key=lambda x: x[0])
    transcript_text = "\n".join([f"[{s:.1f}s - {e:.1f}s] {t}" for s, e, t in sorted_segs[:100]])

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

    # Bounded overall deadline for Gemini API before auto-falling back
    start_time = time.time()
    max_total_timeout = 120.0

    # Iterate over available API keys (with automatic failover if quota is exhausted)
    for key_idx, current_key in enumerate(api_keys):
        masked_key = current_key[:8] + "..." + current_key[-4:] if len(current_key) > 12 else f"key-{key_idx+1}"
        key_label = f"Key #{key_idx + 1} ({masked_key})"
        client = genai.Client(api_key=current_key, http_options=types.HttpOptions(timeout=75000))
        key_exhausted = False

        # Try model chain candidates with retry on transient 503 high demand spikes
        for model_name in models[:4]:
            if key_exhausted or (time.time() - start_time > max_total_timeout):
                break

            for attempt in range(2):
                if time.time() - start_time > max_total_timeout:
                    break
                try:
                    print(f"Calling Gemini Director API ({model_name}, attempt {attempt + 1}, using {key_label})...")
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
                            # Normalize alternative LLM key names
                            if "start" not in item and "start_time" in item:
                                item["start"] = float(item["start_time"])
                            if "end" not in item and "end_time" in item:
                                item["end"] = float(item["end_time"])
                            if "title" not in item and "name" in item:
                                item["title"] = str(item["name"])
                            if "hook_line" not in item and "hook" in item:
                                item["hook_line"] = str(item["hook"])
                            elif "hook_line" not in item and "hook_tag" in item:
                                item["hook_line"] = str(item["hook_tag"])

                            # Fill defaults if needed
                            if "id" not in item:
                                item["id"] = f"c{len(clips) + 1:02d}"
                            if "title" not in item or not item["title"]:
                                item["title"] = f"Viral Highlight #{len(clips) + 1}"
                            if "hook_line" not in item or not item["hook_line"]:
                                item["hook_line"] = item["title"]

                            # Scale and normalize score (handle 0-1 or 1-10 scales from smaller models)
                            raw_score = item.get("score", 85)
                            try:
                                f_score = float(raw_score)
                                if f_score <= 1.0 and f_score > 0.0:
                                    item["score"] = int(round(f_score * 100))
                                elif f_score <= 10.0 and f_score > 0.0:
                                    item["score"] = int(round(f_score * 10))
                                else:
                                    item["score"] = int(round(f_score))
                            except (ValueError, TypeError):
                                item["score"] = 85

                            if "layout" not in item:
                                item["layout"] = "blur-fill"
                            if "source_permission" not in item:
                                item["source_permission"] = "clip-policy"
                            if "why_viral" not in item or len(str(item["why_viral"]).split()) < 5:
                                item["why_viral"] = "Strong emotional reaction with instant viewer hook and high short-form completion rate."
                            item["selector_engine"] = model_name
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
                    err_str = str(e)
                    is_quota = any(w in err_str.lower() for w in ["429", "resource_exhausted", "quota", "ratelimit"])
                    if is_quota:
                        print(f"⚠️ {key_label} quota exhausted: {err_str[:90]}...")
                        key_exhausted = True
                        break  # Stop trying other models on this exhausted key, fall over to next key

                    print(f"Notice: Gemini API model {model_name} (attempt {attempt + 1}) error: {err_str[:120]}")
                    if ("503" in err_str or "unavailable" in err_str.lower()) and attempt == 0:
                        time.sleep(2.0)
                        continue
                    break

        if key_exhausted and key_idx < len(api_keys) - 1:
            print(f"🔄 Seamlessly failing over to Backup Gemini API Key #{key_idx + 2}...")
            continue


    print("Gemini API models unavailable or timed out. Instantly falling back to local heuristic selection.")
    return None
