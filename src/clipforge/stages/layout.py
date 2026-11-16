"""Layout stage: FFmpeg filtergraph construction for vertical 9:16 re-framing (blur-fill, stack, split)."""

from typing import Tuple, Optional
from clipforge.cache import CacheManager


def build_blur_fill_filter() -> str:
    """Build FFmpeg complex filter for 9:16 vertical video with blurred background."""
    # Scale background to fill 1080x1920, apply boxblur; center foreground scaled to width 1080
    return (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=luma_radius=min(h\\,w)/10:luma_power=2[bg];"
        "[0:v]scale=1080:-2[fg];"
        "[bg][fg]overlay=x=0:y=(H-h)/2[base]"
    )


def build_stack_filter(face_box: Optional[Tuple[int, int, int, int]] = None) -> str:
    """Build FFmpeg complex filter for 2-tier stacked gaming layout (gameplay top, facecam bottom)."""
    # If facebox is provided (x, y, w, h), crop facecam for bottom half; top half is center crop of gameplay
    if face_box:
        fx, fy, fw, fh = face_box
        return (
            f"[0:v]crop=in_w:in_w*9/16:0:0,scale=1080:960[top];"
            f"[0:v]crop={fw}:{fh}:{fx}:{fy},scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[bottom];"
            f"[top][bottom]vstack=inputs=2[base]"
        )
    # Default stack: split 16:9 video into two balanced crops
    return (
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[top];"
        "[0:v]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[bottom];"
        "[top][bottom]vstack=inputs=2[base]"
    )


def get_layout_filter(layout_name: str, face_box: Optional[Tuple[int, int, int, int]] = None) -> str:
    if layout_name == "stack":
        return build_stack_filter(face_box)
    # Default is blur-fill
    return build_blur_fill_filter()
