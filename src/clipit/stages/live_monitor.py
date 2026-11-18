"""Continuous Live Stream Clipper & Sentinel:
Monitors multi-hour livestreams, fetches 30-minute chunk intervals with configurable
lookback overlap (5-10m) to protect boundary clips, deduplicates overlapping moments,
catalogs clips as Part 1, Part 2, ..., and runs until the live broadcast concludes.
"""

import os
import sys
import time
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from clipit.cache import CacheManager, get_video_id
from clipit.config import load_config, VideoConfig
from clipit.models import ClipItem, FullTranscript
from clipit.stages.ingest import run_ingest
from clipit.stages.transcribe import run_transcribe
from clipit.stages.signals import run_signals
from clipit.stages.candidates import run_candidates
from clipit.stages.select import run_select
from clipit.stages.refine import run_refine
from clipit.stages.render import run_render
from clipit.stages.review import generate_review_html

console = Console()


def format_seconds(seconds: float) -> str:
    """Format seconds into HH:MM:SS string."""
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def parse_time_str(time_str: str) -> float:
    """Parse HH:MM:SS or MM:SS into seconds."""
    parts = str(time_str).strip().split(":")
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    else:
        return float(parts[0])


def probe_live_stream_status(url_or_id: str) -> Dict[str, Any]:
    """Probe live stream status, broadcast timestamp, and current elapsed duration."""
    url = url_or_id
    if not (url.startswith("http://") or url.startswith("https://")):
        url = f"https://www.youtube.com/watch?v={url_or_id}"

    cmd = ["yt-dlp", "--dump-json", "--no-download", url]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "is_live": False,
            "title": "Live Stream",
            "elapsed_sec": 0.0,
            "release_timestamp": None,
        }

    is_live = bool(data.get("is_live", False) or data.get("live_status") == "is_live")
    was_live = bool(data.get("live_status") in ("was_live", "post_live"))
    release_ts = data.get("release_timestamp")
    now_ts = time.time()

    elapsed_sec = 0.0
    if release_ts:
        elapsed_sec = max(0.0, now_ts - float(release_ts))
    elif data.get("duration"):
        elapsed_sec = float(data.get("duration"))

    return {
        "success": True,
        "is_live": is_live,
        "was_live": was_live,
        "live_status": data.get("live_status", "unknown"),
        "title": data.get("title", "Live Stream"),
        "uploader": data.get("uploader", ""),
        "release_timestamp": release_ts,
        "duration": data.get("duration"),
        "elapsed_sec": elapsed_sec,
        "view_count": data.get("view_count", 0),
    }


