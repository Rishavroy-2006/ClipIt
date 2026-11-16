"""Render stage: Render vertical 1080x1920 MP4 clips with hardware acceleration, audio normalization, and subtitles."""

import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from clipforge.cache import CacheManager
from clipforge.config import VideoConfig
from clipforge.models import FullTranscript, ClipItem
from clipforge.stages.layout import get_layout_filter, detect_facecam_box
from clipforge.stages.captions import generate_clip_ass


def render_single_clip(
    clip: ClipItem,
    source_video: Path,
    output_dir: Path,
    transcript: FullTranscript,
    cache: CacheManager,
    video_config: VideoConfig,
    codec: str = "h264_videotoolbox",
    fonts_dir: Path = Path("assets/fonts"),
    face_box: Optional[Tuple[int, int, int, int]] = None,
    style_name: str = "bold-yellow",
) -> Path:
    """Render a single vertical clip with layout and burned-in animated subtitles."""
    out_mp4 = output_dir / f"{clip.id}.mp4"
    out_json = output_dir / f"{clip.id}.json"
    ass_path = cache.path(f"{clip.id}.ass")

    # Generate ASS subtitles with auto-emojis and top hook banner
    generate_clip_ass(clip, transcript, ass_path, style_path_or_name=style_name, show_hook_banner=True)

    # Base layout filter
    layout_filter = get_layout_filter(clip.layout, face_box=face_box)

    # Escape path for ffmpeg filter string
    escaped_ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")
    escaped_fonts = str(fonts_dir.resolve()).replace("\\", "/").replace(":", "\\:")

    # Complete complex filter graph: layout -> burn subtitles
    filter_complex = f"{layout_filter};[base]ass=filename='{escaped_ass}':fontsdir='{escaped_fonts}'[v]"

    # Build FFmpeg command
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{clip.start:.3f}",
        "-to", f"{clip.end:.3f}",
        "-i", str(source_video.resolve()),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", codec,
    ]

    # Codec specific settings
    if codec == "h264_videotoolbox":
        cmd.extend(["-b:v", "6000k"])
    else:
        cmd.extend(["-crf", str(video_config.crf), "-preset", "fast"])

    # Audio normalization and output formatting
    cmd.extend([
        "-c:a", "aac",
        "-b:a", "192k",
        "-af", f"loudnorm=I={video_config.audio_lufs}:LRA=11:TP=-1.5",
        "-movflags", "+faststart",
        str(out_mp4.resolve()),
    ])

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    # Write sidecar JSON
    clip_dict = clip.model_dump()
    with open(out_json, "w", encoding="utf-8") as f:
        import json
        json.dump(clip_dict, f, indent=2, ensure_ascii=False)

    return out_mp4


def run_render(
    cache: CacheManager,
    transcript: FullTranscript,
    clips: List[ClipItem],
    video_config: VideoConfig,
    out_base: Path = Path("out"),
    style_name: str = "bold-yellow",
) -> List[Path]:
    """Render all selected clips with progress bar."""
    source_mp4 = cache.path("source.mp4")
    out_dir = out_base / cache.video_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Check for facecam box if any clip uses 'stack'
    face_box: Optional[Tuple[int, int, int, int]] = None
    if cache.exists("facecam.json"):
        raw_box = cache.load_json("facecam.json")
        if raw_box:
            face_box = tuple(raw_box)
    elif any(c.layout == "stack" for c in clips):
        print("Detecting streamer facecam position using OpenCV YuNet...")
        detected = detect_facecam_box(source_mp4)
        if detected:
            face_box = detected
            cache.save_json("facecam.json", list(detected))
            print(f"✔ Found stable facecam rectangle: x={detected[0]}, y={detected[1]}, w={detected[2]}, h={detected[3]}")
        else:
            print("Notice: No consistent facecam detected, falling back to centered split.")

    rendered_files: List[Path] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(f"Rendering {len(clips)} vertical clips...", total=len(clips))

        for clip in clips:
            progress.update(task, description=f"Rendering {clip.id}: '{clip.title[:30]}...'")
            try:
                mp4_path = render_single_clip(
                    clip=clip,
                    source_video=source_mp4,
                    output_dir=out_dir,
                    transcript=transcript,
                    cache=cache,
                    video_config=video_config,
                    codec=video_config.draft_codec,
                    face_box=face_box,
                    style_name=style_name,
                )
                rendered_files.append(mp4_path)
            except subprocess.CalledProcessError as e:
                # Fallback to libx264 if videotoolbox hits an edge case
                print(f"Warning: {video_config.draft_codec} failed for {clip.id}, retrying with libx264...")
                mp4_path = render_single_clip(
                    clip=clip,
                    source_video=source_mp4,
                    output_dir=out_dir,
                    transcript=transcript,
                    cache=cache,
                    video_config=video_config,
                    codec="libx264",
                    face_box=face_box,
                    style_name=style_name,
                )
                rendered_files.append(mp4_path)
            progress.advance(task)

    return rendered_files
