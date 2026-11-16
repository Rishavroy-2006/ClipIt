"""Layout stage: Facecam auto-detection using OpenCV YuNet and FFmpeg filtergraphs for 9:16 vertical re-framing."""

from pathlib import Path
from typing import Tuple, Optional, List
import cv2
import numpy as np


def detect_facecam_box(video_path: Path, max_samples: int = 12) -> Optional[Tuple[int, int, int, int]]:
    """Detect persistent streamer facecam box by sampling frames across the stream."""
    model_path = Path("assets/models/face_detection_yunet_2023mar.onnx")
    if not model_path.exists():
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

    # Sample timestamps across video (skipping first/last 10 seconds)
    sample_secs = np.linspace(min(15, duration_s * 0.1), max(20, duration_s * 0.9), max_samples)
    detections: List[Tuple[int, int, int, int]] = []

    for sec in sample_secs:
        frame_no = int(sec * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ret, frame = cap.read()
        if not ret:
            continue
        _, faces = detector.detect(frame)
        if faces is not None:
            for face in faces:
                fx, fy, fw, fh = [int(v) for v in face[:4]]
                # Filter out tiny spurious detections or full-frame detections
                if 40 <= fw <= w * 0.6 and 40 <= fh <= h * 0.6:
                    detections.append((fx, fy, fw, fh))

    cap.release()

    if len(detections) < 3:
        return None

    # Find the most consistent facecam cluster
    # Facecams stay in the same corner across the entire stream
    quadrants: List[List[Tuple[int, int, int, int]]] = [[], [], [], []] # TL, TR, BL, BR
    mid_x, mid_y = w // 2, h // 2

    for box in detections:
        bx, by, bw, bh = box
        cx, cy = bx + bw // 2, by + bh // 2
        if cx < mid_x and cy < mid_y:
            quadrants[0].append(box) # Top-Left
        elif cx >= mid_x and cy < mid_y:
            quadrants[1].append(box) # Top-Right
        elif cx < mid_x and cy >= mid_y:
            quadrants[2].append(box) # Bottom-Left
        else:
            quadrants[3].append(box) # Bottom-Right

    # Pick quadrant with most consistent detections
    best_quad = max(quadrants, key=len)
    if len(best_quad) < 2:
        return None

    # Compute median bounding box
    med_x = int(np.median([b[0] for b in best_quad]))
    med_y = int(np.median([b[1] for b in best_quad]))
    med_w = int(np.median([b[2] for b in best_quad]))
    med_h = int(np.median([b[3] for b in best_quad]))

    # Expand box by 1.8x width and 2.0x height to capture shoulders and mic
    pad_w = int(med_w * 0.4)
    pad_h = int(med_h * 0.5)

    final_x = max(0, med_x - pad_w)
    final_y = max(0, med_y - pad_h)
    final_w = min(w - final_x, med_w + 2 * pad_w)
    final_h = min(h - final_y, med_h + 2 * pad_h)

    # Ensure even dimensions for ffmpeg codecs
    final_w = final_w - (final_w % 2)
    final_h = final_h - (final_h % 2)

    return (final_x, final_y, final_w, final_h)


def build_blur_fill_filter() -> str:
    """Standard 9:16 layout with centered gameplay and blurred background."""
    return (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=luma_radius=min(h\\,w)/10:luma_power=2[bg];"
        "[0:v]scale=1080:-2[fg];"
        "[bg][fg]overlay=x=0:y=(H-h)/2[base]"
    )


def build_stack_filter(face_box: Optional[Tuple[int, int, int, int]] = None) -> str:
    """Stacked gaming layout: Gameplay on top half (1080x960), Streamer facecam on bottom half (1080x960)."""
    if face_box:
        fx, fy, fw, fh = face_box
        return (
            # Top: Gameplay centered crop scaled to 1080x960
            f"[0:v]scale=-2:960,crop=1080:960[top];"
            # Bottom: Cropped streamer webcam scaled and centered into 1080x960
            f"[0:v]crop={fw}:{fh}:{fx}:{fy},"
            f"scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[bottom];"
            f"[top][bottom]vstack=inputs=2[base]"
        )
    # Default stack when no box detected: top and bottom halves split
    return (
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[top];"
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[bottom];"
        "[top][bottom]vstack=inputs=2[base]"
    )


def get_layout_filter(layout_name: str, face_box: Optional[Tuple[int, int, int, int]] = None) -> str:
    if layout_name == "stack":
        return build_stack_filter(face_box)
    return build_blur_fill_filter()
