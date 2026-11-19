"""Layout stage: Facecam detection, Gemini Vision reframing, and FFmpeg filtergraphs for vertical Shorts."""

from pathlib import Path
from typing import Tuple, Optional, List, Dict, Any
import cv2
import numpy as np
import yaml


def detect_facecam_box(
    video_path: Path,
    max_samples: int = 15,
    profile_path: Optional[Path] = None,
) -> Optional[Tuple[int, int, int, int]]:
    """Detect persistent streamer facecam box by sampling frames across the stream."""
    if profile_path and profile_path.exists():
        with open(profile_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if "facecam_box" in data and data["facecam_box"]:
                box = data["facecam_box"]
                return (int(box[0]), int(box[1]), int(box[2]), int(box[3]))

    model_path = Path("assets/models/face_detection_yunet_2023mar.onnx")
    if not model_path.exists():
        alt = Path(__file__).resolve().parent.parent.parent.parent / "assets/models/face_detection_yunet_2023mar.onnx"
        if alt.exists():
            model_path = alt
        else:
            return None

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    duration_s = total_frames / fps if fps > 0 else 0

    if duration_s < 5 or w == 0 or h == 0:
        cap.release()
        return None

    detector = cv2.FaceDetectorYN_create(str(model_path), "", (w, h), score_threshold=0.6)

    sample_secs = np.linspace(duration_s * 0.1, duration_s * 0.9, max_samples)
    detections_per_frame: List[List[Tuple[int, int, int, int, float]]] = []

    for sec in sample_secs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(sec * fps))
        ret, frame = cap.read()
        if not ret:
            continue
        _, faces = detector.detect(frame)
        frame_faces = []
        if faces is not None:
            for face in faces:
                fx, fy, fw, fh = [int(v) for v in face[:4]]
                conf = float(face[-1])
                if 40 <= fw <= w * 0.55 and 40 <= fh <= h * 0.55:
                    frame_faces.append((fx, fy, fw, fh, conf))
        detections_per_frame.append(frame_faces)

    cap.release()

    if not detections_per_frame:
        return None

    clusters: List[Dict[str, Any]] = []
    for frame_idx, frame_faces in enumerate(detections_per_frame):
        for f in frame_faces:
            fx, fy, fw, fh, conf = f
            cx, cy = fx + fw // 2, fy + fh // 2
            matched = None
            for c in clusters:
                mean_cx = float(np.mean([pt[0] for pt in c["centers"]]))
                mean_cy = float(np.mean([pt[1] for pt in c["centers"]]))
                if np.hypot(cx - mean_cx, cy - mean_cy) < 160:
                    matched = c
                    break
            if matched:
                matched["faces"].append((fx, fy, fw, fh, conf, frame_idx))
                matched["centers"].append((cx, cy))
            else:
                clusters.append({
                    "faces": [(fx, fy, fw, fh, conf, frame_idx)],
                    "centers": [(cx, cy)],
                })

    min_frames = max(2, int(len(detections_per_frame) * 0.30))
    persistent: List[Dict[str, Any]] = []
    for c in clusters:
        unique_frames = len(set(f[5] for f in c["faces"]))
        if unique_frames >= min_frames:
            c["unique_frames"] = unique_frames
            persistent.append(c)

    if not persistent:
        return None

    mid_x, mid_y = w // 2, h // 2
    quad_clusters: List[List[Dict[str, Any]]] = [[], [], [], []]
    for c in persistent:
        mean_cx = float(np.mean([pt[0] for pt in c["centers"]]))
        mean_cy = float(np.mean([pt[1] for pt in c["centers"]]))
        q = (0 if mean_cy < mid_y else 2) + (0 if mean_cx < mid_x else 1)
        quad_clusters[q].append(c)

    best_quad = max(quad_clusters, key=lambda qc: sum(c["unique_frames"] for c in qc))
    if not best_quad:
        return None

    all_x = []
    for c in best_quad:
        for f in c["faces"]:
            all_x.append(f[0])
            all_x.append(f[0] + f[2])

    primary_cluster = max(best_quad, key=lambda c: np.mean([f[2] * f[3] * f[4] for f in c["faces"]]))
    face_h = float(np.median([f[3] for f in primary_cluster["faces"]]))

    des_h = int(min(h, max(460, face_h * 2.5)))
    des_h = des_h - (des_h % 2)
    des_w = int(des_h * 1.5)
    des_w = min(w, des_w - (des_w % 2))

    quad_min_x = min(all_x)
    quad_max_x = max(all_x)
    center_x = (quad_min_x + quad_max_x) // 2

    box_x = max(0, min(w - des_w, int(center_x - des_w // 2)))
    box_x = box_x - (box_x % 2)

    mean_cy = float(np.mean([pt[1] for pt in primary_cluster["centers"]]))
    box_y = max(0, h - des_h) if mean_cy >= mid_y else 0
    box_y = box_y - (box_y % 2)

    return (box_x, box_y, des_w, des_h)


def build_reference_stack_filter(
    vision_info: Dict[str, Any],
    source_w: int = 1920,
    source_h: int = 1080,
) -> Tuple[str, int]:
    """Build dynamic edge-to-edge stacked layout matching reference shorts.
    
    - Streamer on TOP (36% - 42% height, determined by vision model).
    - Content/Gameplay on BOTTOM (58% - 64% height, reframed dynamically based on action).
    - Subtitles positioned right across the dividing seam.
    """
    streamer_pct = vision_info.get("streamer_pct", 38)
    if not streamer_pct or streamer_pct <= 0:
        streamer_pct = 38

    # Ensure streamer height is even
    top_h = int(1920 * (streamer_pct / 100.0))
    top_h -= top_h % 2
    bot_h = 1920 - top_h

    # 1. Top Tier: Streamer Facecam
    s_box = vision_info.get("streamer_box") or [400, 0, 950, 300]
    s_ymin, s_xmin, s_ymax, s_xmax = s_box
    raw_bx = int(s_xmin / 1000.0 * source_w)
    raw_by = int(s_ymin / 1000.0 * source_h)
    raw_bw = int((s_xmax - s_xmin) / 1000.0 * source_w)
    raw_bh = int((s_ymax - s_ymin) / 1000.0 * source_h)
    center_sx = raw_bx + raw_bw // 2
    center_sy = raw_by + raw_bh // 2

    ar_top = 1080.0 / top_h
    # Ensure top tier crop height comfortably encompasses the streamer box (unclamped down to 80px for tight face zoom)
    needed_h_from_w = int(raw_bw / ar_top)
    s_src_h = max(80, min(source_h, int(max(raw_bh * 1.15, needed_h_from_w * 1.05))))
    s_src_w = int(s_src_h * ar_top)
    if s_src_w > source_w:
        s_src_w = source_w
        s_src_h = int(s_src_w / ar_top)

    # Facecam framing: corner-pinning & headroom preservation
    # If streamer box hugs a corner, pin to the edge so black margin is never exposed
    if raw_bx <= source_w * 0.18:
        s_src_x = 0
    elif (raw_bx + raw_bw) >= source_w * 0.82:
        s_src_x = source_w - s_src_w
    else:
        s_src_x = max(0, min(source_w - s_src_w, int(center_sx - s_src_w // 2)))

    # Vertical headroom bias: face detectors return brow to chin, so bias slightly upward to keep hair/forehead
    s_src_y = max(0, min(source_h - s_src_h, int(center_sy - s_src_h * 0.42)))

    s_src_w -= s_src_w % 2
    s_src_h -= s_src_h % 2
    s_src_x -= s_src_x % 2
    s_src_y -= s_src_y % 2

    # 2. Bottom Tier: Content / Gameplay (Reframed to action)
    ar_bot = 1080.0 / bot_h

    # Check for letterbox bounds to eliminate black bars
    letterbox = vision_info.get("content_letterbox", [0, source_h])
    top_black_y = int(letterbox[0])
    bot_black_y = int(letterbox[1])

    # Available content height inside letterboxes
    avail_h = max(420, bot_black_y - top_black_y)
    g_src_h = avail_h
    g_src_w = int(g_src_h * ar_bot)
    if g_src_w > source_w:
        g_src_w = source_w
        g_src_h = int(g_src_w / ar_bot)

    g_src_y = top_black_y

    alignment = vision_info.get("content_alignment", "center")
    if "content_focus_x" in vision_info:
        focus_cx = int(vision_info["content_focus_x"] / 1000.0 * source_w)
        g_src_x = max(0, min(source_w - g_src_w, focus_cx - g_src_w // 2))
    else:
        focus = vision_info.get("content_focus", [100, 400, 800, 900])
        focus_cx = int(((focus[1] + focus[3]) / 2.0 / 1000.0) * source_w)

        # Shift gameplay horizontally based on focal point & avoiding streamer quadrant
        if raw_bx < source_w * 0.4:
            # Streamer is on left: shift content right to avoid webcam
            g_src_x = max(raw_bx + raw_bw - 50, min(source_w - g_src_w, focus_cx - g_src_w // 3))
        elif raw_bx > source_w * 0.6:
            # Streamer is on right: shift content left
            g_src_x = min(raw_bx - g_src_w + 50, max(0, focus_cx - g_src_w // 2))
        else:
            g_src_x = max(0, min(source_w - g_src_w, focus_cx - g_src_w // 2))

    g_src_w -= g_src_w % 2
    g_src_h -= g_src_h % 2
    g_src_x -= g_src_x % 2
    g_src_y -= g_src_y % 2

    filter_graph = (
        f"[0:v]crop={s_src_w}:{s_src_h}:{s_src_x}:{s_src_y},scale=1080:{top_h}[top];"
        f"[0:v]crop={g_src_w}:{g_src_h}:{g_src_x}:{g_src_y},scale=1080:{bot_h}[bottom];"
        f"[top][bottom]vstack=inputs=2[base]"
    )

    return filter_graph, top_h


def check_facecam_active(
    video_path: Path,
    cache: Optional[Any] = None,
    profile_path: Optional[Path] = None,
) -> Tuple[bool, Optional[Tuple[int, int, int, int]]]:
    """Check if the video has an active streamer facecam/webcam in a corner."""
    if cache and hasattr(cache, "exists") and cache.exists("facecam.json"):
        data = cache.load_json("facecam.json")
        if isinstance(data, dict):
            has_fc = data.get("has_facecam", False)
            box = tuple(data["box"]) if data.get("box") else None
            return has_fc, box
        elif isinstance(data, list) and len(data) == 4:
            return True, tuple(data)

    box = detect_facecam_box(video_path, profile_path=profile_path)
    if box:
        if cache and hasattr(cache, "save_json"):
            cache.save_json("facecam.json", {"has_facecam": True, "box": list(box)})
        return True, box
    else:
        if cache and hasattr(cache, "save_json"):
            cache.save_json("facecam.json", {"has_facecam": False, "box": None})
        return False, None


def build_blur_fill_filter(
    vision_info: Optional[Dict[str, Any]] = None,
    source_w: int = 1920,
    source_h: int = 1080,
    background: str = "black",
) -> str:
    """Build 9:16 letterbox layout matching reference shorts (e.g. iShowSpeed Funniest Moments).
    
    The FULL 16:9 stream (gameplay + corner webcam) is centered vertically.
    Streamer facecam remains visible in its natural corner.
    Top letterbox space (y=0..656) hosts the massive 3-line bold headline.
    Bottom letterbox space (y=1264..1920) hosts the kinetic dialogue subtitles.
    """
    if source_w <= source_h:
        # Input is already vertical or square - fit into 1080x1920
        return "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(1080-iw)/2:(1920-ih)/2:color=black[base]"

    if not vision_info:
        top_y, bot_y = 0, source_h
    else:
        letterbox = vision_info.get("content_letterbox", [0, source_h])
        top_y = int(letterbox[0])
        bot_y = int(letterbox[1])

    avail_h = bot_y - top_y
    if avail_h < 400 or (top_y < 15 and (source_h - bot_y) < 15):
        crop_part = ""
    else:
        avail_h -= avail_h % 2
        top_y -= top_y % 2
        crop_part = f"crop={source_w}:{avail_h}:0:{top_y},"

    if background == "blur":
        return (
            f"[0:v]{crop_part}scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,boxblur=20:2,eq=brightness=-0.3[bg];"
            f"[0:v]{crop_part}scale=1080:-2[fg];"
            f"[bg][fg]overlay=x=0:y=(H-h)/2[base]"
        )
    else:
        # Solid black canvas matching reference
        return f"[0:v]{crop_part}scale=1080:-2,pad=1080:1920:0:(1920-ih)/2:color=black[base]"


def build_reframed_crop_filter(
    vision_info: Optional[Dict[str, Any]] = None,
    source_w: int = 1920,
    source_h: int = 1080,
) -> str:
    """Build deframed vertical crop with selectable aspect ratios (default 3:4 letterbox, 9:16 full-bleed, 1:1, etc.)."""
    if source_w <= source_h:
        return "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(1080-iw)/2:(1920-ih)/2:color=black[base]"

    ratio_str = str(vision_info.get("content_ratio", "3:4")) if vision_info else "3:4"
    ratio_map = {
        "3:4": (3.0 / 4.0, 1080, 1440, 240),
        "9:16": (9.0 / 16.0, 1080, 1920, 0),
        "1:1": (1.0 / 1.0, 1080, 1080, 420),
        "4:5": (4.0 / 5.0, 1080, 1352, 284),
        "16:9": (16.0 / 9.0, 1080, 608, 656),
    }

    ar, target_w, target_h, pad_y = ratio_map.get(ratio_str, (3.0 / 4.0, 1080, 1440, 240))

    letterbox = vision_info.get("content_letterbox", [0, source_h]) if vision_info else [0, source_h]
    top_y = int(letterbox[0])
    top_y -= top_y % 2
    bot_y = int(letterbox[1])
    bot_y -= bot_y % 2
    avail_h = max(480, bot_y - top_y)
    avail_h -= avail_h % 2

    zoom = float(vision_info.get("content_zoom", 1.0)) if vision_info else 1.0
    zoom = max(1.0, min(2.5, zoom))

    if ar <= 16.0 / 9.0:
        base_h = avail_h
        base_w = int(base_h * ar)
        if base_w > source_w:
            base_w = source_w
            base_h = int(base_w / ar)
    else:
        base_w = source_w
        base_h = int(base_w / ar)

    crop_w = max(64, min(source_w, int(base_w / zoom)))
    crop_h = max(64, min(avail_h, int(base_h / zoom)))

    if vision_info and "content_focus_x" in vision_info:
        focus_cx = int(vision_info["content_focus_x"] / 1000.0 * source_w)
        crop_x = max(0, min(source_w - crop_w, focus_cx - crop_w // 2))
    else:
        focus = (vision_info.get("content_focus") if vision_info else None) or [100, 400, 800, 900]
        focus_cx = int(((focus[1] + focus[3]) / 2.0 / 1000.0) * source_w)

        has_facecam = bool(vision_info.get("has_facecam", False)) if vision_info else False
        s_box = vision_info.get("streamer_box") if vision_info else None

        if has_facecam and s_box and len(s_box) == 4:
            raw_bx = int(s_box[1] / 1000.0 * source_w)
            raw_bw = int((s_box[3] - s_box[1]) / 1000.0 * source_w)
            if raw_bx < source_w * 0.4:
                # Streamer is on left: ensure crop is strictly right of webcam
                crop_x = max(raw_bx + raw_bw - 50, min(source_w - crop_w, focus_cx - crop_w // 2))
            elif raw_bx > source_w * 0.6:
                # Streamer is on right: ensure crop is strictly left of webcam
                crop_x = min(raw_bx - crop_w + 50, max(0, focus_cx - crop_w // 2))
            else:
                crop_x = max(0, min(source_w - crop_w, focus_cx - crop_w // 2))
        else:
            crop_x = max(0, min(source_w - crop_w, focus_cx - crop_w // 2))

    crop_y = top_y + max(0, min(avail_h - crop_h, (avail_h - crop_h) // 2))

    crop_w -= crop_w % 2
    crop_h -= crop_h % 2
    crop_x -= crop_x % 2
    crop_y -= crop_y % 2

    if ratio_str == "9:16":
        return f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=1080:1920[base]"
    else:
        return f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=1080:{target_h},pad=1080:1920:0:{pad_y}:color=black[base]"


def get_layout_filter(
    layout_name: str,
    face_box: Optional[Tuple[int, int, int, int]] = None,
    source_w: int = 1920,
    source_h: int = 1080,
    vision_info: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[int]]:
    """Get the appropriate FFmpeg layout filter.
    
    Returns (filter_complex_string, seam_y_or_none).
    """
    if layout_name == "stack":
        if vision_info:
            return build_reference_stack_filter(vision_info, source_w=source_w, source_h=source_h)
        default_vision = {
            "streamer_box": list(face_box) if face_box else [550, 0, 1000, 420],
            "content_focus": [100, 500, 800, 950],
            "content_alignment": "right",
            "streamer_pct": 38,
            "content_pct": 62,
            "has_facecam": bool(face_box),
        }
        return build_reference_stack_filter(default_vision, source_w=source_w, source_h=source_h)
    elif layout_name in ("reframed", "crop", "deframed"):
        return build_reframed_crop_filter(vision_info, source_w=source_w, source_h=source_h), None
    elif layout_name == "blur-fill":
        return build_blur_fill_filter(vision_info, source_w=source_w, source_h=source_h), None


class SmoothedCameraman:
    """Handles smooth camera movement using the 'Heavy Tripod' algorithm.
    - Safe zone: only moves if subject leaves 25% center safe zone.
    - Jump confirmation: rejects single-frame detection noise/glitches.
    - Instant scene cut reset: snaps immediately across shot transitions.
    """
    def __init__(self, crop_width: int, video_width: int, jump_confirm_frames: int = 3):
        self.crop_width = crop_width
        self.video_width = video_width
        self.current_center_x = video_width / 2.0
        self.target_center_x = video_width / 2.0
        self.safe_zone_radius = crop_width * 0.25
        self.jump_confirm_frames = jump_confirm_frames
        self._pending_target = None
        self._pending_count = 0
        self._snap_pending = False

    def begin_scene(self):
        """Reset damping at a scene cut to instantly frame the new subject."""
        self._pending_target = None
        self._pending_count = 0
        self._snap_pending = True

    def update_target(self, new_center_x: float):
        if self._snap_pending:
            self._snap_pending = False
            self._pending_target = None
            self._pending_count = 0
            self.target_center_x = new_center_x
            self.current_center_x = new_center_x
            return

        if abs(new_center_x - self.target_center_x) > self.safe_zone_radius:
            if self._pending_target is not None and abs(new_center_x - self._pending_target) <= self.safe_zone_radius:
                self._pending_count += 1
            else:
                self._pending_target = new_center_x
                self._pending_count = 1
            if self._pending_count < self.jump_confirm_frames:
                return

        self._pending_target = None
        self._pending_count = 0
        self.target_center_x = new_center_x

    def get_center_x(self, smooth_factor: float = 0.15) -> float:
        diff = self.target_center_x - self.current_center_x
        # Only move the camera if the target has moved outside the safe zone
        if abs(diff) > self.safe_zone_radius:
            effective_pull = diff - (self.safe_zone_radius if diff > 0 else -self.safe_zone_radius)
            self.current_center_x += effective_pull * smooth_factor
        return self.current_center_x

