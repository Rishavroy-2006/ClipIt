"""Captions stage: Generate animated, word-by-word highlighted ASS subtitle files."""

from pathlib import Path
from typing import List, Dict, Any, Optional
import yaml
from clipforge.models import FullTranscript, ClipItem, WordTimestamp


DEFAULT_ASS_HEADER = """[Script Info]
Title: ClipForge Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary_color},&H000000FF,{outline_color},{shadow_color},-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def format_ass_time(seconds: float) -> str:
    """Format seconds into ASS timestamp format: H:MM:SS.cs."""
    seconds = max(0.0, seconds)
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"


def load_style(style_path: Optional[Path] = None) -> Dict[str, Any]:
    if style_path is None:
        style_path = Path("styles/bold-yellow.yaml")

    if style_path.exists():
        with open(style_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    return {
        "font": "Anton",
        "size": 80,
        "primary_color": "&H00FFFFFF",
        "highlight_color": "&H000AD6FF",
        "outline_color": "&H00000000",
        "shadow_color": "&H80000000",
        "outline": 5,
        "shadow": 2,
        "words_per_line": 3,
        "position": {"y": 0.65},
    }


def generate_clip_ass(
    clip: ClipItem,
    transcript: FullTranscript,
    output_path: Path,
    style_path: Optional[Path] = None,
) -> Path:
    """Generate an animated word-by-word ASS subtitle file for a single clip."""
    style = load_style(style_path)

    # Calculate vertical margin (Alignment 2 = bottom-center)
    # y=0.65 means 65% from the top, so distance from bottom is 1920 * (1 - 0.65) = 672
    pos_y = style.get("position", {}).get("y", 0.65)
    margin_v = int(1920 * (1.0 - pos_y))

    header = DEFAULT_ASS_HEADER.format(
        font=style.get("font", "Anton"),
        size=style.get("size", 80),
        primary_color=style.get("primary_color", "&H00FFFFFF"),
        outline_color=style.get("outline_color", "&H00000000"),
        shadow_color=style.get("shadow_color", "&H80000000"),
        outline=style.get("outline", 5),
        shadow=style.get("shadow", 2),
        margin_v=margin_v,
    )

    # Collect all words within clip timeline
    clip_words: List[WordTimestamp] = []
    for seg in transcript.segments:
        for w in seg.words:
            if w.end >= clip.start and w.start <= clip.end:
                clip_words.append(w)

    events: List[str] = []
    words_per_line = style.get("words_per_line", 3)
    highlight_color = style.get("highlight_color", "&H000AD6FF")

    # Group words into chunks of `words_per_line`
    for i in range(0, len(clip_words), words_per_line):
        line_chunk = clip_words[i : i + words_per_line]
        if not line_chunk:
            continue

        # For each word in the chunk, display the entire line with active word highlighted
        for active_idx, active_word in enumerate(line_chunk):
            w_start_rel = max(0.0, active_word.start - clip.start)
            w_end_rel = max(w_start_rel + 0.1, active_word.end - clip.start)

            # Build line text
            line_parts = []
            for j, w in enumerate(line_chunk):
                clean_text = w.w.upper() if style.get("uppercase", True) else w.w
                if j == active_idx:
                    # Pop animation: scale up slightly and apply highlight color
                    line_parts.append(f"{{\\c{highlight_color}\\t(0,100,\\fscx110\\fscy110)\\t(100,200,\\fscx100\\fscy100)}}{clean_text}{{\\r}}")
                else:
                    line_parts.append(clean_text)

            dialogue_text = " ".join(line_parts)
            start_str = format_ass_time(w_start_rel)
            end_str = format_ass_time(w_end_rel)
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{dialogue_text}")

    full_ass = header + "\n" + "\n".join(events) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_ass)

    return output_path
