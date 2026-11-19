"""Captions stage: Generate animated, word-by-word highlighted ASS subtitle files with auto-emojis and hook banner."""

import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import yaml
from clipit.models import FullTranscript, ClipItem, WordTimestamp


def _dim_ass_color(ass_color: str, opacity: float = 0.75) -> str:
    """Scale RGB toward black so inactive words are dimmed in ASS &HAABBGGRR format."""
    clean = str(ass_color or "").strip().replace("&H", "").replace("&", "")
    if len(clean) == 8:
        aa = clean[0:2]
        bb = int(clean[2:4], 16)
        gg = int(clean[4:6], 16)
        rr = int(clean[6:8], 16)
    elif len(clean) == 6:
        aa = "00"
        rr = int(clean[0:2], 16)
        gg = int(clean[2:4], 16)
        bb = int(clean[4:6], 16)
    else:
        return "&H00C0C0C0"

    factor = 0.5 + 0.5 * max(0.05, min(1.0, opacity))
    rr_dim = min(255, max(0, round(rr * factor)))
    gg_dim = min(255, max(0, round(gg * factor)))
    bb_dim = min(255, max(0, round(bb * factor)))
    return f"&H{aa}{bb_dim:02X}{gg_dim:02X}{rr_dim:02X}"


_FONT_METRIC_CACHE: Dict[Tuple[str, int], Any] = {}


def get_font_metric(font_name: str, font_size: int):
    """Retrieve or cache Pillow ImageFont metric for precise advance measurement."""
    key = (font_name, font_size)
    if key in _FONT_METRIC_CACHE:
        return _FONT_METRIC_CACHE[key]

    font_file = None
    search_dirs = [Path("assets/fonts"), Path(__file__).resolve().parent.parent.parent.parent / "assets/fonts"]
    for d in search_dirs:
        if not d.exists():
            continue
        clean_name = font_name.lower().replace(" ", "").replace("-", "")
        for f in d.glob("*.ttf"):
            stem_clean = f.stem.lower().replace(" ", "").replace("-", "")
            if clean_name in stem_clean or stem_clean.startswith(clean_name[:5]):
                font_file = f
                break
        if font_file:
            break
        anton = d / "Anton-Regular.ttf"
        if anton.exists():
            font_file = anton

    if font_file and font_file.exists():
        try:
            from PIL import ImageFont
            metric = ImageFont.truetype(str(font_file), font_size)
            _FONT_METRIC_CACHE[key] = metric
            return metric
        except Exception:
            pass
    return None


