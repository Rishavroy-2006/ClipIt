"""Captions stage: Generate animated, word-by-word highlighted ASS subtitle files with auto-emojis and hook banner."""

import re
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
Style: HookBanner,{font},54,&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,3,10,0,8,80,80,150,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

EMOJI_MAP: Dict[str, str] = {
    "dead": "💀",
    "die": "💀",
    "died": "💀",
    "kill": "💀",
    "killed": "💀",
    "bro": "😭",
    "bruh": "😭",
    "no way": "😱",
    "insane": "😱",
    "crazy": "😱",
    "what": "⁉️",
    "omg": "🤯",
    "holy": "🤯",
    "win": "🏆",
    "clutch": "🏆",
    "goat": "🐐",
    "fail": "📉",
    "gta": "🚗",
    "car": "🚗",
    "fire": "🔥",
    "money": "💰",
    "cop": "🚨",
    "police": "🚨",
    "laugh": "😂",
    "funny": "😂",
    "lmao": "😂",
    "dad": "👨",
    "game": "🎮",
    "rockstar": "⭐",
}


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


def load_style(style_path_or_name: Optional[Any] = None) -> Dict[str, Any]:
    if style_path_or_name is None:
        style_path_or_name = "bold-yellow"

    if isinstance(style_path_or_name, str) and not style_path_or_name.endswith(".yaml"):
        style_path = Path(f"styles/{style_path_or_name}.yaml")
    else:
        style_path = Path(style_path_or_name)

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


def attach_emojis_to_text(word: str) -> str:
    """Check if word triggers an auto-emoji."""
    clean = re.sub(r"[^\w]", "", word.lower())
    if clean in EMOJI_MAP:
        return f"{word} {EMOJI_MAP[clean]}"
    return word


def generate_clip_ass(
    clip: ClipItem,
    transcript: FullTranscript,
    output_path: Path,
    style_path_or_name: Optional[Any] = None,
    show_hook_banner: bool = True,
) -> Path:
    """Generate an animated word-by-word ASS subtitle file with auto-emojis and top hook banner."""
    style = load_style(style_path_or_name)

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

    events: List[str] = []

    # 1. Top Banner Hook Overlay (First 2.5 seconds with fadeout)
    if show_hook_banner and clip.hook_line:
        hook_text = clip.hook_line.strip()
        # Add emoji if appropriate
        hook_words = [attach_emojis_to_text(w) for w in hook_text.split()]
        formatted_hook = " ".join(hook_words).upper()
        # Fadeout over last 300ms of the 2.5s window
        events.append(
            f"Dialogue: 1,0:00:00.00,0:00:02.50,HookBanner,,0,0,0,,{{\\fad(0,300)}} {formatted_hook} "
        )

    # 2. Collect words within clip timeline
    clip_words: List[WordTimestamp] = []
    for seg in transcript.segments:
        for w in seg.words:
            if w.end >= clip.start and w.start <= clip.end:
                clip_words.append(w)

    words_per_line = style.get("words_per_line", 3)
    highlight_color = style.get("highlight_color", "&H000AD6FF")

    # Group words into chunks of `words_per_line`
    for i in range(0, len(clip_words), words_per_line):
        line_chunk = clip_words[i : i + words_per_line]
        if not line_chunk:
            continue

        for active_idx, active_word in enumerate(line_chunk):
            w_start_rel = max(0.0, active_word.start - clip.start)
            w_end_rel = max(w_start_rel + 0.1, active_word.end - clip.start)

            line_parts = []
            for j, w in enumerate(line_chunk):
                word_str = attach_emojis_to_text(w.w)
                clean_text = word_str.upper() if style.get("uppercase", True) else word_str
                if j == active_idx:
                    # Pop animation: scale up slightly with highlight color
                    line_parts.append(
                        f"{{\\c{highlight_color}\\t(0,100,\\fscx112\\fscy112)\\t(100,200,\\fscx100\\fscy100)}}{clean_text}{{\\r}}"
                    )
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
