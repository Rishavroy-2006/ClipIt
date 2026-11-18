"""Command Line Interface for ClipIt using Typer and Rich."""

import sys
import shutil
import subprocess
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from clipit.config import load_config
from clipit.cache import CacheManager, get_video_id
from clipit.stages.ingest import run_ingest
from clipit.stages.transcribe import run_transcribe
from clipit.stages.signals import run_signals
from clipit.stages.candidates import run_candidates
from clipit.stages.select import run_select
from clipit.stages.refine import run_refine
from clipit.stages.render import run_render
from clipit.stages.review import generate_review_html
from clipit.stages.export import run_export
from clipit.llm.selector_api import get_api_key

app = typer.Typer(
    name="clipit",
    help="ClipIt: Local AI video clipping tool for YouTube Shorts, Reels, and TikTok",
    add_completion=False,
)
console = Console()


@app.command()
def check():
    """Verify system requirements, hardware acceleration, and installed tools (macOS & Windows)."""
    import os
    import platform

    console.print(Panel.fit("[bold cyan]ClipIt Environment Check[/bold cyan]"))

    table = Table(title="Hardware & Media Engine Status")
    table.add_column("Component", style="bold")
    table.add_column("Status", style="green")
    table.add_column("Details")

    # 1. Platform & OS
    os_name = platform.system()
    arch = platform.machine()
    table.add_row("Platform", "INFO", f"{os_name} ({arch}) - Python {platform.python_version()}")

    # 2. Disk Space
    root_path = Path.cwd().anchor or "/"
    try:
        disk = shutil.disk_usage(root_path)
        free_gb = disk.free / (1024**3)
        table.add_row(
            "Disk Space",
            "PASS" if free_gb >= 10 else "WARN",
            f"{free_gb:.1f} GB free on {root_path}",
        )
    except Exception as e:
        table.add_row("Disk Space", "WARN", f"Could not check: {e}")

    # 3. ffmpeg & libass & hardware encoders
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        res_ass = subprocess.run(
            ["ffmpeg", "-filters"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        has_ass = "ass" in res_ass.stdout or "subtitles" in res_ass.stdout

        res_enc = subprocess.run(
            ["ffmpeg", "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        enc_out = res_enc.stdout
        available_encs = []
        if "h264_videotoolbox" in enc_out:
            available_encs.append("VideoToolbox (Apple)")
        if "h264_nvenc" in enc_out:
            available_encs.append("NVENC (NVIDIA)")
        if "h264_qsv" in enc_out:
            available_encs.append("QSV (Intel)")
        if "h264_amf" in enc_out:
            available_encs.append("AMF (AMD)")
        if "libx264" in enc_out:
            available_encs.append("libx264 (CPU)")

        enc_str = ", ".join(available_encs) if available_encs else "None"
        hw_ready = bool(available_encs)
        table.add_row(
            "FFmpeg Engine",
            "PASS" if (has_ass and hw_ready) else "WARN",
            f"libass: {'YES' if has_ass else 'NO'} | Encoders: {enc_str}",
        )
    else:
        table.add_row("FFmpeg Engine", "FAIL", "ffmpeg not found in PATH")

    # 4. yt-dlp
    ytdlp_path = shutil.which("yt-dlp")
    table.add_row(
        "yt-dlp",
        "PASS" if ytdlp_path else "FAIL",
        ytdlp_path or "Missing (install via pip install yt-dlp or winget/brew)",
    )

    # 5. Whisper Speech-to-Text Backend
    whisper_found = False
    if os_name == "Darwin" and arch == "arm64":
        try:
            import mlx.core as mx
            import mlx_whisper
            device = mx.default_device()
            table.add_row("Whisper (MLX Metal)", "PASS", f"Apple Silicon Metal acceleration active ({device})")
            whisper_found = True
        except Exception as e:
            table.add_row("Whisper (MLX Metal)", "WARN", f"mlx-whisper check: {e}")

    if not whisper_found:
        try:
            from faster_whisper import WhisperModel
            table.add_row("Whisper (faster-whisper)", "PASS", "faster-whisper installed (CUDA / CPU auto-detect)")
            whisper_found = True
        except Exception:
            pass

    if not whisper_found:
        try:
            import whisper
            table.add_row("Whisper (openai-whisper)", "PASS", "openai-whisper installed")
            whisper_found = True
        except Exception:
            pass

    if not whisper_found:
        table.add_row(
            "Whisper Backend",
            "WARN",
            "Install mlx-whisper (macOS) or faster-whisper (Windows/Linux): pip install faster-whisper",
        )

    # 6. Gemini LLM API Key (Optional for smart AI selection)
    from clipit.llm.selector_api import get_api_keys
    keys = get_api_keys()
    if len(keys) > 1:
        table.add_row("Gemini Director API", "PASS", f"{len(keys)} API keys configured (Automatic Failover active)")
    elif len(keys) == 1:
        table.add_row("Gemini Director API", "PASS", "1 API key detected (AI Virality Selection enabled)")
    else:
        table.add_row("Gemini Director API", "INFO", "GEMINI_API_KEY unset (defaults to offline heuristic selector)")


    console.print(table)


@app.command()
def run(
    url_or_file: str = typer.Argument(..., help="YouTube URL or local video file path"),
    target_clips: int = typer.Option(10, "--target-clips", help="Number of target clips to pick"),
    min_score: int = typer.Option(70, "--min-score", help="Minimum virality score (0-100)"),
    layout: str = typer.Option("blur-fill", "--layout", help="Layout style: blur-fill, stack, split"),
    selector: str = typer.Option(
        "api" if get_api_key() else "file", "--selector", help="Selection mode: 'api', 'file', or 'heuristic'"
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
    """Run the complete ClipIt pipeline from video to rendered vertical clips."""
    config = load_config()
    vid = get_video_id(url_or_file)
    cache = CacheManager(vid)

    console.print(Panel(
        f"[bold cyan]ClipIt Execution Pipeline[/bold cyan]\n"
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
        style_name=style,
        profile_name=profile,
    )
    console.print(f"✔ Rendered {len(rendered)} MP4 clips in [bold]{out_dir}[/bold]")

    # Stage 8: Review HTML
    review_path = generate_review_html(refined_clips, cache, out_dir)
    console.print(Panel(
        f"[bold green]✨ Processing Complete![/bold green]\n\n"
        f"Clips Directory: [link=file://{out_dir.resolve()}]{out_dir}[/link]\n"
        f"Review Dashboard: [link=file://{review_path.resolve()}]{review_path}[/link]\n\n"
        f"[dim]Run 'clipit review {vid}' anytime to open the review dashboard.[/dim]",
        title="ClipIt Ready",
        border_style="green",
    ))


@app.command(name="live")
def live(
    url: str = typer.Argument(..., help="YouTube Live Stream URL or video ID"),
    chunk_mins: int = typer.Option(30, "--chunk-mins", "-c", help="Interval duration in minutes for each part (default: 30)"),
    overlap_mins: int = typer.Option(5, "--overlap-mins", "-o", help="Lookback overlap in minutes to protect boundary clips (default: 5)"),
    target_clips: int = typer.Option(6, "--target-clips", "-n", help="Target viral clips per part"),
    layout: str = typer.Option("stack", "--layout", help="Layout style: stack, blur-fill, reframed"),
    style: str = typer.Option("bold-yellow", "--style", help="Subtitle style preset"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Streamer profile preset (e.g. ishowspeed)"),
    once: bool = typer.Option(False, "--once", help="Process only the next pending part and exit"),
    max_parts: Optional[int] = typer.Option(None, "--max-parts", help="Maximum number of parts to process before stopping"),
):
    """Continuously monitor a YouTube live stream, fetch 30-min chunks with 5-min overlap, and clip until stream ends."""
    from clipit.stages.live_monitor import LiveStreamMonitor
    monitor = LiveStreamMonitor(
        url=url,
        chunk_mins=chunk_mins,
        overlap_mins=overlap_mins,
        target_clips=target_clips,
        layout=layout,
        style=style,
        profile=profile,
    )
    if once:
        monitor.run_next_pending_part()
    else:
        monitor.run_loop(max_parts=max_parts)


@app.command()
def export(
    video_id: str = typer.Argument(..., help="Video ID (e.g. yt_E07OTlhncP0)"),
    manifest: Optional[Path] = typer.Option(
        None, "--manifest", "-m", help="Path to approved.json (optional, auto-detected)"
    ),
):
    """Package approved clips into ready_to_post/ with viral titles and copy-paste pack."""
    console.print(f"[bold cyan]Packaging approved clips for:[/bold cyan] {video_id}")
    try:
        dest_dir = run_export(video_id, manifest_path=manifest)
        pack_txt = dest_dir / "copy_paste_pack.txt"
        csv_file = dest_dir / "schedule.csv"

        console.print(Panel(
            f"[bold green]✔ Export Staged Successfully![/bold green]\n\n"
            f"📁 Publishing Folder: [link=file://{dest_dir.resolve()}]{dest_dir}[/link]\n"
            f"📄 Copy-Paste Pack: [link=file://{pack_txt.resolve()}]{pack_txt}[/link]\n"
            f"📊 Schedule CSV: [link=file://{csv_file.resolve()}]{csv_file}[/link]\n\n"
            f"[dim]The videos have been renamed with viral slugs and titles ready for upload.[/dim]",
            title="ClipIt Export Ready",
            border_style="green",
        ))
    except Exception as e:
        console.print(f"[red]Export failed:[/red] {e}")


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
    video_id: Optional[str] = typer.Argument(None, help="Optional Video ID to clean large video files for"),
    days: int = typer.Option(30, "--days", "-d", help="Max age in days for automatic 30-day retention lifecycle purge"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview expired projects without deleting files"),
):
    """Clean expired video assets (30-day lifecycle) or purge video cache for a specific project."""
    if video_id:
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
    else:
        from clipit.retention import enforce_retention_policy
        console.print(f"[bold cyan]Running {days}-day retention lifecycle check...[/bold cyan]")
        res = enforce_retention_policy(max_age_days=days, dry_run=dry_run)
        if res["purged_count"] == 0:
            console.print(f"[green]✔ All {res['scanned_projects']} projects are within the {days}-day lifecycle. Zero expired files.[/green]")
        else:
            action = "Would purge" if dry_run else "Purged"
            console.print(f"[yellow]{action} {res['purged_count']} expired project(s). Freed {res['freed_gb']:.2f} GB disk space.[/yellow]")
            for p in res["purged_projects"]:
                console.print(f" • [bold]{p['video_id']}[/bold] (Age: {p['age_days']} days, Created: {p['created_at']}) -> Freed {p['freed_mb']} MB")


@app.command(name="serve")
def serve(
    port: int = typer.Option(8765, "--port", "-p", help="Port to run the studio server on"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open browser on startup"),
):
    """Launch the ClipIt AI Studio interactive web application."""
    from clipit.web.server import start_server
    start_server(port=port, open_browser=open_browser)


@app.command(name="studio")
def studio(
    port: int = typer.Option(8765, "--port", "-p", help="Port to run the studio server on"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open browser on startup"),
):
    """Alias for 'serve': Launch the ClipIt AI Studio interactive web application."""
    from clipit.web.server import start_server
    start_server(port=port, open_browser=open_browser)


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


@app.command(name="youtube-status")
def youtube_status():
    """Check YouTube API configuration and channel connection status."""
    from clipit.youtube import is_configured, is_connected, get_channel_info

    configured = is_configured()
    connected = is_connected()

    console.print(Panel.fit("[bold cyan]YouTube Channel Status[/bold cyan]"))
    if not configured:
        console.print("[yellow]client_secrets.json not found in project root.[/yellow]")
        console.print("Please place your downloaded OAuth client JSON in the project root as client_secrets.json.")
        return

    console.print("[green]✔ client_secrets.json configured.[/green]")
    if not connected:
        console.print("[yellow]Channel not connected yet. Run: [bold]clipit youtube-auth[/bold][/yellow]")
        return

    ch = get_channel_info()
    if ch:
        console.print(f"[bold green]✔ Connected to YouTube Channel:[/bold green] {ch.get('title')} ({ch.get('customUrl', '')})")
        console.print(f"  Subscribers: {ch.get('subscribers')} | Total Videos: {ch.get('videoCount')}")
    else:
        console.print("[green]✔ Authorized token active.[/green]")


@app.command(name="youtube-auth")
def youtube_auth():
    """Authorize and link your YouTube channel via Google OAuth2."""
    from clipit.youtube import is_configured, authenticate_channel

    if not is_configured():
        console.print("[red]client_secrets.json not found. Please place it in the project root first.[/red]")
        raise typer.Exit(1)

    console.print("[bold cyan]Starting Google OAuth2 authorization in your default browser...[/bold cyan]")
    res = authenticate_channel(open_browser=True)
    if res.get("success"):
        ch = res.get("channel") or {}
        console.print(f"[bold green]🎉 Successfully linked channel:[/bold green] {ch.get('title', 'YouTube Channel')} ({ch.get('customUrl', '')})")
    else:
        console.print(f"[bold red]Authorization failed:[/bold red] {res.get('error')}")


@app.command(name="youtube-disconnect")
def youtube_disconnect():
    """Unlink current YouTube channel and remove local token."""
    from clipit.youtube import disconnect_channel
    if disconnect_channel():
        console.print("[green]✔ Successfully disconnected YouTube channel.[/green]")
    else:
        console.print("[yellow]No active YouTube connection found.[/yellow]")


@app.command(name="youtube-upload")
def youtube_upload(
    file_path: Path = typer.Argument(..., help="Path to vertical MP4 video file to upload"),
    title: str = typer.Option("", "--title", "-t", help="Title for the YouTube Short"),
    description: str = typer.Option("", "--desc", "-d", help="Description for the YouTube Short"),
    privacy: str = typer.Option("unlisted", "--privacy", "-p", help="Privacy: public, unlisted, or private"),
):
    """Directly upload a video file as a YouTube Short to your linked channel."""
    from clipit.youtube import is_connected, get_channel_info, upload_short
    import json
    import time

    if not is_connected():
        console.print("[red]YouTube channel is not connected. Run 'clipit youtube-auth' first.[/red]")
        raise typer.Exit(1)

    if not file_path.exists():
        console.print(f"[red]Video file not found: {file_path}[/red]")
        raise typer.Exit(1)

    ch = get_channel_info() or {}
    console.print(Panel.fit(f"[bold cyan]Publishing Short to YouTube Channel:[/bold cyan] {ch.get('title', 'YouTube')} ({ch.get('customUrl', '')})"))

    # Auto-detect title and description from sidecar JSON if not provided
    auto_title = title
    auto_desc = description
    sidecar_json = file_path.with_suffix(".json")
    if not auto_title and sidecar_json.exists():
        try:
            with open(sidecar_json, "r", encoding="utf-8") as f:
                c_data = json.load(f)
                auto_title = c_data.get("title", "")
                if not auto_desc:
                    tags = " ".join(c_data.get("hashtags", ["#Shorts", "#Gaming"]))
                    auto_desc = f"{auto_title}\n\n{tags}\n\n{c_data.get('why_viral', '')}"
        except Exception:
            pass

    if not auto_title:
        auto_title = file_path.stem.replace("_", " ").title()

    console.print(f" • [bold]Title:[/bold] {auto_title}")
    console.print(f" • [bold]File:[/bold] {file_path} ({file_path.stat().st_size / (1024**2):.1f} MB)")
    console.print(f" • [bold]Privacy:[/bold] {privacy.upper()}")

    with console.status("[bold green]Uploading video to YouTube...[/bold green]"):
        t0 = time.time()
        res = upload_short(
            file_path=file_path,
            title=auto_title,
            description=auto_desc,
            privacy_status=privacy,
        )
        elapsed = time.time() - t0

    if res.get("success"):
        console.print(f"\n[bold green]🎉 Published to YouTube Shorts in {elapsed:.1f}s![/bold green]")
        console.print(f" • [bold]Video ID:[/bold] {res.get('video_id')}")
        console.print(f" • [bold cyan]URL:[/bold cyan] {res.get('url')}")
    else:
        console.print(f"\n[bold red]Upload failed:[/bold red] {res.get('error')}")
        raise typer.Exit(1)


@app.command(name="test-keys")
def test_keys():
    """Test all configured Gemini API keys and check their health, latency, and quota."""
    from clipit.llm.selector_api import get_api_keys
    from google import genai
    from google.genai import types
    import time

    keys = get_api_keys()
    console.print(Panel.fit("[bold cyan]Gemini API Keys Diagnostic & Health Check[/bold cyan]"))

    if not keys:
        console.print("[yellow]No Gemini API keys found in environment or .env.[/yellow]")
        console.print("Add GEMINI_API_KEY and GEMINI_API_KEY_2 to your .env file to enable AI virality selection.")
        return

    table = Table(title=f"Configured API Keys ({len(keys)} detected)")
    table.add_column("Key #", style="bold")
    table.add_column("Masked Key", style="dim")
    table.add_column("Model")
    table.add_column("Status")
    table.add_column("Latency")
    table.add_column("Details")

    all_valid = True
    for i, k in enumerate(keys, 1):
        masked = k[:6] + "..." + k[-4:] if len(k) > 10 else "key"
        model_name = "gemini-3.1-flash-lite"
        t0 = time.time()
        try:
            client = genai.Client(api_key=k, http_options=types.HttpOptions(timeout=10000))
            resp = client.models.generate_content(
                model=model_name,
                contents="Reply with the single word PONG",
            )
            lat = (time.time() - t0) * 1000
            table.add_row(
                f"#{i}",
                masked,
                model_name,
                "[bold green]ACTIVE[/bold green]",
                f"{lat:.0f} ms",
                f"Valid (Response: {resp.text.strip()[:10]})",
            )
        except Exception as e:
            all_valid = False
            lat = (time.time() - t0) * 1000
            err_str = str(e)
            if "429" in err_str or "quota" in err_str.lower():
                status_text = "[bold yellow]QUOTA EXHAUSTED[/bold yellow]"
            else:
                status_text = "[bold red]ERROR[/bold red]"
            table.add_row(
                f"#{i}",
                masked,
                model_name,
                status_text,
                f"{lat:.0f} ms",
                err_str[:40] + "...",
            )

    console.print(table)
    if len(keys) >= 2:
        console.print("\n[bold green]✔ Dual-Key Automatic Failover is ACTIVE.[/bold green] If Key #1 hits rate limits, requests will automatically transition to Key #2 seamlessly.")
    else:
        console.print("\n[dim]Tip: Add GEMINI_API_KEY_2 in .env to enable automatic backup key failover.[/dim]")




if __name__ == "__main__":
    app()
