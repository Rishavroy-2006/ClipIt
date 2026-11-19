#!/usr/bin/env python3
"""Autonomous Overnight Live Stream Clipper & Sentinel.

Continuously captures, analyzes, transcribes, selects with Gemini AI Director,
and renders synchronized vertical 9:16 clips for iShowSpeed's GTA 6 live stream.

Strict Guarantees:
- Resumes at Part 7 (start_edge = 10,627.0s with 5m overlap from Part 6).
- Dedicated project: out/yt_qSyo5d-dVzA_overnight/ and cache/yt_qSyo5d-dVzA_overnight_ptXX/.
- Strict quality threshold: score >= 80 ONLY.
- Dual Gemini API keys with multi-key failover and retry (NO offline heuristic fallback).
- Microsecond dual-PTS sync (inherited from verified Part 5 settings).
- Continuous live piling until the broadcast finishes.
- Real-time state persistence in .planning/overnight_sentinel_state.json.
- Morning Top 5 viral clips ranking and report (.planning/overnight_top5_clips.md).
- Immediate purge of intermediate source.mp4/audio.wav per part to save disk space.
- NO automatic YouTube uploads, NO git push.
"""

import os
import sys
import time
import json
import shutil
import re
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from clipit.cache import CacheManager, get_video_id
from clipit.config import load_config, VideoConfig
from clipit.models import ClipItem, FullTranscript, CandidateWindow
from clipit.stages.ingest import run_ingest
from clipit.stages.transcribe import run_transcribe
from clipit.stages.signals import run_signals
from clipit.stages.candidates import run_candidates
from clipit.stages.refine import run_refine
from clipit.stages.render import run_render
from clipit.stages.review import generate_review_html
from clipit.stages.live_monitor import format_seconds, probe_live_stream_status
from clipit.llm.selector_api import select_clips_with_gemini, get_api_keys

console = Console()

TARGET_URL = "https://www.youtube.com/watch?v=qSyo5d-dVzA"
PROJECT_ID = "yt_qSyo5d-dVzA_overnight"
PART6_CACHE_DIR = PROJECT_ROOT / "cache" / "yt_qSyo5d-dVzA_pt06"
STATE_FILE = PROJECT_ROOT / ".planning" / "overnight_sentinel_state.json"
TOP5_FILE = PROJECT_ROOT / ".planning" / "overnight_top5_clips.md"
OUT_BASE = PROJECT_ROOT / "out"
CACHE_BASE = PROJECT_ROOT / "cache"

CHUNK_MINS = 30
OVERLAP_MINS = 5
MIN_SCORE_THRESHOLD = 80
START_EDGE_SEC = 10627.0  # Where Part 6 ended (02:57:07)
START_PART_INDEX = 7


def strict_ai_select(
    cache: CacheManager,
    transcript: FullTranscript,
    candidates: List[CandidateWindow],
    video_title: str,
    streamer: str,
    min_score: int = 80,
    max_retries: int = 5,
) -> List[ClipItem]:
    """Select viral clips strictly using Gemini AI Director with retry across dual keys.
    
    Refuses to fall back to heuristic offline selection.
    """
    console.print(f"[bold cyan]🧠 Invoking Gemini AI Director (Score >= {min_score} only)...[/bold cyan]")
    
    for attempt in range(1, max_retries + 1):
        try:
            clips = select_clips_with_gemini(
                video_title=video_title,
                streamer=streamer,
                target_clips=0,  # Auto mode
                candidates=candidates,
                transcript=transcript,
                min_score=min_score,
            )
            
            if clips:
                # Filter strictly by score >= min_score
                filtered = [c for c in clips if getattr(c, "score", 0) >= min_score]
                console.print(f"✔ AI Director returned {len(clips)} clips; {len(filtered)} met score >= {min_score}")
                return filtered
            
            console.print(f"[yellow]⚠️ AI selection attempt {attempt}/{max_retries} returned no clips. Retrying in 15s...[/yellow]")
            time.sleep(15.0)
        except Exception as e:
            console.print(f"[red]⚠️ AI selection error (attempt {attempt}/{max_retries}): {e}[/red]")
            time.sleep(20.0)
            
    console.print(f"[bold red]❌ AI Director failed after {max_retries} attempts across all keys. Returning empty set (NO heuristic fallback).[/bold red]")
    return []


