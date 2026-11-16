"""Command Line Interface for ClipForge using Typer and Rich."""

import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from clipforge.config import load_config
from clipforge.cache import CacheManager, get_video_id
from clipforge.stages.ingest import run_ingest
from clipforge.stages.transcribe import run_transcribe
from clipforge.stages.signals import run_signals
from clipforge.stages.candidates import run_candidates
from clipforge.stages.select import run_select
from clipforge.stages.refine import run_refine
from clipforge.stages.render import run_render
from clipforge.stages.review import generate_review_html

app = typer.Typer(
    name="clipforge",
    help="ClipForge: Local AI video clipping tool for YouTube Shorts, Reels, and TikTok",
    add_completion=False,
)
console = Console()


@app.command()
def check():
    """Verify system requirements, Metal acceleration, and installed tools."""
    console.print(Panel.fit("[bold cyan]ClipForge Environment Check[/bold cyan]"))

    table = Table(title="Hardware & Media Engine Status")
    table.add_column("Component", style="bold")
    table.add_column("Status", style="green")
    table.add_column("Details")

    # 1. Disk Space
    disk = shutil.disk_usage("/")
    free_gb = disk.free / (1024**3)
    table.add_row(
        "Disk Space",
        "PASS" if free_gb >= 10 else "WARN",
        f"{free_gb:.1f} GB free on root volume",
    )

    # 2. ffmpeg & libass & videotoolbox
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        res_ass = subprocess.run(
            ["ffmpeg", "-filters"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        has_ass = "ass" in res_ass.stdout or "subtitles" in res_ass.stdout

        res_enc = subprocess.run(
            ["ffmpeg", "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        has_vtb = "videotoolbox" in res_enc.stdout

        details = f"Found at {ffmpeg_path} (libass: {'YES' if has_ass else 'NO'}, VideoToolbox: {'YES' if has_vtb else 'NO'})"
        table.add_row(
            "FFmpeg Engine",
            "PASS" if (has_ass and has_vtb) else "WARN",
            details,
        )
    else:
        table.add_row("FFmpeg Engine", "FAIL", "ffmpeg not found in PATH")

    # 3. yt-dlp
    ytdlp_path = shutil.which("yt-dlp")
    table.add_row(
        "yt-dlp",
        "PASS" if ytdlp_path else "FAIL",
        ytdlp_path or "Missing",
    )

    # 4. MLX / Metal
    try:
        import mlx.core as mx
        device = mx.default_device()
        table.add_row("Apple MLX / Metal", "PASS", f"Active device: {device}")
    except Exception as e:
        table.add_row("Apple MLX / Metal", "WARN", f"MLX check: {e}")

    # 5. mlx-whisper
    try:
        import mlx_whisper
        table.add_row("MLX Whisper", "PASS", "mlx-whisper installed")
    except Exception as e:
        table.add_row("MLX Whisper", "WARN", f"mlx-whisper import: {e}")

    console.print(table)


@app.command()
def run(
    url_or_file: str = typer.Argument(..., help="YouTube URL or local video file path"),
    target_clips: int = typer.Option(10, "--target-clips", help="Number of target clips to pick"),
    min_score: int = typer.Option(70, "--min-score", help="Minimum virality score (0-100)"),
    layout: str = typer.Option("blur-fill", "--layout", help="Layout style: blur-fill, stack, split"),
    selector: str = typer.Option(
        "file", "--selector", help="Selection mode: 'file' (offline/subscription) or 'api'"
    ),
    from_stage: Optional[str] = typer.Option(
        None, "--from", help="Resume from stage: ingest, transcribe, signals, candidates, select, refine, render"
    ),
    start: Optional[str] = typer.Option(None, "--start", help="Start timestamp HH:MM:SS"),
    end: Optional[str] = typer.Option(None, "--end", help="End timestamp HH:MM:SS"),
    style: str = typer.Option("bold-yellow", "--style", help="Subtitle style preset"),
    censor_captions: bool = typer.Option(False, "--censor-captions", help="Censor profanity in subtitles"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Streamer profile preset"),
):
    """Run the complete ClipForge pipeline from video to rendered vertical clips."""
    config = load_config()
    vid = get_video_id(url_or_file)
    cache = CacheManager(vid)

    console.print(Panel(
        f"[bold cyan]ClipForge Execution Pipeline[/bold cyan]\n"
        f"Source: [white]{url_or_file}[/white]\n"
        f"Video ID: [green]{vid}[/green] | Selector Mode: [yellow]{selector}[/yellow] | Layout: [magenta]{layout}[/magenta]",
        expand=False
    ))

    # Stage 1: Ingest
    with console.status("[bold green]Stage 1: Ingesting video & extracting 16kHz audio..."):
        info = run_ingest(url_or_file, cache, start=start, end=end)
    console.print(f"✔ Ingest complete: [bold]{info.get('title', 'Video')}[/bold] ({info.get('duration', 0.0):.1f}s)")

    # Stage 2: Transcribe
    with console.status("[bold green]Stage 2: Metal-accelerated transcription (MLX Whisper)..."):
        transcript = run_transcribe(cache, model_name=config.whisper.model)
    console.print(f"✔ Transcription complete: {len(transcript.segments)} segments, language: {transcript.language}")

    # Stage 3: Signals
    with console.status("[bold green]Stage 3: Extracting multimodal highlight signals..."):
        signals = run_signals(cache, transcript, config.signals)
    console.print(f"✔ Multi-signal curve computed for {signals.get('duration', 0)} seconds")

    # Stage 4: Candidates
    with console.status("[bold green]Stage 4: Generating candidate peak windows..."):
        candidates = run_candidates(
            cache,
            transcript,
            signals,
            target_clips=target_clips,
            min_length=config.defaults.min_length,
            max_length=config.defaults.max_length,
            preferred_length=config.defaults.preferred_max,
        )
    console.print(f"✔ Generated {len(candidates)} highlight candidate windows")

    # Stage 5: Select
    with console.status(f"[bold green]Stage 5: Selecting viral clips (Mode: {selector})..."):
        raw_clips = run_select(
            cache,
            transcript,
            candidates,
            target_clips=target_clips,
            min_score=min_score,
            selector_mode=selector,
        )
    console.print(f"✔ Selected {len(raw_clips)} candidate clips")

    # Stage 6: Refine
    with console.status("[bold green]Stage 6: Refining boundaries & deduplicating..."):
        refined_clips = run_refine(
            cache,
            transcript,
            raw_clips,
            min_length=config.defaults.min_length,
            max_length=config.defaults.max_length,
        )
    console.print(f"✔ Refined {len(refined_clips)} final clips ready for rendering")

    # Override layout if set by CLI
    if layout:
        for c in refined_clips:
            c.layout = layout

    # Stage 7: Render
    console.print("[bold green]Stage 7: Rendering vertical clips with kinetic captions...[/bold green]")
    out_dir = Path("out") / vid
    rendered = run_render(
        cache,
        transcript,
        refined_clips,
        config.video,
        out_base=Path("out"),
    )
    console.print(f"✔ Rendered {len(rendered)} MP4 clips in [bold]{out_dir}[/bold]")

    # Stage 8: Review HTML
    review_path = generate_review_html(refined_clips, cache, out_dir)
    console.print(Panel(
        f"[bold green]✨ Processing Complete![/bold green]\n\n"
        f"Clips Directory: [link=file://{out_dir.resolve()}]{out_dir}[/link]\n"
        f"Review Dashboard: [link=file://{review_path.resolve()}]{review_path}[/link]\n\n"
        f"[dim]Run 'clipforge review {vid}' anytime to open the review dashboard.[/dim]",
        title="ClipForge Ready",
        border_style="green",
    ))


@app.command()
def review(
    video_id: str = typer.Argument(..., help="Video ID or folder in cache/ or out/"),
):
    """Open or regenerate the static review.html page for a processed video."""
    review_path = Path("out") / video_id / "review.html"
    if not review_path.exists():
        review_path = Path("cache") / video_id / "review.html"

    if review_path.exists():
        console.print(f"Opening review dashboard: [link=file://{review_path.resolve()}]{review_path}[/link]")
        subprocess.run(["open", str(review_path)])
    else:
        console.print(f"[yellow]Review page not found for {video_id}[/yellow]")


@app.command()
def clean(
    video_id: str = typer.Argument(..., help="Video ID to clean large video files for"),
):
    """Free disk space by deleting cached raw video/audio while preserving transcripts and metadata JSON."""
    cache_dir = Path("cache") / video_id
    if not cache_dir.exists():
        console.print(f"[red]Directory not found: {cache_dir}[/red]")
        return

    freed = 0
    for ext in ("*.mp4", "*.mkv", "*.webm", "*.wav"):
        for f in cache_dir.glob(ext):
            sz = f.stat().st_size
            f.unlink()
            freed += sz
    console.print(f"[green]Cleaned cache for {video_id}. Freed {freed / (1024**2):.1f} MB.[/green]")


@app.command()
def cost(
    video_id: str = typer.Argument(..., help="Video ID to view resource, timing, and token metrics for"),
):
    """Report execution timings, transcription duration, and token counts."""
    metrics_path = Path("cache") / video_id / "metrics.json"
    if metrics_path.exists():
        console.print(metrics_path.read_text())
    else:
        console.print("[dim]No metrics recorded yet.[/dim]")


if __name__ == "__main__":
    app()
