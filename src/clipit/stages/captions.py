"""Captions stage: Generate animated, word-by-word highlighted ASS subtitle files with auto-emojis and hook banner."""

import re
from pathlib import Path
from typing import List, Dict, Any, Optional
import yaml
from clipit.models import FullTranscript, ClipItem, WordTimestamp


DEFAULT_ASS_HEADER = """[Script Info]
Title: ClipIt Subtitles
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary_color},&H000000FF,{outline_color},{shadow_color},-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,60,60,{margin_v},1
Style: HookBanner,{font},54,&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,3,10,0,8,80,80,90,1
Style: TopHeader,{font},{top_fs},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,{top_sx},100,2,0,1,8,3,8,20,20,{top_margin_v},1

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
    "speed": "⚡",
    "shock": "⚡",
    "run": "🏃",
    "fast": "💨",
    "rage": "😡",
    "angry": "🤬",
    "boom": "💥",
    "shot": "💥",
    "w": "🏆",
    "cap": "🧢",
}


def strip_unsupported_ass_chars(text: str) -> str:
    """Strip emoji and non-Latin symbols that cause missing glyph boxes [] in ASS font engines."""
    clean = re.sub(r"[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf\ufe00-\ufe0f]", "", text)
    return re.sub(r"\s+", " ", clean).strip()


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
    """Check if word triggers an auto-emoji (for metadata/titles)."""
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
    seam_y: Optional[int] = None,
    layout_name: Optional[str] = None,
    creator_name: Optional[str] = "ISHOWSPEED",
    topic_name: Optional[str] = "GTA 6 TRAILER 2",
    text_position: Optional[str] = None,
    content_ratio: Optional[str] = "3:4",
) -> Path:
    """Generate an animated word-by-word ASS subtitle file with auto-emojis, layout-aware headlines, and custom text positioning."""
    style = load_style(style_path_or_name)

    # 1. Determine Subtitle Vertical Position (MarginV)
    # text_position can be 'above', 'middle', 'bottom'
    if text_position == "above":
        # Positions subtitles in the upper region / top header space
        margin_v = 1640
        top_fs, top_sx, top_margin_v = 60, 100, 240
    elif text_position == "middle":
        # Positions subtitles in the center or across dividing seam
        if seam_y is not None and layout_name == "stack":
            margin_v = max(100, 1920 - seam_y - 25)
        else:
            margin_v = 940
        top_fs, top_sx, top_margin_v = 70, 100, 80
    elif text_position == "bottom":
        # Positions subtitles in the standard lower-third (bottom of frame/letterbox)
        if layout_name == "blur-fill":
            margin_v = 560
        else:
            margin_v = 200
        top_fs, top_sx, top_margin_v = 72, 105, 60
    else:
        # Default positioning based on layout
        if layout_name == "blur-fill":
            margin_v = 560
        elif seam_y is not None:
            margin_v = max(100, 1920 - seam_y - 25)
        elif clip.layout == "stack":
            margin_v = 1175
        else:
            pos_y = style.get("position", {}).get("y", 0.65)
            margin_v = int(1920 * (1.0 - pos_y))
        top_fs, top_sx, top_margin_v = 70, 100, 70

    # 2. Heading Sizes for Blur-Fill or 3:4 Letterboxed Title
    clean_title = strip_unsupported_ass_chars(clip.title)
    clean_title = re.sub(r"[#@].*", "", clean_title).strip().upper()
    if not clean_title:
        clean_title = strip_unsupported_ass_chars(clip.hook_line or "VIRAL MOMENT").upper()

    if layout_name == "blur-fill":
        l1 = f"{creator_name.upper()} REACTS" if creator_name else "LIVE REACTION"
        l2_raw = clean_title
        l3 = topic_name.upper() if topic_name else "GTA 6 TRAILER 2"

        max_len = max(len(l1), len(l2_raw), len(l3))
        if max_len <= 16:
            top_fs, top_sx = 162, 135
        elif max_len <= 19:
            top_fs, top_sx = 154, 130
        elif max_len <= 23:
            top_fs, top_sx = 138, 125
        else:
            top_fs, top_sx = 120, 120
        top_margin_v = 115
    elif layout_name == "reframed" and content_ratio in ("3:4", "1:1", "4:5"):
        # Podcast / Hormozi style: bold 2-line header in top black bar
        words = clean_title.split()
        if len(words) >= 4:
            split_idx = len(words) // 2
            l1 = " ".join(words[:split_idx])
            l2_raw = " ".join(words[split_idx:])
        else:
            l1 = clean_title
            l2_raw = ""
        top_fs = 74 if len(clean_title) < 25 else 62
        top_sx = 105
        top_margin_v = 55

    header = DEFAULT_ASS_HEADER.format(
        font=style.get("font", "Anton"),
        size=style.get("size", 80),
        primary_color=style.get("primary_color", "&H00FFFFFF"),
        outline_color=style.get("outline_color", "&H00000000"),
        shadow_color=style.get("shadow_color", "&H80000000"),
        outline=style.get("outline", 5),
        shadow=style.get("shadow", 2),
        margin_v=margin_v,
        top_fs=top_fs,
        top_sx=top_sx,
        top_margin_v=top_margin_v,
    )

    events: List[str] = []

    # 1. Headline / Hook Overlay
    if layout_name == "blur-fill":
        # Massive 3-line persistent headline overlay matching iShowSpeed Funniest Moments reference
        l2 = f"{{\\c&H1DF8E8&}}{l2_raw}{{\\c&HFFFFFF&}}"
        duration_str = format_ass_time(clip.end - clip.start)
        headline_text = f"{l1}\\N{l2}\\N{l3}"
        events.append(f"Dialogue: 2,0:00:00.00,{duration_str},TopHeader,,0,0,0,,{headline_text}")
    elif layout_name == "reframed" and content_ratio in ("3:4", "1:1", "4:5") and text_position != "above":
        # Hormozi / Podcast reference style header with cyan glowing highlight in top black bar
        duration_str = format_ass_time(clip.end - clip.start)
        if l2_raw:
            headline_text = f"{l1}\\N{{\\c&H1DF8E8&}}{l2_raw}{{\\c&HFFFFFF&}}"
        else:
            headline_text = l1
        events.append(f"Dialogue: 2,0:00:00.00,{duration_str},TopHeader,,0,0,0,,{headline_text}")
    elif show_hook_banner and clip.hook_line:
        hook_text = clip.hook_line.strip()
        # Clean non-Latin characters so Anton font renders crisp text with no missing glyph boxes
        clean_hook = strip_unsupported_ass_chars(hook_text).upper()
        if clean_hook:
            # Fadeout over last 300ms of the 2.5s window
            events.append(
                f"Dialogue: 1,0:00:00.00,0:00:02.50,HookBanner,,0,0,0,,{{\\fad(0,300)}} {clean_hook} "
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
                word_str = strip_unsupported_ass_chars(w.w)
                clean_text = word_str.upper() if style.get("uppercase", True) else word_str
                if j == active_idx:
                    # Bold MrBeast / TikTok dynamic spring bounce with active-word pop
                    line_parts.append(
                        f"{{\\c{highlight_color}\\t(0,70,\\fscx126\\fscy126)\\t(70,160,\\fscx100\\fscy100)}}{clean_text}{{\\r}}"
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
