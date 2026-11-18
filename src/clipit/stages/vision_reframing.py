"""Vision-guided intelligent reframing stage.

Samples frames (first, middle, last) from viral clip candidate windows and uses
Gemini Vision (with OpenCV fallback) to dynamically determine:
1. Streamer facecam bounding box and optimal vertical ratio (e.g. 36% to 42%).
2. Primary gameplay action focal point and horizontal alignment ('left', 'center', 'right').
3. Edge-to-edge crop parameters matching reference TikTok/Shorts split layouts.
"""

import os
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import cv2
import numpy as np
from clipit.models import ClipItem
from clipit.cache import CacheManager


# Sorted strictly by available quota, reliability, and speed
FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",  # 15 RPM, 500 RPD (fastest & highest free-tier quota)
    "gemini-3.5-flash-lite",  # 15 RPM, 500 RPD
    "gemma-4-26b-a4b-it",     # 30 RPM, 14.4K RPD
    "gemini-3.8-flash",       # 5 RPM, 20 RPD
    "gemini-3.6-flash",       # 5 RPM, 20 RPD
    "gemini-3-flash",
    "gemini-2.5-flash",
]


def extract_sample_frames(video_path: Path, clip: ClipItem, cache: CacheManager) -> List[Path]:
    """Extract first, middle, and last frames from clip timeline."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    duration = clip.end - clip.start
    
    t_first = clip.start + min(1.0, duration * 0.15)
    t_mid = (clip.start + clip.end) / 2.0
    t_last = clip.end - min(1.0, duration * 0.15)

    frame_paths: List[Path] = []
    for label, t in [("first", t_first), ("mid", t_mid), ("last", t_last)]:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if ret:
            p = cache.path(f"vision_{clip.id}_{label}.jpg")
            cv2.imwrite(str(p), frame)
            frame_paths.append(p)

    cap.release()
    return frame_paths


def analyze_with_gemini_vision(frame_path: Path, api_key: str) -> Optional[Dict[str, Any]]:
    """Analyze a single stream frame with Gemini Multimodal Vision API."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=10000))
    img_bytes = frame_path.read_bytes()
    part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")

    prompt = """Analyze this gaming stream frame for TikTok/Shorts vertical video variants:
1. Is there an active streamer reaction webcam / facecam visible on screen? ("has_facecam": true/false)
2. If yes, locate the streamer's reaction webcam: [ymin, xmin, ymax, xmax] normalized (0 to 1000). If no facecam, return null.
3. Locate the main focal point / action of the gameplay / content being reacted to: [ymin, xmin, ymax, xmax] normalized (0 to 1000).
4. Determine horizontal alignment of the gameplay action: "left", "center", or "right".
5. Recommend optimal vertical height percentage for streamer on top (between 36 and 42) and content on bottom (between 58 and 64). If no facecam, recommend 0 and 100.

Output JSON only in this exact schema:
{
  "has_facecam": true,
  "streamer_box": [ymin, xmin, ymax, xmax],
  "content_focus": [ymin, xmin, ymax, xmax],
  "content_alignment": "left|center|right",
  "streamer_pct": 38,
  "content_pct": 62
}"""

    start_time = time.time()
    for model in FALLBACK_MODELS[:2]:
        if time.time() - start_time > 15.0:
            break
        try:
            res = client.models.generate_content(
                model=model,
                contents=[part, prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            data = json.loads(res.text)
            if "content_alignment" in data:
                if "has_facecam" not in data:
                    data["has_facecam"] = bool(data.get("streamer_box"))
                return data
        except Exception:
            continue

    return None


def analyze_with_opencv_fallback(frame_path: Path) -> Dict[str, Any]:
    """Offline heuristic vision fallback using OpenCV YuNet."""
    frame = cv2.imread(str(frame_path))
    if frame is None:
        return {
            "has_facecam": False,
            "streamer_box": None,
            "content_focus": [100, 300, 800, 900],
            "content_alignment": "center",
            "content_focus_x": 500,
            "streamer_pct": 0,
            "content_pct": 100,
        }
    h, w = frame.shape[:2]
    model_path = Path("assets/models/face_detection_yunet_2023mar.onnx")

    has_facecam = False
    streamer_box = [500, 0, 1000, 450] # normalized default (bottom-left)
    content_alignment = "center"
    content_focus_x = 500
    streamer_pct = 38

    if model_path.exists():
        detector = cv2.FaceDetectorYN_create(str(model_path), "", (w, h), score_threshold=0.6)
        _, faces = detector.detect(frame)
        if faces is not None and len(faces) > 0:
            # Pick corner face
            corner_faces = []
            for face in faces:
                fx, fy, fw, fh = [int(v) for v in face[:4]]
                cx, cy = fx + fw // 2, fy + fh // 2
                if (cx < w * 0.4 or cx > w * 0.6) and (cy < h * 0.4 or cy > h * 0.6):
                    corner_faces.append((fx, fy, fw, fh))
            if corner_faces:
                has_facecam = True
                min_x = min(f[0] for f in corner_faces)
                max_x = max(f[0] + f[2] for f in corner_faces)
                min_y = min(f[1] for f in corner_faces)
                max_y = max(f[1] + f[3] for f in corner_faces)
                
                # Expand box
                bx = max(0, min_x - 50)
                by = max(0, min_y - 80)
                bw = min(w - bx, (max_x - min_x) + 120)
                bh = min(h - by, (max_y - min_y) + 140)

                streamer_box = [
                    int(by / h * 1000),
                    int(bx / w * 1000),
                    int((by + bh) / h * 1000),
                    int((bx + bw) / w * 1000),
                ]
                if bx < w * 0.4:
                    content_alignment = "right"
                    content_focus_x = 650
                else:
                    content_alignment = "left"
                    content_focus_x = 350

    return {
        "has_facecam": has_facecam,
        "streamer_box": streamer_box if has_facecam else None,
        "content_focus": [100, 300, 800, 900],
        "content_alignment": content_alignment,
        "content_focus_x": content_focus_x,
        "streamer_pct": streamer_pct if has_facecam else 0,
        "content_pct": 100 - (streamer_pct if has_facecam else 0),
    }


def detect_content_letterbox(img_or_path: Any) -> Tuple[int, int]:
    """Detect vertical boundaries of active content to eliminate letterbox bars."""
    if isinstance(img_or_path, (str, Path)):
        img = cv2.imread(str(img_or_path))
    else:
        img = img_or_path
    if img is None:
        return 0, 1080
    h, w = img.shape[:2]
    cols = [int(w * f) for f in [0.4, 0.5, 0.6, 0.7, 0.8]]
    top_candidates = []
    bot_candidates = []
    for c in cols:
        col = img[:, c]
        b = np.mean(col, axis=1)
        top = 0
        for y in range(0, int(h * 0.35)):
            if b[y] > 20:
                top = y
                break
        top_candidates.append(top)
        bot = h
        for y in range(h - 1, int(h * 0.65), -1):
            if b[y] > 20:
                bot = y
                break
        bot_candidates.append(bot)
    top_y = int(np.median(top_candidates))
    bot_y = int(np.median(bot_candidates))
    return top_y, bot_y


def analyze_clip_vision(
    clip: ClipItem,
    source_video: Path,
    cache: CacheManager,
) -> Dict[str, Any]:
    """Perform vision analysis for a clip with caching."""
    cache_file = f"vision_{clip.id}.json"
    
    # 1. Extract sample frames (first, mid, last)
    sample_frames = extract_sample_frames(source_video, clip, cache)
    mid_frame = sample_frames[1] if len(sample_frames) > 1 else (sample_frames[0] if sample_frames else None)

    letterbox_bounds = [0, 1080]
    if mid_frame and mid_frame.exists():
        letterbox_bounds = list(detect_content_letterbox(mid_frame))

    if cache.exists(cache_file):
        cached = cache.load_json(cache_file)
        if cached:
            if "content_letterbox" not in cached:
                cached["content_letterbox"] = letterbox_bounds
                cache.save_json(cache_file, cached)
            return cached

    if not sample_frames or not mid_frame:
        fallback = analyze_with_opencv_fallback(source_video)
        fallback["content_letterbox"] = letterbox_bounds
        return fallback

    # 2. Vision Detection Engine: OpenCV YuNet by default (~75ms per clip)
    engine = os.environ.get("CLIPIT_VISION_ENGINE", "opencv").lower()
    analysis: Optional[Dict[str, Any]] = None

    if engine == "gemini":
        from clipit.llm.selector_api import get_api_keys
        for key in get_api_keys():
            analysis = analyze_with_gemini_vision(mid_frame, key)
            if analysis:
                break


    if not analysis:
        analysis = analyze_with_opencv_fallback(mid_frame)

    if "content_focus_x" not in analysis:
        analysis["content_focus_x"] = 500

    analysis["content_letterbox"] = letterbox_bounds
    cache.save_json(cache_file, analysis)
    return analysis
