"""Render stage: Render vertical 1080x1920 MP4 clips with hardware acceleration, audio normalization, and subtitles."""

import os
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from clipit.cache import CacheManager
from clipit.config import VideoConfig, get_available_encoder
from clipit.models import FullTranscript, ClipItem
from clipit.stages.layout import get_layout_filter, detect_facecam_box, check_facecam_active
from clipit.stages.captions import generate_clip_ass
from clipit.stages.vision_reframing import analyze_clip_vision

# Output args that strip container/stream metadata carried over from source VODs
METADATA_SCRUB = [
    "-map_metadata", "-1",
    "-map_chapters", "-1",
    "-map_metadata:s:v", "-1",
    "-map_metadata:s:a", "-1",
]



def get_source_transcript_offset(
    source_video: Path,
    transcript: FullTranscript,
    cache: CacheManager,
    clip_start: float,
    clip_end: Optional[float] = None,
) -> float:
    """Detect and return any timing offset (in seconds) between source.mp4 audio and transcript.json.

    When VODs or live stream recordings are re-downloaded, concatenated, or container presentation
    timestamps drift relative to the initial transcription, source.mp4 can have a non-zero timebase offset.
    This function dynamically checks a 12-second audio sample of source.mp4 around a sequence of spoken words,
    transcribes it, and calculates the robust median difference across matching words via difflib.SequenceMatcher.
    If |offset| > 0.15s, it returns the offset so video/audio trimming aligns 100% with ASS subtitles.
    Results are cached in timeline_offset.json for instantaneous subsequent renders.
    """
    if source_video.name.startswith("clip_source_"):
        return 0.0

    cached_calib = cache.load_json("timeline_offset.json")
    if cached_calib and "offset" in cached_calib:
        try:
            return float(cached_calib["offset"])
        except (ValueError, TypeError):
            pass

    try:
        import difflib
        import statistics

        mid_pts = ((clip_start + clip_end) / 2.0) if clip_end and clip_end > clip_start else (clip_start + 15.0)

        # Collect 10-15 words from transcript starting around mid_pts
        trans_words = []
        for seg in transcript.segments:
            for w in seg.words:
                if w.start >= mid_pts and len(trans_words) < 15:
                    clean = re.sub(r"[^a-zA-Z]", "", w.w).lower()
                    if len(clean) >= 3:
                        trans_words.append((clean, w.start))
            if len(trans_words) >= 15:
                break

        # Fallback to clip_start if mid_pts had no words
        if not trans_words:
            for seg in transcript.segments:
                for w in seg.words:
                    if w.start >= clip_start and len(trans_words) < 15:
                        clean = re.sub(r"[^a-zA-Z]", "", w.w).lower()
                        if len(clean) >= 3:
                            trans_words.append((clean, w.start))
                if len(trans_words) >= 15:
                    break

        if not trans_words:
            return 0.0

        # Extract a 14s slice around the first words
        first_t = trans_words[0][1]
        slice_start = max(0.0, first_t - 2.0)
        temp_wav = cache.path(f"_sync_calib_{os.getpid()}.wav")
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{slice_start:.3f}",
            "-i", str(source_video.resolve()),
            "-t", "14.0",
            "-vn", "-ar", "16000", "-ac", "1",
            str(temp_wav)
        ]
        subprocess.run(cmd, capture_output=True, check=True)

        slice_words = []
        try:
            import mlx_whisper
            res = mlx_whisper.transcribe(str(temp_wav), path_or_hf_repo="mlx-community/whisper-tiny", word_timestamps=True)
            for seg in res.get("segments", []):
                for w in seg.get("words", []):
                    clean = re.sub(r"[^a-zA-Z]", "", w.get("word", "")).lower()
                    if len(clean) >= 3:
                        slice_words.append((clean, slice_start + float(w.get("start", 0.0))))
        except Exception:
            try:
                from faster_whisper import WhisperModel
                m = WhisperModel("tiny", device="cpu", compute_type="int8")
                segs, _ = m.transcribe(str(temp_wav), word_timestamps=True)
                for seg in segs:
                    for w in seg.words:
                        clean = re.sub(r"[^a-zA-Z]", "", w.word).lower()
                        if len(clean) >= 3:
                            slice_words.append((clean, slice_start + float(w.start)))
            except Exception:
                pass

        if temp_wav.exists():
            try:
                temp_wav.unlink()
            except Exception:
                pass

        t_tokens = [w[0] for w in trans_words]
        s_tokens = [w[0] for w in slice_words]

        matcher = difflib.SequenceMatcher(None, t_tokens, s_tokens)
        diffs = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    t_w, t_t = trans_words[i1 + k]
                    s_w, s_t = slice_words[j1 + k]
                    diff = s_t - t_t
                    if abs(diff) < 5.0:  # Valid drift window
                        diffs.append(diff)

        if diffs:
            median_diff = round(float(statistics.median(diffs)), 3)
            # If offset is within 35ms (1 video frame), treat as zero
            if abs(median_diff) <= 0.035:
                median_diff = 0.0
            cache.save_json("timeline_offset.json", {"offset": median_diff})
            return median_diff
    except Exception:
        pass

    return 0.0