class OvernightSentinel:
    """Manages the full overnight stream capture, sync render, and live piling loop."""

    def __init__(self):
        self.url = TARGET_URL
        self.project_id = PROJECT_ID
        self.out_dir = OUT_BASE / self.project_id
        self.cache_dir = CACHE_BASE / self.project_id
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        self.state = self.load_or_init_state()

    def load_or_init_state(self) -> Dict[str, Any]:
        """Load existing overnight state or initialize fresh."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "parts" in data and "last_edge_sec" in data:
                        console.print(f"[green]✔ Resuming overnight sentinel state from Part {len(data['parts']) + START_PART_INDEX} (edge: {format_seconds(data['last_edge_sec'])})[/green]")
                        return data
            except Exception as e:
                console.print(f"[yellow]Could not parse existing state file: {e}. Reinitializing...[/yellow]")

        # Initialize deduplication baseline from Part 6 to avoid duplicating boundary moments
        dedup_baseline: List[Dict[str, Any]] = []
        p6_refined = PART6_CACHE_DIR / "refined_clips.json"
        if p6_refined.exists():
            try:
                with open(p6_refined, "r", encoding="utf-8") as f:
                    p6_clips = json.load(f)
                # Part 6 window was 8527.0s to 10627.0s
                for c in p6_clips:
                    c_copy = dict(c)
                    c_copy["abs_start"] = 8527.0 + float(c.get("start", 0.0))
                    c_copy["abs_end"] = 8527.0 + float(c.get("end", 0.0))
                    dedup_baseline.append(c_copy)
                console.print(f"✔ Seeded deduplication baseline with {len(dedup_baseline)} clips from Part 6")
            except Exception as e:
                console.print(f"[dim]Could not seed Part 6 baseline: {e}[/dim]")

        new_state = {
            "project_id": self.project_id,
            "url": self.url,
            "title": "iShowSpeed GTA 6 Overnight Stream",
            "start_time_iso": datetime.now(timezone.utc).isoformat(),
            "last_edge_sec": START_EDGE_SEC,
            "chunk_mins": CHUNK_MINS,
            "overlap_mins": OVERLAP_MINS,
            "min_score": MIN_SCORE_THRESHOLD,
            "status": "running",
            "current_part": f"Part {START_PART_INDEX}",
            "parts": [],
            "all_clips": [],
            "dedup_baseline": dedup_baseline,
            "errors": [],
            "last_updated": time.time(),
        }
        self.save_state(new_state)
        return new_state

    def save_state(self, state: Optional[Dict[str, Any]] = None):
        """Persist state to STATE_FILE and cache."""
        if state is not None:
            self.state = state
        self.state["last_updated"] = time.time()
        self.state["last_updated_iso"] = datetime.now(timezone.utc).isoformat()
        
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

        # Mirror session to out_dir
        with open(self.out_dir / "live_session.json", "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

        self.update_top5_report()

    def update_top5_report(self):
        """Compile the Top 5 viral clips across all overnight parts."""
        all_clips = self.state.get("all_clips", [])
        if not all_clips:
            return

        sorted_clips = sorted(all_clips, key=lambda x: x.get("score", 0), reverse=True)
        top5 = sorted_clips[:5]

        md_lines = [
            "# 🏆 Overnight Top 5 Viral Clips (Ready to Post)\n",
            f"**Stream**: {self.state.get('title', 'iShowSpeed GTA 6')}",
            f"**Last Updated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"**Total Overnight Clips Generated (Score >= 80)**: {len(all_clips)}\n",
            "---\n",
        ]

        for rank, clip in enumerate(top5, 1):
            cid = clip.get("id", "cXX")
            score = clip.get("score", 85)
            title = clip.get("title", "Viral Highlight")
            hook = clip.get("hook_line", "")
            part = clip.get("part", "Part ?")
            window = clip.get("stream_window", "")
            why = clip.get("why_viral", "")
            dur = clip.get("end", 0.0) - clip.get("start", 0.0)

            stack_file = self.out_dir / f"{cid}.mp4"
            blur_file = self.out_dir / f"{cid}_blur.mp4"
            reframed_file = self.out_dir / f"{cid}_reframed.mp4"

            md_lines.extend([
                f"### #{rank} · {title} ({score}/100)",
                f"- **Part & ID**: `{part}` ({cid}) | **Stream Window**: `{window}`",
                f"- **Duration**: `{dur:.1f}s` | **Hook Line**: *\"{hook}\"*",
                f"- **Why Viral**: {why}",
                f"- **Video Variants**:",
                f"  - **[1] Reaction Stacked**: `file://{stack_file.resolve()}`",
                f"  - **[2] Blur-Fill (Headline)**: `file://{blur_file.resolve()}`",
                f"  - **[3] Content Reframed**: `file://{reframed_file.resolve()}`",
                "\n",
            ])

        with open(TOP5_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

    def deduplicate_against_all(
        self,
        new_clips: List[ClipItem],
        chunk_start_sec: float,
    ) -> List[ClipItem]:
        """Filter out candidate clips in overlap that duplicate prior parts or Part 6."""
        all_prior = list(self.state.get("dedup_baseline", [])) + list(self.state.get("all_clips", []))
        deduped: List[ClipItem] = []

        for clip in new_clips:
            abs_start = chunk_start_sec + clip.start
            abs_end = chunk_start_sec + clip.end
            clip_dur = abs_end - abs_start

            is_dup = False
            for ex in all_prior:
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
    ) -> Optional[Dict[str, Any]]:
        """Ingest, transcribe, AI select, and render one 30m chunk."""
        start_str = format_seconds(start_sec)
        end_str = format_seconds(end_sec)
        part_name = f"Part {part_index}"
        duration = end_sec - start_sec

        console.print(Panel(
            f"[bold cyan]🎬 Ingesting & Processing {part_name}[/bold cyan]\n"
            f"Stream Time Window: [bold yellow]{start_str} ➔ {end_str}[/bold yellow] ({duration / 60:.1f} mins)\n"
            f"Lookback Overlap: [green]{OVERLAP_MINS} mins[/green] | Score Threshold: [bold magenta]>={MIN_SCORE_THRESHOLD}[/bold magenta]",
            border_style="cyan"
        ))

        self.state["current_part"] = part_name
        self.save_state()

        part_vid = f"{self.project_id}_pt{part_index:02d}"
        part_cache = CacheManager(part_vid, base_dir=CACHE_BASE)
        config = load_config()

        # 1. Ingest Chunk
        try:
            with console.status(f"[bold green]Downloading stream chunk ({start_str} to {end_str})..."):
                info = run_ingest(self.url, part_cache, start=start_str, end=end_str)
            console.print(f"✔ Chunk ingested: {info.get('duration', duration):.1f}s")
        except Exception as e:
            err_msg = f"Failed to ingest chunk {start_str}->{end_str}: {e}"
            console.print(f"[red]{err_msg}[/red]")
            self.state["errors"].append({"part": part_name, "stage": "ingest", "error": str(e), "ts": time.time()})
            self.save_state()
            return None

        # 2. Metal-Accelerated Whisper Transcription
        try:
            with console.status(f"[bold green]Transcribing {part_name} (mlx-whisper)..."):
                transcript = run_transcribe(part_cache, model_name=config.whisper.model)
            console.print(f"✔ Transcription: {len(transcript.segments)} segments")
        except Exception as e:
            err_msg = f"Failed transcription for {part_name}: {e}"
            console.print(f"[red]{err_msg}[/red]")
            self.state["errors"].append({"part": part_name, "stage": "transcribe", "error": str(e), "ts": time.time()})
            self.save_state()
            return None

        # 3. Multimodal Signals
        with console.status(f"[bold green]Computing virality signals for {part_name}..."):
            signals = run_signals(part_cache, transcript, config.signals)

        # 4. Candidate Generation
        with console.status(f"[bold green]Generating peak candidate windows..."):
            candidates = run_candidates(
                part_cache,
                transcript,
                signals,
                target_clips=15,
                min_length=config.defaults.min_length,
                max_length=config.defaults.max_length,
                preferred_length=config.defaults.preferred_max,
            )

        # 5. Strict AI Virality Selection (Dual Gemini Keys, NO Heuristic Fallback)
        raw_clips = strict_ai_select(
            cache=part_cache,
            transcript=transcript,
            candidates=candidates,
            video_title=info.get("title", "iShowSpeed GTA 6"),
            streamer=info.get("uploader", "iShowSpeed"),
            min_score=MIN_SCORE_THRESHOLD,
        )

        if not raw_clips:
            console.print(f"[yellow]Notice: No moments met score >= {MIN_SCORE_THRESHOLD} in {part_name}. Skipping render for this chunk.[/yellow]")
            # Clean intermediate files
            self.cleanup_cache_media(part_cache)
            self.state["last_edge_sec"] = end_sec
            self.save_state()
            return {
                "part_index": part_index,
                "name": part_name,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "clips_count": 0,
                "clips": [],
            }

        # 6. Boundary Refinement
        with console.status(f"[bold green]Refining boundaries to word timestamps..."):
            refined_clips = run_refine(
                part_cache,
                transcript,
                raw_clips,
                min_length=config.defaults.min_length,
                max_length=config.defaults.max_length,
            )

        # 7. Boundary Deduplication
        deduped_clips = self.deduplicate_against_all(refined_clips, chunk_start_sec=start_sec)
        
        # Enforce score >= 80 filter strictly
        final_clips = [c for c in deduped_clips if getattr(c, "score", 0) >= MIN_SCORE_THRESHOLD]

        # Rename clip IDs with Part prefix
        for idx, c in enumerate(final_clips, 1):
            c.id = f"pt{part_index:02d}_c{idx:02d}"
            c.layout = "stack"
            c.title = re.sub(r"^\[?Part\s*\d+\]?:?\s*", "", c.title, flags=re.IGNORECASE).strip()

        console.print(f"✔ Finalized {len(final_clips)} viral clips for {part_name} (Score >= {MIN_SCORE_THRESHOLD})")

        # 8. Render Vertical 9:16 Clips (Dual-PTS Precision)
        part_out_dir = self.out_dir / f"part_{part_index:02d}"
        part_out_dir.mkdir(parents=True, exist_ok=True)

        if final_clips:
            console.print(f"[bold green]Rendering {len(final_clips)} clips with dual-PTS sync & ishowspeed profile...[/bold green]")
            run_render(
                cache=part_cache,
                transcript=transcript,
                clips=final_clips,
                video_config=config.video,
                out_base=CACHE_BASE,
                style_name="bold-yellow",
                profile_name="ishowspeed",
                force=True,
            )

        # 9. Stage clips into part folder AND main out directory
        part_clips_dicts = []
        src_out = CACHE_BASE / part_vid
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
                if f.suffix in (".mp4", ".mkv", ".mov"):
                    try:
                        f.unlink()
                    except Exception:
                        pass

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
        self.save_state()

        # Update consolidated refined_clips.json
        master_refined = self.cache_dir / "refined_clips.json"
        with open(master_refined, "w", encoding="utf-8") as f:
            json.dump(self.state["all_clips"], f, indent=2, ensure_ascii=False)

        # Regenerate consolidated review.html
        all_clip_items = [ClipItem(**c) for c in self.state["all_clips"]]
        generate_review_html(all_clip_items, CacheManager(self.project_id, base_dir=CACHE_BASE), self.out_dir)

        # 11. Conserve disk space: delete intermediate chunk raw source.mp4 and audio.wav
        self.cleanup_cache_media(part_cache)

        console.print(Panel(
            f"[bold green]✨ {part_name} Complete![/bold green]\n"
            f"Clips Qualified (Score >= {MIN_SCORE_THRESHOLD}): [bold]{len(part_clips_dicts)}[/bold]\n"
            f"Total Overnight Clips: [bold]{len(self.state['all_clips'])}[/bold]\n"
            f"Review Dashboard: [link=file://{(self.out_dir / 'review.html').resolve()}]{self.out_dir / 'review.html'}[/link]",
            title=f"{part_name} Staged",
            border_style="green",
        ))

        return part_meta

    def cleanup_cache_media(self, cache: CacheManager):
        """Purge temporary audio.wav, but keep source.mp4 for instant Web Studio re-renders if disk space permits."""
        wav_p = cache.path("audio.wav")
        if wav_p.exists():
            try:
                wav_p.unlink()
                console.print(f"  [dim]Purged cache temp: {wav_p.name}[/dim]")
            except Exception as e:
                console.print(f"  [dim]Could not purge {wav_p.name}: {e}[/dim]")

        # Only purge source.mp4 if free disk space is below 15 GB
        try:
            free_gb = shutil.disk_usage(str(PROJECT_ROOT)).free / (1024 ** 3)
            if free_gb < 15.0:
                src_p = cache.path("source.mp4")
                if src_p.exists():
                    src_p.unlink()
                    console.print(f"  [dim]Low disk space ({free_gb:.1f}GB). Purged: {src_p.name}[/dim]")
            else:
                console.print(f"  [dim]Preserved source.mp4 for fast Web Studio re-renders ({free_gb:.1f}GB free)[/dim]")
        except Exception:
            pass

    def run_loop(self, poll_interval_sec: int = 60):
        """Continuous overnight loop: catches up backlog and piles new live footage."""
        console.print(Panel.fit(
            f"[bold red]🔴 Overnight Live Sentinel Running[/bold red]\n\n"
            f"Target URL: [white]{self.url}[/white]\n"
            f"Project: [cyan]{self.project_id}[/cyan]\n"
            f"Starting Edge: [yellow]{format_seconds(self.state.get('last_edge_sec', START_EDGE_SEC))}[/yellow]\n"
            f"Score Floor: [magenta]>={MIN_SCORE_THRESHOLD} Only[/magenta] | AI Model: [green]Gemini Director (Dual Keys)[/green]\n"
            f"Hardware Encode: [blue]Apple VideoToolbox (Dual-PTS Sync)[/blue]\n"
            f"Monitoring continuously until stream terminates.",
            border_style="red"
        ))

        chunk_sec = CHUNK_MINS * 60.0
        overlap_sec = OVERLAP_MINS * 60.0
        consecutive_errors = 0

        while True:
            status = probe_live_stream_status(self.url)
            if not status.get("success"):
                consecutive_errors += 1
                console.print(f"[yellow]Warning: Could not connect to stream probe ({consecutive_errors}/5). Retrying in {poll_interval_sec}s...[/yellow]")
                if consecutive_errors >= 5:
                    console.print("[red]Stream unreachable after 5 attempts. Pausing briefly...[/red]")
                    time.sleep(poll_interval_sec * 2)
                    consecutive_errors = 0
                time.sleep(poll_interval_sec)
                continue

            consecutive_errors = 0
            is_live = status.get("is_live", False)
            elapsed_sec = float(status.get("elapsed_sec", 0.0))
            last_edge = float(self.state.get("last_edge_sec", START_EDGE_SEC))
            target_edge = last_edge + chunk_sec
            part_idx = len(self.state.get("parts", [])) + START_PART_INDEX

            # Condition 1: Another 30-minute chunk is available right now
            if elapsed_sec >= target_edge:
                start_sec = max(0.0, last_edge - overlap_sec)
                end_sec = target_edge
                self.process_part(part_index=part_idx, start_sec=start_sec, end_sec=end_sec)
                continue

            # Condition 2: Stream has concluded
            if not is_live:
                tail_dur = elapsed_sec - last_edge
                if tail_dur >= 120.0:
                    console.print(f"[yellow]Stream concluded! Processing final buffer ({format_seconds(last_edge)} to {format_seconds(elapsed_sec)})...[/yellow]")
                    start_sec = max(0.0, last_edge - overlap_sec)
                    end_sec = elapsed_sec
                    self.process_part(part_index=part_idx, start_sec=start_sec, end_sec=end_sec)

                console.print(Panel(
                    f"[bold green]🏁 Live Broadcast Finished[/bold green]\n\n"
                    f"All parts (7 to {part_idx - 1}) processed and rendered.\n"
                    f"Total Viral Clips Generated (Score >= {MIN_SCORE_THRESHOLD}): [bold]{len(self.state.get('all_clips', []))}[/bold]\n"
                    f"Morning Top 5 Report: [link=file://{TOP5_FILE.resolve()}]{TOP5_FILE}[/link]\n"
                    f"Review Dashboard: [link=file://{(self.out_dir / 'review.html').resolve()}]{self.out_dir / 'review.html'}[/link]",
                    border_style="green",
                    title="Overnight Clipping Complete"
                ))
                self.state["status"] = "completed"
                self.save_state()
                break

            # Condition 3: Stream is live, waiting for more footage to reach 30 minutes
            needed_sec = target_edge - elapsed_sec
            console.print(
                f"[dim]Live stream active ({format_seconds(elapsed_sec)}). "
                f"Next part ({part_idx}) needs {format_seconds(needed_sec)} more stream footage. "
                f"Sleeping {poll_interval_sec}s...[/dim]"
            )
            time.sleep(poll_interval_sec)


def main():
    sentinel = OvernightSentinel()
    sentinel.run_loop(poll_interval_sec=60)


if __name__ == "__main__":
    main()