class LiveStreamMonitor:
    """Continuous live-stream chunking and clipping coordinator."""

    def __init__(
        self,
        url: str,
        chunk_mins: int = 30,
        overlap_mins: int = 5,
        target_clips: int = 6,
        layout: str = "stack",
        style: str = "bold-yellow",
        profile: Optional[str] = None,
        out_base: Path = Path("out"),
        cache_base: Path = Path("cache"),
    ):
        self.url = url
        self.video_id = get_video_id(url)
        self.chunk_mins = max(5, int(chunk_mins))
        self.overlap_mins = max(1, min(self.chunk_mins - 1, int(overlap_mins)))
        self.target_clips = 0 if str(target_clips).lower() in ("auto", "0") else max(1, int(target_clips))
        self.layout = layout
        self.style = style
        self.profile = profile or ("ishowspeed" if "speed" in url.lower() or "speed" in self.video_id.lower() else None)

        self.out_base = out_base
        self.cache_base = cache_base
        self.out_dir = out_base / self.video_id
        self.cache_dir = cache_base / self.video_id

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.session_file = self.cache_dir / "live_session.json"
        self.state = self.load_or_init_session()

    def emit_event(self, ev_type: str, data: Dict[str, Any]):
        """Emit real-time SSE event to local ClipIt Studio server if active."""
        try:
            import urllib.request
            payload = json.dumps({
                "session_id": f"live_{self.video_id}",
                "video_id": self.video_id,
                "url": self.url,
                "event": ev_type,
                "data": data,
            }).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:8765/api/internal/emit-event",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=0.3)
        except Exception:
            pass

    def load_or_init_session(self) -> Dict[str, Any]:
        """Load persistent live monitoring session or initialize fresh state."""
        if self.session_file.exists():
            try:
                with open(self.session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "parts" in data and "last_edge_sec" in data:
                        return data
            except Exception:
                pass

        # Check if existing clips were generated prior to live monitor initialization
        initial_parts = []
        initial_clips = []
        initial_edge = 0.0

        existing_refined = self.cache_dir / "refined_clips.json"
        existing_info = self.cache_dir / "info.json"
        if existing_refined.exists() and existing_info.exists():
            try:
                with open(existing_refined, "r", encoding="utf-8") as f:
                    r_clips = json.load(f)
                with open(existing_info, "r", encoding="utf-8") as f:
                    inf = json.load(f)

                prior_dur = float(inf.get("duration", 0.0))
                if prior_dur > 0 and r_clips:
                    # Adopt existing 0..prior_dur as Part 1!
                    initial_edge = prior_dur
                    p1_clips = []
                    for idx, c in enumerate(r_clips, 1):
                        cid = c.get("id", f"c{idx:02d}")
                        c_dict = dict(c)
                        c_dict["part"] = "Part 1"
                        c_dict["part_index"] = 1
                        c_dict["stream_window"] = f"00:00:00 - {format_seconds(prior_dur)}"
                        c_dict["abs_start"] = float(c.get("start", 0.0))
                        c_dict["abs_end"] = float(c.get("end", 0.0))
                        if not cid.startswith("pt"):
                            c_dict["id"] = f"pt01_{cid}"
                        p1_clips.append(c_dict)

                    initial_parts.append({
                        "part_index": 1,
                        "name": "Part 1",
                        "start_sec": 0.0,
                        "end_sec": prior_dur,
                        "start_str": "00:00:00",
                        "end_str": format_seconds(prior_dur),
                        "duration_sec": prior_dur,
                        "clips_count": len(p1_clips),
                        "completed_at": time.time(),
                        "clips": p1_clips,
                    })
                    initial_clips.extend(p1_clips)
            except Exception as e:
                console.print(f"[dim]Could not adopt existing clips: {e}[/dim]")

        new_state = {
            "video_id": self.video_id,
            "url": self.url,
            "title": "Live Stream Monitor",
            "chunk_mins": self.chunk_mins,
            "overlap_mins": self.overlap_mins,
            "created_at": time.time(),
            "last_edge_sec": initial_edge,
            "status": "watching",
            "parts": initial_parts,
            "all_clips": initial_clips,
        }
        self.save_session(new_state)
        return new_state

    def save_session(self, state: Optional[Dict[str, Any]] = None):
        """Persist session state to both cache and out directories."""
        if state is not None:
            self.state = state
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.session_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

        out_session = self.out_dir / "live_session.json"
        with open(out_session, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def deduplicate_clips(
        self,
        new_clips: List[ClipItem],
        chunk_start_sec: float,
    ) -> List[ClipItem]:
        """Filter out candidate clips in the overlap zone that duplicate existing parts."""
        existing_clips = self.state.get("all_clips", [])
        if not existing_clips:
            return new_clips

        deduped: List[ClipItem] = []
        for clip in new_clips:
            abs_start = chunk_start_sec + clip.start
            abs_end = chunk_start_sec + clip.end
            clip_dur = abs_end - abs_start

            is_dup = False
            for ex in existing_clips:
                ex_start = float(ex.get("abs_start", ex.get("start", 0.0)))
                ex_end = float(ex.get("abs_end", ex.get("end", 0.0)))

                ov_start = max(abs_start, ex_start)
                ov_end = min(abs_end, ex_end)
                overlap = max(0.0, ov_end - ov_start)
                ex_dur = ex_end - ex_start

                min_dur = min(clip_dur, ex_dur) if min(clip_dur, ex_dur) > 0 else 1.0
                if (overlap / min_dur > 0.40) or (overlap >= 15.0):
                    is_dup = True
                    break

            if not is_dup:
                deduped.append(clip)
            else:
                console.print(f"  [dim]Skipping duplicate boundary clip ({format_seconds(abs_start)} - {format_seconds(abs_end)}): '{clip.title[:30]}...'[/dim]")

        return deduped

    def process_part(
        self,
        part_index: int,
        start_sec: float,
        end_sec: float,
    ) -> Dict[str, Any]:
        """Ingest, transcribe, select, and render one discrete live stream part."""
        start_str = format_seconds(start_sec)
        end_str = format_seconds(end_sec)
        part_name = f"Part {part_index}"
        duration = end_sec - start_sec

        console.print(Panel(
            f"[bold cyan]🎬 Processing {part_name}[/bold cyan]\n"
            f"Stream Time Window: [bold yellow]{start_str} ➔ {end_str}[/bold yellow] ({duration / 60:.1f} minutes)\n"
            f"Lookback Overlap: [green]{self.overlap_mins} mins[/green] (capturing split boundary moments)\n"
            f"Target Clips: [white]{self.target_clips}[/white] | Style: [magenta]{self.style}[/magenta]",
            border_style="cyan"
        ))

        self.emit_event("pipeline_start", {
            "video_id": self.video_id,
            "url": self.url,
            "title": f"Live Stream · {part_name} ({start_str} - {end_str})",
            "stages": [
                {"id": "ingest", "name": f"Live Ingest ({part_name})", "badge": "HLS Live Stream", "role": "Stream Acquisition & Demuxer", "desc": f"Capturing stream window {start_str} to {end_str}"},
                {"id": "transcribe", "name": f"Speech Alignment ({part_name})", "badge": "mlx-whisper", "role": "Metal-Accelerated Alignment", "desc": "Transcribing speech with exact word timestamps"},
                {"id": "signals", "name": f"Virality Intelligence ({part_name})", "badge": "Acoustic & Sentiment", "role": "Excitement & Volume Scoring", "desc": "Detecting peak emotions and speech spikes"},
                {"id": "candidates", "name": "Boundary Deduplication", "badge": "5m Lookback", "role": "Overlap Moment Stitcher", "desc": "Eliminating duplicates while capturing split moments"},
                {"id": "render", "name": "Hardware Motion Render", "badge": "Apple VideoToolbox", "role": "Multi-Variant Render", "desc": "Rendering 1080x1920 with synchronized audio & captions"},
                {"id": "export", "name": "Publishing Staging", "badge": "Live Studio Deliverables", "role": "Part Organizer & Review", "desc": "Staging deliverables and updating live review"},
            ]
        })
        self.emit_event("stage_start", {"stage_id": "ingest", "name": f"Live Ingest ({part_name})"})
        self.emit_event("agent_log", {"stage_id": "ingest", "log": f"🔴 [LIVE SENTINEL] Capturing {part_name}: {start_str} -> {end_str} (lookback overlap: {self.overlap_mins}m)...", "metrics": {"task": f"Ingesting {part_name}", "model": "yt-dlp + ffmpeg", "device": "HLS Live DVR"}})

        # Use isolated cache directory for this part
        part_vid = f"{self.video_id}_pt{part_index:02d}"
        part_cache = CacheManager(part_vid, base_dir=self.cache_base)
        config = load_config()

        # 1. Ingest Chunk
        with console.status(f"[bold green]Downloading stream chunk ({start_str} to {end_str})..."):
            info = run_ingest(self.url, part_cache, start=start_str, end=end_str)
        console.print(f"✔ Chunk ingested: {info.get('duration', duration):.1f}s audio/video")

        self.emit_event("stage_complete", {"stage_id": "ingest", "progress_pct": 20})
        self.emit_event("stage_start", {"stage_id": "transcribe", "name": f"Speech Alignment ({part_name})"})
        self.emit_event("agent_log", {"stage_id": "transcribe", "log": f"[TRANSCRIBE] Running mlx-whisper speech recognition on {part_name}...", "metrics": {"task": f"Transcribing {part_name}", "model": "mlx-whisper", "device": "Apple Neural Engine / Metal"}})

        # 2. Metal-Accelerated Whisper Transcription
        with console.status(f"[bold green]Transcribing {part_name}..."):
            transcript = run_transcribe(part_cache, model_name=config.whisper.model)
        console.print(f"✔ Transcription: {len(transcript.segments)} segments")

        self.emit_event("stage_complete", {"stage_id": "transcribe", "progress_pct": 40})
        self.emit_event("stage_start", {"stage_id": "signals", "name": f"Virality Intelligence ({part_name})"})
        self.emit_event("agent_log", {"stage_id": "signals", "log": f"[SIGNALS] Analyzing acoustic energy peaks, speech density & chat velocity for {part_name}..."})

        # 3. Multimodal Signals
        with console.status(f"[bold green]Computing virality signals for {part_name}..."):
            signals = run_signals(part_cache, transcript, config.signals)

        # 4. Candidate Generation
        with console.status(f"[bold green]Generating peak candidate windows..."):
            candidates = run_candidates(
                part_cache,
                transcript,
                signals,
                target_clips=self.target_clips + 2,
                min_length=config.defaults.min_length,
                max_length=config.defaults.max_length,
                preferred_length=config.defaults.preferred_max,
            )

        # 5. Virality Selection
        with console.status(f"[bold green]Selecting viral moments (AI Director)..."):
            raw_clips = run_select(
                part_cache,
                transcript,
                candidates,
                target_clips=0 if self.target_clips <= 0 else self.target_clips + 2,
                min_score=65,
                selector_mode="api",
                force=True,
            )

        # 6. Boundary Refinement
        with console.status(f"[bold green]Refining boundaries & speech cadence..."):
            refined_clips = run_refine(
                part_cache,
                transcript,
                raw_clips,
                min_length=config.defaults.min_length,
                max_length=config.defaults.max_length,
            )

        # 7. Boundary Deduplication against prior parts
        deduped_clips = self.deduplicate_clips(refined_clips, chunk_start_sec=start_sec)
        final_clips = deduped_clips[:29] if self.target_clips <= 0 else deduped_clips[:self.target_clips]

        self.emit_event("stage_complete", {"stage_id": "signals", "progress_pct": 60})
        self.emit_event("stage_start", {"stage_id": "candidates", "name": "Boundary Deduplication"})
        self.emit_event("agent_log", {"stage_id": "candidates", "log": f"[DEDUP] Evaluated candidates across {self.overlap_mins}m boundary. Approved {len(final_clips)} viral moments for {part_name}."})

        # Rename clip IDs with Part prefix (keep title clean without [Part X] prefix)
        for idx, c in enumerate(final_clips, 1):
            c.id = f"pt{part_index:02d}_c{idx:02d}"
            c.layout = self.layout
            c.title = re.sub(r"^\[?Part\s*\d+\]?:?\s*", "", c.title, flags=re.IGNORECASE).strip()

        console.print(f"✔ Finalized {len(final_clips)} viral clips for {part_name} (after deduplication)")

        # 8. Render Vertical Clips
        console.print(f"[bold green]Rendering vertical 9:16 clips for {part_name}...[/bold green]")
        part_out_dir = self.out_dir / f"part_{part_index:02d}"
        part_out_dir.mkdir(parents=True, exist_ok=True)

        self.emit_event("stage_complete", {"stage_id": "candidates", "progress_pct": 75})
        self.emit_event("stage_start", {"stage_id": "render", "name": f"Hardware Motion Render ({part_name})"})

        rendered_files = run_render(
            cache=part_cache,
            transcript=transcript,
            clips=final_clips,
            video_config=config.video,
            out_base=self.cache_base,
            style_name=self.style,
            profile_name=self.profile,
            force=True,
        )

        # 9. Stage clips into part folder AND main out directory
        part_clips_dicts = []
        src_out = self.cache_base / part_vid
        for c in final_clips:
            c_dict = c.model_dump()
            c_dict["part"] = part_name
            c_dict["part_index"] = part_index
            c_dict["stream_window"] = f"{start_str} - {end_str}"
            c_dict["abs_start"] = start_sec + c.start
            c_dict["abs_end"] = start_sec + c.end

            for f in src_out.glob(f"{c.id}*"):
                dst_part = part_out_dir / f.name
                shutil.copy2(f, dst_part)
                dst_main = self.out_dir / f.name
                shutil.copy2(f, dst_main)

            sidecar_p = self.out_dir / f"{c.id}.json"
            if sidecar_p.exists():
                try:
                    with open(sidecar_p, "r", encoding="utf-8") as sf:
                        sc = json.load(sf)
                    sc.update(c_dict)
                    with open(sidecar_p, "w", encoding="utf-8") as sf:
                        json.dump(sc, sf, indent=2, ensure_ascii=False)
                    shutil.copy2(sidecar_p, part_out_dir / f"{c.id}.json")
                except Exception:
                    pass

            part_clips_dicts.append(c_dict)

        # 10. Update state & master review
        part_meta = {
            "part_index": part_index,
            "name": part_name,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "start_str": start_str,
            "end_str": end_str,
            "duration_sec": duration,
            "clips_count": len(part_clips_dicts),
            "completed_at": time.time(),
            "clips": part_clips_dicts,
        }

        self.state["parts"].append(part_meta)
        self.state["all_clips"].extend(part_clips_dicts)
        self.state["last_edge_sec"] = end_sec
        self.save_session()

        # Update consolidated refined_clips.json
        master_refined = self.cache_dir / "refined_clips.json"
        with open(master_refined, "w", encoding="utf-8") as f:
            json.dump(self.state["all_clips"], f, indent=2, ensure_ascii=False)

        # Regenerate consolidated review.html
        all_clip_items = [ClipItem(**c) for c in self.state["all_clips"]]
        generate_review_html(all_clip_items, CacheManager(self.video_id, base_dir=self.cache_base), self.out_dir)

        self.emit_event("stage_complete", {"stage_id": "render", "progress_pct": 95})
        self.emit_event("stage_start", {"stage_id": "export", "name": "Publishing Staging"})
        self.emit_event("agent_log", {"stage_id": "export", "log": f"[EXPORT] {part_name} staged: {len(final_clips)} clips ready in out/{self.video_id}/."})
        self.emit_event("stage_complete", {"stage_id": "export", "progress_pct": 100})
        self.emit_event("pipeline_complete", {
            "video_id": self.video_id,
            "total_clips": len(final_clips),
            "message": f"✔ {part_name} complete! {len(final_clips)} viral shorts rendered. Standby for next 30m stream edge."
        })

        console.print(Panel(
            f"[bold green]✨ {part_name} Complete![/bold green]\n\n"
            f"Clips Rendered: [bold]{len(final_clips)}[/bold]\n"
            f"Part Folder: [link=file://{part_out_dir.resolve()}]{part_out_dir}[/link]\n"
            f"Consolidated Review: [link=file://{(self.out_dir / 'review.html').resolve()}]{self.out_dir / 'review.html'}[/link]",
            title=f"{part_name} Summary",
            border_style="green",
        ))

        return part_meta

    def run_next_pending_part(self) -> Optional[Dict[str, Any]]:
        """Determine and process the next pending part based on stream edge."""
        status = probe_live_stream_status(self.url)
        if not status.get("success"):
            console.print(f"[yellow]Stream probe failed: {status.get('error')}[/yellow]")
            return None

        is_live = status.get("is_live", False)
        elapsed_sec = float(status.get("elapsed_sec", 0.0))
        last_edge = float(self.state.get("last_edge_sec", 0.0))
        chunk_sec = self.chunk_mins * 60.0
        overlap_sec = self.overlap_mins * 60.0

        part_idx = len(self.state.get("parts", [])) + 1
        target_edge = last_edge + chunk_sec

        if elapsed_sec < target_edge:
            if is_live:
                needed = target_edge - elapsed_sec
                console.print(f"[cyan]Stream is live ({format_seconds(elapsed_sec)}). Next chunk needs {format_seconds(needed)} more content.[/cyan]")
                return None
            else:
                tail_dur = elapsed_sec - last_edge
                if tail_dur < 120.0:
                    console.print(f"[green]Broadcast has ended and all content has been captured. Zero pending tail.[/green]")
                    self.state["status"] = "completed"
                    self.save_session()
                    return None
                target_edge = elapsed_sec

        start_sec = max(0.0, last_edge - overlap_sec)
        end_sec = target_edge

        return self.process_part(part_index=part_idx, start_sec=start_sec, end_sec=end_sec)

    def run_loop(
        self,
        max_parts: Optional[int] = None,
        poll_interval_sec: int = 60,
    ):
        """Continuously run every 30 minutes until the live stream ends."""
        console.print(Panel.fit(
            f"[bold red]🔴 Live Stream Sentinel Activated[/bold red]\n\n"
            f"URL: [white]{self.url}[/white]\n"
            f"Chunk Interval: [yellow]{self.chunk_mins} mins[/yellow] | Overlap Window: [cyan]{self.overlap_mins} mins[/cyan]\n"
            f"Target Clips/Part: [white]{self.target_clips}[/white] | Subtitles: [magenta]{self.style}[/magenta]\n"
            f"Monitoring loop will run every {self.chunk_mins} minutes until the stream terminates.",
            border_style="red"
        ))

        consecutive_errors = 0
        while True:
            if max_parts and len(self.state.get("parts", [])) >= max_parts:
                console.print(f"[green]Reached maximum requested parts ({max_parts}). Stopping monitor loop.[/green]")
                break

            status = probe_live_stream_status(self.url)
            if not status.get("success"):
                consecutive_errors += 1
                console.print(f"[yellow]Warning: Could not connect to stream probe ({consecutive_errors}/5). Retrying in {poll_interval_sec}s...[/yellow]")
                if consecutive_errors >= 5:
                    console.print("[red]Stream unreachable after 5 attempts. Exiting sentinel loop.[/red]")
                    break
                time.sleep(poll_interval_sec)
                continue

            consecutive_errors = 0
            is_live = status.get("is_live", False)
            elapsed_sec = float(status.get("elapsed_sec", 0.0))
            last_edge = float(self.state.get("last_edge_sec", 0.0))
            chunk_sec = self.chunk_mins * 60.0
            overlap_sec = self.overlap_mins * 60.0

            target_edge = last_edge + chunk_sec
            part_idx = len(self.state.get("parts", [])) + 1

            if elapsed_sec >= target_edge:
                start_sec = max(0.0, last_edge - overlap_sec)
                end_sec = target_edge
                self.process_part(part_index=part_idx, start_sec=start_sec, end_sec=end_sec)
                continue

            if not is_live:
                tail_dur = elapsed_sec - last_edge
                if tail_dur >= 120.0:
                    console.print(f"[yellow]Stream concluded! Processing final buffer ({format_seconds(last_edge)} to {format_seconds(elapsed_sec)})...[/yellow]")
                    start_sec = max(0.0, last_edge - overlap_sec)
                    end_sec = elapsed_sec
                    self.process_part(part_index=part_idx, start_sec=start_sec, end_sec=end_sec)

                console.print(Panel(
                    f"[bold green]🏁 Live Broadcast Finished[/bold green]\n\n"
                    f"All parts (1 to {len(self.state.get('parts', []))}) successfully processed and rendered.\n"
                    f"Total Viral Clips Generated: [bold]{len(self.state.get('all_clips', []))}[/bold]\n"
                    f"Review Dashboard: [link=file://{(self.out_dir / 'review.html').resolve()}]{self.out_dir / 'review.html'}[/link]",
                    border_style="green",
                    title="Live Monitor Complete"
                ))
                self.state["status"] = "completed"
                self.save_session()
                break

            needed_sec = target_edge - elapsed_sec
            self.emit_event("agent_log", {
                "stage_id": "ingest",
                "log": f"⏳ [LIVE SENTINEL STANDBY] Live stream active ({format_seconds(elapsed_sec)}). Next part ({part_idx}) needs {format_seconds(needed_sec)} more stream footage. Checking again in {poll_interval_sec}s...",
                "metrics": {"task": f"Waiting for Part {part_idx} footage", "vod_ts": format_seconds(elapsed_sec)}
            })
            console.print(
                f"[dim]Live stream active ({format_seconds(elapsed_sec)}). "
                f"Next part ({part_idx}) needs {format_seconds(needed_sec)} more stream footage. "
                f"Sleeping {poll_interval_sec}s...[/dim]"
            )
            time.sleep(poll_interval_sec)
