"""Render stage: Render vertical 1080x1920 MP4 clips with hardware acceleration, audio normalization, and subtitles."""

import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from clipforge.cache import CacheManager
from clipforge.config import VideoConfig, get_available_encoder
from clipforge.models import FullTranscript, ClipItem
from clipforge.stages.layout import get_layout_filter, detect_facecam_box, check_facecam_active
from clipforge.stages.captions import generate_clip_ass
from clipforge.stages.vision_reframing import analyze_clip_vision


def render_variant_file(
    clip: ClipItem,
    source_video: Path,
    out_mp4: Path,
    layout_name: str,
    transcript: FullTranscript,
    cache: CacheManager,
    video_config: VideoConfig,
    codec: str = "auto",
    fonts_dir: Path = Path("assets/fonts"),
    face_box: Optional[Tuple[int, int, int, int]] = None,
    style_name: str = "bold-yellow",
    vision_info: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> Path:
    """Render a specific layout variant of a clip."""
    if not force and out_mp4.exists() and out_mp4.stat().st_size > 1000:
        return out_mp4

    if codec == "auto":
        codec = getattr(video_config, "draft_codec", None) or get_available_encoder()

    layout_filter, seam_y = get_layout_filter(layout_name, face_box=face_box, vision_info=vision_info)

    ass_suffix = f"_{layout_name}" if layout_name != "stack" else ""
    ass_path = cache.path(f"{clip.id}{ass_suffix}.ass")
    text_pos = vision_info.get("text_position") if vision_info else None
    content_ratio = vision_info.get("content_ratio", "3:4") if vision_info else "3:4"

    generate_clip_ass(
        clip,
        transcript,
        ass_path,
        style_path_or_name=style_name,
        show_hook_banner=True,
        seam_y=seam_y if layout_name == "stack" else None,
        layout_name=layout_name,
        text_position=text_pos,
        content_ratio=content_ratio,
    )

    # Cross-platform relative path handling to avoid Windows drive letter colons in FFmpeg filtergraph
    try:
        rel_ass = ass_path.relative_to(Path.cwd()).as_posix()
        escaped_ass = rel_ass.replace(":", "\\:")
    except ValueError:
        escaped_ass = ass_path.resolve().as_posix().replace(":", "\\:")

    try:
        rel_fonts = fonts_dir.relative_to(Path.cwd()).as_posix()
        escaped_fonts = rel_fonts.replace(":", "\\:")
    except ValueError:
        escaped_fonts = fonts_dir.resolve().as_posix().replace(":", "\\:")

    filter_complex = f"{layout_filter};[base]ass=filename='{escaped_ass}':fontsdir='{escaped_fonts}'[v]"

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

    if codec == "h264_videotoolbox":
        cmd.extend(["-b:v", "6000k"])
    elif codec == "h264_nvenc":
        cmd.extend(["-b:v", "6000k", "-preset", "p4"])
    elif codec in ("h264_qsv", "h264_amf"):
        cmd.extend(["-b:v", "6000k"])
    else:
        cmd.extend(["-crf", str(video_config.crf), "-preset", "fast"])

    audio_filter = (
        f"highpass=f=80,equalizer=f=3000:t=q:w=1.5:g=2.5,"
        f"acompressor=threshold=-18dB:ratio=3:attack=10:release=120:makeup=2dB,"
        f"loudnorm=I={video_config.audio_lufs}:LRA=9:TP=-1.0"
    )

    cmd.extend([
        "-c:a", "aac",
        "-b:a", "192k",
        "-af", audio_filter,
        "-movflags", "+faststart",
        str(out_mp4.resolve()),
    ])

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        # Fallback to libx264 if videotoolbox fails on an edge case
        fallback_cmd = [
            "ffmpeg", "-y",
            "-ss", f"{clip.start:.3f}",
            "-to", f"{clip.end:.3f}",
            "-i", str(source_video.resolve()),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-crf", "18", "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-af", audio_filter,
            "-movflags", "+faststart",
            str(out_mp4.resolve()),
        ]
        subprocess.run(fallback_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    return out_mp4


def render_single_clip(
    clip: ClipItem,
    source_video: Path,
    output_dir: Path,
    transcript: FullTranscript,
    cache: CacheManager,
    video_config: VideoConfig,
    codec: str = "auto",
    fonts_dir: Path = Path("assets/fonts"),
    face_box: Optional[Tuple[int, int, int, int]] = None,
    style_name: str = "bold-yellow",
    render_variants: bool = True,
    force: bool = False,
) -> Path:
    """Render vertical clip variants (Reaction Stacked, Blur-Fill, and Deframed 9:16)."""
    vision_info = analyze_clip_vision(clip, source_video, cache)
    has_facecam = vision_info.get("has_facecam", bool(face_box))

    variants_list: List[Dict[str, Any]] = []

    if has_facecam:
        # Variant 1: Stacked Reaction (Streamer top, content bottom, captions across seam)
        p1 = output_dir / f"{clip.id}.mp4"
        render_variant_file(
            clip=clip, source_video=source_video, out_mp4=p1, layout_name="stack",
            transcript=transcript, cache=cache, video_config=video_config,
            codec=codec, fonts_dir=fonts_dir, face_box=face_box, style_name=style_name, vision_info=vision_info,
            force=force,
        )
        variants_list.append({
            "id": "stack",
            "name": "Reaction Stacked",
            "file": f"{clip.id}.mp4",
            "description": "Streamer reaction on top, reframed gameplay on bottom with seam captions"
        })

        if render_variants:
            # Variant 2: Content Only - Full ratio with cinematic background blur
            p2 = output_dir / f"{clip.id}_blur.mp4"
            render_variant_file(
                clip=clip, source_video=source_video, out_mp4=p2, layout_name="blur-fill",
                transcript=transcript, cache=cache, video_config=video_config,
                codec=codec, fonts_dir=fonts_dir, face_box=face_box, style_name=style_name, vision_info=vision_info,
                force=force,
            )
            variants_list.append({
                "id": "blur-fill",
                "name": "Reference Letterbox",
                "file": f"{clip.id}_blur.mp4",
                "description": "Full 16:9 stream with streamer corner webcam + bold 3-line headline at top + subtitles"
            })

            # Variant 3: Content Only - Deframed 9:16 full-bleed crop focused on action
            p3 = output_dir / f"{clip.id}_reframed.mp4"
            render_variant_file(
                clip=clip, source_video=source_video, out_mp4=p3, layout_name="reframed",
                transcript=transcript, cache=cache, video_config=video_config,
                codec=codec, fonts_dir=fonts_dir, face_box=face_box, style_name=style_name, vision_info=vision_info,
                force=force,
            )
            variants_list.append({
                "id": "reframed",
                "name": "Content (Deframed 9:16)",
                "file": f"{clip.id}_reframed.mp4",
                "description": "Content only in full-bleed 9:16 vertical crop focused on the action"
            })
    else:
        # Streamer has no facecam (pure gameplay / content stream)
        # Variant 1: Deframed 9:16 vertical crop
        p1 = output_dir / f"{clip.id}.mp4"
        render_variant_file(
            clip=clip, source_video=source_video, out_mp4=p1, layout_name="reframed",
            transcript=transcript, cache=cache, video_config=video_config,
            codec=codec, fonts_dir=fonts_dir, face_box=None, style_name=style_name, vision_info=vision_info,
            force=force,
        )
        variants_list.append({
            "id": "reframed",
            "name": "Content (Deframed 9:16)",
            "file": f"{clip.id}.mp4",
            "description": "Full-bleed 9:16 vertical crop focused on the action"
        })

        if render_variants:
            # Variant 2: Blur-Fill (full ratio centered with blurred background)
            p2 = output_dir / f"{clip.id}_blur.mp4"
            render_variant_file(
                clip=clip, source_video=source_video, out_mp4=p2, layout_name="blur-fill",
                transcript=transcript, cache=cache, video_config=video_config,
                codec=codec, fonts_dir=fonts_dir, face_box=None, style_name=style_name, vision_info=vision_info,
                force=force,
            )
            variants_list.append({
                "id": "blur-fill",
                "name": "Content (Blur-Fill)",
                "file": f"{clip.id}_blur.mp4",
                "description": "Full ratio content centered with cinematic background blur"
            })

    # Write sidecar JSON with full variant catalog
    clip_dict = clip.model_dump()
    clip_dict["has_facecam"] = has_facecam
    clip_dict["variants"] = variants_list
    out_json = output_dir / f"{clip.id}.json"
    with open(out_json, "w", encoding="utf-8") as f:
        import json
        json.dump(clip_dict, f, indent=2, ensure_ascii=False)

    return output_dir / f"{clip.id}.mp4"


def run_render(
    cache: CacheManager,
    transcript: FullTranscript,
    clips: List[ClipItem],
    video_config: VideoConfig,
    out_base: Path = Path("out"),
    style_name: str = "bold-yellow",
    profile_name: Optional[str] = None,
    progress_cb: Optional[Any] = None,
    force: bool = False,
) -> List[Path]:
    """Render all selected clips with progress bar."""
    source_mp4 = cache.path("source.mp4")
    out_dir = out_base / cache.video_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Check for facecam box and verify if streamer is active with a live cam
    profile_path: Optional[Path] = None
    if profile_name:
        candidate_p = Path(profile_name) if profile_name.endswith(".yaml") else Path(f"profiles/{profile_name}.yaml")
        if candidate_p.exists():
            profile_path = candidate_p

    has_facecam, face_box = check_facecam_active(source_mp4, cache=cache, profile_path=profile_path)
    if has_facecam and face_box:
        print(f"✔ Active streamer facecam detected at x={face_box[0]}, y={face_box[1]}, w={face_box[2]}, h={face_box[3]}")
        print("  Generating 3 variants per clip: [1] Stacked Reaction, [2] Content Blur-Fill, [3] Deframed 9:16")
    else:
        print("Notice: No active streamer facecam detected (streamer has no webcam / pure content)")
        print("  Generating 2 content-only variants per clip: [1] Deframed 9:16, [2] Content Blur-Fill")

    rendered_files: List[Path] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task(f"Rendering {len(clips)} vertical clips...", total=len(clips))

        for idx, clip in enumerate(clips, 1):
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
                    force=force,
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
                    force=force,
                )
                rendered_files.append(mp4_path)
            progress.advance(task)
            if progress_cb:
                try:
                    progress_cb(idx, len(clips), clip)
                except Exception:
                    pass

    return rendered_files