def measure_line_width(font_name: str, font_size: int, words: List[str]) -> float:
    """Measure exact text pixel width using Pillow TrueType font metrics to prevent subtitle clipping."""
    metric = get_font_metric(font_name, font_size)
    text = " ".join(words)
    if metric is not None:
        try:
            return float(metric.getlength(text))
        except Exception:
            pass
    return float(len(text) * font_size * 0.55)



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
Style: TopHeader,{font},{top_fs},&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,-1,0,0,0,{top_sx},100,1,0,1,4,2,8,40,40,{top_margin_v},1

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
    creator_name: Optional[str] = None,
    topic_name: Optional[str] = None,
    text_position: Optional[str] = None,
    content_ratio: Optional[str] = "3:4",
    title_size: Optional[int] = None,
    caption_size: Optional[int] = None,
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
    clean_title = re.sub(r"^\[?Part\s*\d+\]?:?\s*", "", clean_title, flags=re.IGNORECASE)
    clean_title = re.sub(r"[#@].*", "", clean_title).strip().upper()
    if not clean_title:
        clean_title = strip_unsupported_ass_chars(clip.hook_line or "VIRAL MOMENT").upper()

    font_family = style.get("font", "Anton")
    title_words = clean_title.split()

    creator_tag = None
    if creator_name and creator_name.strip():
        c_clean = creator_name.strip().upper()
        c_alnum = re.sub(r"[^A-Z0-9]", "", c_clean)
        title_alnum = re.sub(r"[^A-Z0-9]", "", clean_title)
        # Avoid redundant creator tag if creator name/handle is already part of the title
        is_in_title = c_alnum in title_alnum or ("SPEED" in title_alnum if "SPEED" in c_alnum else False)
        if not is_in_title:
            creator_tag = f"• {c_clean} •"

    topic_tag = None
    if topic_name and topic_name.strip():
        clean_top = topic_name.strip().upper()
        if clean_top not in clean_title:
            topic_tag = f"# {clean_top}"

    # Smart split title into 1 or 2 lines using TrueType font measurement
    if len(title_words) <= 1:
        line1, line2 = clean_title, ""
    else:
        total_w = measure_line_width(font_family, 84, title_words)
        if total_w <= 720.0 and len(title_words) <= 3:
            line1, line2 = clean_title, ""
        else:
            best_split = len(title_words) // 2
            best_diff = float("inf")
            for i in range(1, len(title_words)):
                w1 = measure_line_width(font_family, 84, title_words[:i])
                w2 = measure_line_width(font_family, 84, title_words[i:])
                if w1 <= 920.0 and w2 <= 920.0:
                    diff = abs(w1 - w2)
                    if diff < best_diff:
                        best_diff = diff
                        best_split = i
            line1 = " ".join(title_words[:best_split])
            line2 = " ".join(title_words[best_split:])

    if layout_name == "blur-fill":
        longest_str = max([line1, line2, creator_tag or "", topic_tag or ""], key=lambda s: len(s) if s else 0)
        safe_fs = int(920.0 / (max(8, len(longest_str)) * 0.58))
        base_target_fs = title_size if title_size and title_size > 30 else 84
        top_fs = max(42, min(base_target_fs, safe_fs))
        top_sx = 100

        # Calculate optical vertical centering in the 656px top letterbox bar (midpoint y = 328)
        # Approximate line heights: badge ~ 55px, title line ~ top_fs * 1.15, topic ~ 48px
        total_block_h = 0
        if creator_tag:
            total_block_h += 55
        if line1:
            total_block_h += int(top_fs * 1.15)
        if line2:
            total_block_h += int(top_fs * 1.15)
        if topic_tag:
            total_block_h += 48

        top_margin_v = max(40, int(328 - total_block_h / 2) + 12)
    elif layout_name == "reframed" and content_ratio in ("3:4", "1:1", "4:5"):
        longest_str = max([line1, line2], key=lambda s: len(s) if s else 0)
        safe_fs = int(920.0 / (max(8, len(longest_str)) * 0.58))
        base_target_fs = title_size if title_size and title_size > 30 else (72 if len(longest_str) < 20 else 60)
        top_fs = max(38, min(base_target_fs, safe_fs))
        top_sx = 100
        top_margin_v = 55

    # Subtitle / caption font size & style parameters
    caption_fs = caption_size if caption_size and caption_size >= 40 else style.get("size", 80)
    base_opacity = float(style.get("base_opacity", 0.75))
    dimmed_primary = _dim_ass_color(style.get("primary_color", "&H00FFFFFF"), base_opacity)
    reveal_mode = bool(style.get("reveal", False))

    header = DEFAULT_ASS_HEADER.format(
        font=font_family,
        size=caption_fs,
        primary_color=dimmed_primary,
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
        duration_str = format_ass_time(clip.end - clip.start)
        hl_col = style.get("highlight_color", "&H000AD6FF")
        pri_col = style.get("primary_color", "&H00FFFFFF")

        parts = []
        if creator_tag:
            parts.append(f"{{\\fs46\\c&H00D0D0D0\\3c&H00000000\\bord3}}{creator_tag}")
        if line1 and line2:
            parts.append(f"{{\\fs{top_fs}\\c{pri_col}}}{line1}\\N{{\\c{hl_col}}}{line2}{{\\r}}")
        elif line1:
            parts.append(f"{{\\fs{top_fs}\\c{hl_col}}}{line1}{{\\r}}")
        if topic_tag:
            parts.append(f"{{\\fs44\\c&H00A0A0A0\\3c&H00000000\\bord2.5}}{topic_tag}")

        headline_text = "\\N".join(parts)
        events.append(f"Dialogue: 2,0:00:00.00,{duration_str},TopHeader,,0,0,0,,{headline_text}")
    elif layout_name == "reframed" and content_ratio in ("3:4", "1:1", "4:5") and text_position != "above":
        duration_str = format_ass_time(clip.end - clip.start)
        hl_col = style.get("highlight_color", "&H000AD6FF")
        pri_col = style.get("primary_color", "&H00FFFFFF")
        if line2:
            headline_text = f"{{\\fs{top_fs}\\c{pri_col}}}{line1}\\N{{\\c{hl_col}}}{line2}{{\\r}}"
        else:
            headline_text = f"{{\\fs{top_fs}\\c{hl_col}}}{line1}{{\\r}}"
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

    # Smart phrase chunking:
    # Do NOT blindly chunk words across long pauses or sentence boundaries!
    # Measure line width with TrueType Pillow font metrics to prevent subtitle clipping.
    SAFE_WIDTH = 900.0
    line_chunks: List[List[WordTimestamp]] = []
    curr_chunk: List[WordTimestamp] = []

    for idx, w in enumerate(clip_words):
        clean_w = strip_unsupported_ass_chars(w.w).strip()
        if not clean_w:
            continue
        word_text = clean_w.upper() if style.get("uppercase", True) else clean_w

        # Test if adding this word would overflow SAFE_WIDTH
        test_words = [strip_unsupported_ass_chars(item.w).strip() for item in curr_chunk] + [word_text]
        test_width = measure_line_width(font_family, caption_fs, test_words)
        too_wide = len(curr_chunk) >= 1 and test_width > SAFE_WIDTH

        is_last_word = (idx == len(clip_words) - 1)
        if not is_last_word:
            next_w = clip_words[idx + 1]
            gap = max(0.0, next_w.start - w.end)
            ends_sentence = bool(re.search(r"[.!?]$", w.w.strip()))
            has_pause = gap > 0.40
            at_max_words = len(curr_chunk) >= words_per_line

            if too_wide:
                line_chunks.append(curr_chunk)
                curr_chunk = [w]
            elif ends_sentence or has_pause or at_max_words:
                curr_chunk.append(w)
                line_chunks.append(curr_chunk)
                curr_chunk = []
            else:
                curr_chunk.append(w)
        else:
            if too_wide:
                line_chunks.append(curr_chunk)
                curr_chunk = [w]
            else:
                curr_chunk.append(w)
            line_chunks.append(curr_chunk)
            curr_chunk = []

    # Identify any single lone word wider than SAFE_WIDTH and calculate scale percentage
    single_word_squeeze: Dict[float, int] = {}
    for chunk in line_chunks:
        if len(chunk) == 1:
            cw = strip_unsupported_ass_chars(chunk[0].w).strip()
            cw_text = cw.upper() if style.get("uppercase", True) else cw
            w_px = measure_line_width(font_family, caption_fs, [cw_text])
            if w_px > SAFE_WIDTH:
                pct = max(50, int(100 * SAFE_WIDTH / w_px))
                single_word_squeeze[chunk[0].start] = pct

    # Generate continuous dialogue events for each chunk with zero gaps and zero overlap
    for chunk_idx, line_chunk in enumerate(line_chunks):
        if not line_chunk:
            continue

        # Enforce strictly monotonic word start timestamps within this chunk
        rel_starts = []
        last_s = -1.0
        for w in line_chunk:
            s = max(0.0, w.start - clip.start)
            if s <= last_s:
                s = last_s + 0.08
            rel_starts.append(s)
            last_s = s

        # Lookahead to start of next chunk (relative to clip.start) to prevent collisions
        next_chunk_start_rel = None
        if chunk_idx + 1 < len(line_chunks) and line_chunks[chunk_idx + 1]:
            next_chunk_start_rel = max(0.0, line_chunks[chunk_idx + 1][0].start - clip.start)

        for active_idx, active_word in enumerate(line_chunk):
            w_start_rel = rel_starts[active_idx]

            # Continuous, non-flickering display:
            if active_idx < len(line_chunk) - 1:
                # Seamless partition: end of current word == start of next word
                w_end_rel = rel_starts[active_idx + 1]
            else:
                # Last word in this line chunk: hold text with a short sustain (+0.22s)
                sustain_end = max(w_start_rel + 0.15, active_word.end - clip.start + 0.22)
                if next_chunk_start_rel is not None:
                    # Prevent collision with the start of the next line chunk
                    w_end_rel = min(sustain_end, max(w_start_rel + 0.10, next_chunk_start_rel - 0.02))
                else:
                    w_end_rel = sustain_end

            if w_end_rel <= w_start_rel:
                w_end_rel = w_start_rel + 0.08

            line_parts = []
            for j, w in enumerate(line_chunk):
                word_str = strip_unsupported_ass_chars(w.w)
                clean_text = word_str.upper() if style.get("uppercase", True) else word_str
                squeeze_tag = (
                    f"\\fscx{single_word_squeeze[w.start]}\\fscy{single_word_squeeze[w.start]}"
                    if w.start in single_word_squeeze
                    else ""
                )

                if j == active_idx:
                    # Bold MrBeast / TikTok dynamic spring bounce with active-word pop and bright highlight color
                    line_parts.append(
                        f"{{\\c{highlight_color}{squeeze_tag}\\t(0,70,\\fscx126\\fscy126)\\t(70,160,\\fscx100\\fscy100)}}{clean_text}{{\\r}}"
                    )
                elif reveal_mode and j > active_idx:
                    # Hormozi progressive reveal: upcoming words are transparent placeholder slots
                    line_parts.append(f"{{\\alpha&HFF&}}{clean_text}{{\\r}}")
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