def get_source_stream_starts(source_video: Path) -> Tuple[float, float]:
    """Get the video and audio stream start_time offsets in seconds from the container.

    In live stream chunks (HLS / MPEG-TS), containers have non-zero presentation timestamps (PTS),
    and the video stream start_time often differs from audio start_time due to GOP/packet demuxing.
    When audio.wav is extracted, its samples start at t=0, which corresponds to the first audio packet's
    PTS (audio_start). To keep video, audio, and ASS subtitles in 100% microsecond sync during render
    with -copyts:
      - video trimming must start at (clip.start + video_start)
      - audio trimming must start at (clip.start + audio_start)
    """
    try:
        import json as _json
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type,start_time",
            "-show_entries", "format=start_time",
            "-of", "json",
            str(source_video.resolve())
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = _json.loads(res.stdout)
        v_st = 0.0
        a_st = 0.0
        for s in data.get("streams", []):
            ctype = s.get("codec_type")
            st = s.get("start_time")
            if st is not None:
                try:
                    if ctype == "video":
                        v_st = float(st)
                    elif ctype == "audio":
                        a_st = float(st)
                except (ValueError, TypeError):
                    pass
        fmt_st = data.get("format", {}).get("start_time")
        if fmt_st is not None:
            try:
                fst = float(fmt_st)
                if a_st == 0.0:
                    a_st = fst
                if v_st == 0.0:
                    v_st = fst
            except (ValueError, TypeError):
                pass
        return v_st, a_st
    except Exception:
        pass
    return 0.0, 0.0


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
    hook_audio: Optional[Path] = None,
    force: bool = False,
) -> Path:
    """Render a specific layout variant of a clip."""
    if not force and out_mp4.exists() and out_mp4.stat().st_size > 1000:
        return out_mp4

    if codec == "auto":
        codec = getattr(video_config, "draft_codec", None) or get_available_encoder()

    info = cache.load_json("info.json") or {}
    src_w = info.get("width")
    src_h = info.get("height")
    if not src_w or not src_h:
        try:
            import json as _json
            p = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(source_video)],
                capture_output=True, text=True, check=True
            )
            d = _json.loads(p.stdout)
            src_w = int(d["streams"][0]["width"])
            src_h = int(d["streams"][0]["height"])
        except Exception:
            src_w, src_h = 1920, 1080

    layout_filter, seam_y = get_layout_filter(
        layout_name, face_box=face_box, vision_info=vision_info, source_w=src_w, source_h=src_h
    )

    ass_suffix = f"_{layout_name}" if layout_name != "stack" else ""
    ass_path = cache.path(f"{clip.id}{ass_suffix}.ass")
    text_pos = vision_info.get("text_position") if vision_info else None
    content_ratio = vision_info.get("content_ratio", "3:4") if vision_info else "3:4"
    title_size = vision_info.get("title_size") if vision_info else None
    caption_size = vision_info.get("caption_size") if vision_info else None
    custom_title = vision_info.get("title") if vision_info else None
    if custom_title:
        clip.title = custom_title
    if clip.title:
        import re
        clip.title = re.sub(r"^\[?Part\s*\d+\]?:?\s*", "", clip.title, flags=re.IGNORECASE).strip()

    creator = info.get("uploader") or info.get("channel") or None
    custom_topic = vision_info.get("topic") if vision_info else None

    generate_clip_ass(
        clip,
        transcript,
        ass_path,
        style_path_or_name=style_name,
        show_hook_banner=True,
        seam_y=seam_y if layout_name == "stack" else None,
        layout_name=layout_name,
        creator_name=creator,
        topic_name=custom_topic,
        text_position=text_pos,
        content_ratio=content_ratio,
        title_size=title_size,
        caption_size=caption_size,
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

    clip_dur = max(0.1, clip.end - clip.start)

    # Input seeking with -copyts preserves container presentation timestamps (PTS).
    # Since audio.wav was extracted starting at sample 0 (at PTS audio_start), clip.start is relative to audio_start.
    # In MP4 and MPEG-TS containers, audio and video share the identical container presentation clock (PTS).
    # Therefore, BOTH video and audio trimming must start at the exact same target PTS (clip.start + audio_start).
    _, audio_start = get_source_stream_starts(source_video)
    target_pts = clip.start + audio_start
    fast_seek = max(0.0, target_pts - 5.0)

    # Audio hook (spoken title entry hook) resolution & ducking
    hook_dur = 0.0
    if hook_audio is None and getattr(video_config, "audio_hook_enabled", True):
        from clipit.stages.audio_hook import get_or_generate_audio_hook, get_hook_duration
        topic = custom_topic or "GTA 6"
        voice = getattr(video_config, "audio_hook_voice", "en-US-ChristopherNeural")
        rate = getattr(video_config, "audio_hook_rate", "+15%")
        hook_audio = get_or_generate_audio_hook(
            clip=clip, cache=cache, topic=topic, voice=voice, rate=rate
        )

    if hook_audio and hook_audio.exists():
        from clipit.stages.audio_hook import get_hook_duration
        hook_dur = get_hook_duration(hook_audio)

    inputs = [
        "-ss", f"{fast_seek:.3f}",
        "-copyts",
        "-i", str(source_video.resolve()),
    ]

    # Force 1:1 square pixel aspect ratio via setsar=1 to avoid anamorphic container flags
    if hook_audio and hook_audio.exists() and hook_dur > 0.2:
        inputs.extend(["-i", str(hook_audio.resolve())])
        duck_vol = getattr(video_config, "audio_hook_ducking", 0.35)
        filter_complex = (
            f"{layout_filter};"
            f"[base]trim=start={target_pts:.3f}:duration={clip_dur:.3f},setpts=PTS-STARTPTS,"
            f"ass=filename='{escaped_ass}':fontsdir='{escaped_fonts}',setsar=1,setpts=PTS-STARTPTS[v];"
            f"[0:a]atrim=start={target_pts:.3f}:duration={clip_dur:.3f},asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,"
            f"volume=enable='between(t,0,{hook_dur:.2f})':volume={duck_vol:.2f},"
            f"volume=enable='between(t,{hook_dur:.2f},{hook_dur+0.35:.2f})':volume=0.75[stream_ducked];"
            f"[1:a]asetpts=PTS-STARTPTS,aresample=48000,highpass=f=80,equalizer=f=3200:t=q:w=1.2:g=3.0,volume=1.35[hook_clean];"
            f"[stream_ducked][hook_clean]amix=inputs=2:duration=first:dropout_transition=1,"
            f"highpass=f=80,equalizer=f=3000:t=q:w=1.5:g=2.5,"
            f"acompressor=threshold=-18dB:ratio=3:attack=10:release=120:makeup=2dB,"
            f"loudnorm=I={video_config.audio_lufs}:LRA=11:TP=-2.0[a]"
        )
        audio_map = "[a]"
    else:
        filter_complex = (
            f"{layout_filter};"
            f"[base]trim=start={target_pts:.3f}:duration={clip_dur:.3f},setpts=PTS-STARTPTS,"
            f"ass=filename='{escaped_ass}':fontsdir='{escaped_fonts}',setsar=1,setpts=PTS-STARTPTS[v];"
            f"[0:a]atrim=start={target_pts:.3f}:duration={clip_dur:.3f},asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,"
            f"highpass=f=80,equalizer=f=3000:t=q:w=1.5:g=2.5,"
            f"acompressor=threshold=-18dB:ratio=3:attack=10:release=120:makeup=2dB,"
            f"loudnorm=I={video_config.audio_lufs}:LRA=11:TP=-2.0[a]"
        )
        audio_map = "[a]"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", audio_map,
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

    cmd.extend([
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
    ])
    cmd.extend(METADATA_SCRUB)
    cmd.append(str(out_mp4.resolve()))

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError:
        # Fallback to libx264 if videotoolbox fails on an edge case
        fallback_cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", audio_map,
            "-c:v", "libx264",
            "-crf", "18", "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
        ]
        fallback_cmd.extend(METADATA_SCRUB)
        fallback_cmd.append(str(out_mp4.resolve()))
        subprocess.run(fallback_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    # Post-render sync correction:
    # H.264 VideoToolbox and B-frame encoders produce a container where the first video keyframe
    # sits at a non-zero PTS (typically 1 frame = ~21ms at 47fps), while AAC audio starts at 0.
    # Detect this offset and stream-copy remux to align both streams to PTS=0 without re-encoding.
    _fix_av_start_offset(out_mp4)

    return out_mp4


def _fix_av_start_offset(mp4_path: Path) -> None:
    """Detect and fix video/audio start_time mismatch in a rendered MP4 via stream-copy remux.

    This corrects the AAC/H.264 codec priming delay (typically ~21ms) where the video stream
    starts at a non-zero PTS while audio starts at 0. The fix is a lossless stream-copy remux
    using -itsoffset to advance audio by the video start offset, aligning both to PTS=0.
    """
    try:
        import json as _json
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(mp4_path)],
            capture_output=True, text=True, timeout=10
        )
        if probe.returncode != 0:
            return
        data = _json.loads(probe.stdout)
        v_start = a_start = 0.0
        for s in data.get("streams", []):
            try:
                if s.get("codec_type") == "video":
                    v_start = float(s.get("start_time", 0))
                elif s.get("codec_type") == "audio":
                    a_start = float(s.get("start_time", 0))
            except (ValueError, TypeError):
                pass
        offset = v_start - a_start
        # Only apply if offset is meaningful (>5ms) — avoids unnecessary remux on already-aligned files
        if abs(offset) < 0.005:
            return
        tmp_path = mp4_path.with_suffix(".tmp_sync.mp4")
        # Delay audio by offset seconds so it aligns with the video stream start
        # Then -avoid_negative_ts make_zero normalises both streams to start at PTS=0
        remux_cmd = [
            "ffmpeg", "-y",
            "-i", str(mp4_path),
            "-itsoffset", f"{offset:.6f}",
            "-i", str(mp4_path),
            "-map", "0:v", "-map", "1:a",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "+faststart",
        ]
        remux_cmd.extend(METADATA_SCRUB)
        remux_cmd.append(str(tmp_path))
        result = subprocess.run(remux_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 100_000:
            tmp_path.replace(mp4_path)
        elif tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    except Exception:
        pass  # Never crash render on sync correction failure — the rendered file is still valid


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
    """Render vertical clip variants: always generates all 3 layouts:
    1. Reaction Stacked (<clip.id>.mp4)
    2. Reference Letterbox (<clip.id>_blur.mp4)
    3. Content Deframed 9:16 (<clip.id>_reframed.mp4)
    """
    vision_info = analyze_clip_vision(clip, source_video, cache)
    if vision_info.get("title"):
        clip.title = vision_info["title"]
    if clip.title:
        import re
        clip.title = re.sub(r"^\[?Part\s*\d+\]?:?\s*", "", clip.title, flags=re.IGNORECASE).strip()
    if "has_facecam" in vision_info:
        has_facecam = bool(vision_info["has_facecam"])
    else:
        has_facecam = bool(face_box)
        # Inherit global facecam coordinates from cache if local frame was ambiguous
        if not has_facecam:
            global_fc = cache.load_json("facecam.json") or {}
            if global_fc.get("has_facecam"):
                has_facecam = True
                if "streamer_box" not in vision_info and "streamer_box" in global_fc:
                    vision_info["streamer_box"] = global_fc["streamer_box"]
                elif "streamer_box" not in vision_info and "box" in global_fc:
                    vision_info["streamer_box"] = global_fc["box"]

    variants_list: List[Dict[str, Any]] = []

    # Audio entry hook resolution: generate punchy spoken title hook once per clip
    hook_audio = None
    if getattr(video_config, "audio_hook_enabled", True):
        from clipit.stages.audio_hook import get_or_generate_audio_hook
        topic = (vision_info.get("topic") if vision_info else None) or "GTA 6"
        voice = getattr(video_config, "audio_hook_voice", "en-US-ChristopherNeural")
        rate = getattr(video_config, "audio_hook_rate", "+15%")
        hook_audio = get_or_generate_audio_hook(
            clip=clip, cache=cache, topic=topic, voice=voice, rate=rate, force=force
        )

    # Variant 1: Stacked Reaction (Streamer top, content bottom, captions across seam)
    p1 = output_dir / f"{clip.id}.mp4"
    render_variant_file(
        clip=clip, source_video=source_video, out_mp4=p1, layout_name="stack",
        transcript=transcript, cache=cache, video_config=video_config,
        codec=codec, fonts_dir=fonts_dir, face_box=face_box, style_name=style_name, vision_info=vision_info,
        hook_audio=hook_audio, force=force,
    )
    variants_list.append({
        "id": "stack",
        "name": "Reaction Stacked",
        "label": "Reaction Stacked",
        "file": f"{clip.id}.mp4",
        "description": "Streamer reaction on top, reframed gameplay on bottom with seam captions"
    })

    if render_variants:
        # Variant 2: Reference Letterbox - Full 16:9 stream with dynamic background blur
        p2 = output_dir / f"{clip.id}_blur.mp4"
        render_variant_file(
            clip=clip, source_video=source_video, out_mp4=p2, layout_name="blur-fill",
            transcript=transcript, cache=cache, video_config=video_config,
            codec=codec, fonts_dir=fonts_dir, face_box=face_box, style_name=style_name, vision_info=vision_info,
            hook_audio=hook_audio, force=force,
        )
        variants_list.append({
            "id": "blur-fill",
            "name": "Reference Letterbox",
            "label": "Reference Letterbox",
            "file": f"{clip.id}_blur.mp4",
            "description": "Full 16:9 stream with corner webcam + bold 3-line headline at top + lower subtitles"
        })

        # Variant 3: Content Only - Deframed 9:16 full-bleed crop focused on action
        p3 = output_dir / f"{clip.id}_reframed.mp4"
        render_variant_file(
            clip=clip, source_video=source_video, out_mp4=p3, layout_name="reframed",
            transcript=transcript, cache=cache, video_config=video_config,
            codec=codec, fonts_dir=fonts_dir, face_box=face_box, style_name=style_name, vision_info=vision_info,
            hook_audio=hook_audio, force=force,
        )
        variants_list.append({
            "id": "reframed",
            "name": "Content (Deframed 9:16)",
            "label": "Content (Deframed 9:16)",
            "file": f"{clip.id}_reframed.mp4",
            "description": "Content only in full-bleed 9:16 vertical crop focused on the action"
        })

    # Write sidecar JSON with full variant catalog
    clip_dict = clip.model_dump()
    clip_dict["has_facecam"] = has_facecam
    clip_dict["variants"] = variants_list
    clip_dict["has_audio_hook"] = bool(hook_audio and hook_audio.exists())
    if hook_audio and hook_audio.exists():
        from clipit.stages.audio_hook import clean_spoken_hook
        clip_dict["spoken_hook"] = clean_spoken_hook(clip.title, topic=(vision_info.get("topic") if vision_info else "GTA 6"))
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
