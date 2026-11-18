# CLIPIT AGENTS & SYSTEM CONTEXT (AGENTS.md)
================================================================================
Target Audience: AI Coding Agents (Cursor, Claude Code, Antigravity, Windsurf, Copilot)
                 & Human Collaborators (macOS & Windows).
Last Updated: November 2026
================================================================================

## 1. PROJECT OVERVIEW & PHILOSOPHY

ClipIt is an autonomous, local-first AI video clipping engine and studio tailored
for gaming streamers and editors (e.g., GTA 6 reactions, Kai Cenat, iShowSpeed, Twitch streams).
It ingests 2-12 hour raw stream VODs (via YouTube URL or local MP4), analyzes audio and chat signals,
identifies viral high-energy peaks, reframes the video to 9:16 vertical format with 3 layout options,
generates kinetic bouncing subtitles with auto-emojis, and produces ready-to-publish social packs.

### Key Tenets:
1. **Local-First & Zero Cloud Render Cost**: Video processing, audio analysis, face detection,
   and hardware rendering run locally on your machine via FFmpeg and local neural models.
2. **Deterministic Offline Fallbacks**: Works completely offline without any API keys (using
   heuristic virality scoring). If a `GEMINI_API_KEY` is present, it optionally invokes Gemini 2.5 Flash
   to act as an AI Director for viral scoring and hook generation.
3. **Cross-Platform Compatibility**: Fully engineered for both macOS (Apple Silicon Metal acceleration)
   and Windows (NVIDIA NVENC, Intel QSV, AMD AMF, and faster-whisper with CUDA/CPU).
4. **Zero-Build Web Studio**: Vanilla HTML5, CSS3, and ES Modules (`src/clipit/web/static/`).
   No Node.js, Webpack, Vite, or npm build steps required.

---

## 2. THE 8-STAGE AUTONOMOUS PIPELINE

The core pipeline is divided into 8 modular stages located under `src/clipit/stages/`:

```
[1. Ingest] ──► [2. Transcribe] ──► [3. Signals] ──► [4. Candidates]
                                                            │
[8. Export] ◄── [7. Render]     ◄── [6. Refine]  ◄── [5. Select (AI)]
```

### Stage 1: Ingest (`stages/ingest.py`)
- **Tool**: `yt-dlp` + FFmpeg.
- **Inputs**: YouTube URL or local file path.
- **Outputs**:
  - `cache/<video_id>/source.mp4`: Best MP4 video stream (up to 1080p60).
  - `cache/<video_id>/audio.wav`: 16kHz mono 16-bit PCM audio stream for transcription & signal processing.
  - `cache/<video_id>/info.json`: Video title, channel, duration, resolution, thumbnail URL.

### Stage 2: Transcribe (`stages/transcribe.py`)
- **Tool**:
  - **macOS (arm64)**: `mlx-whisper` (Metal GPU acceleration).
  - **Windows / Linux / x86**: `faster-whisper` (CTranslate2 with auto CUDA / CPU support).
  - Fallback: `openai-whisper`.
- **Outputs**:
  - `cache/<video_id>/transcript.json`: Complete word-level timestamps (`w`, `start`, `end`, `prob`).

### Stage 3: Signals Intelligence (`stages/signals.py`)
- **Tool**: NumPy, SciPy (`scipy.io.wavfile`).
- **Processing**:
  - Audio loudness Z-score and vocal excitement (high-frequency spectral energy / screaming proxy).
  - Chat velocity & spike detection (if live chat is downloaded).
  - Gaming keyword density ("oh my god", "no way", "insane", "clutch", "glitch").
  - Scene cut detection via `scenedetect`.
- **Outputs**:
  - `cache/<video_id>/signals.json`: Second-by-second composite virality scores.

### Stage 4: Candidate Slicing (`stages/candidates.py`)
- **Processing**: Identifies local score peaks, brackets optimal 15s-60s clip windows around each peak,
  merges overlapping bursts, and applies non-maximum suppression (NMS).
- **Outputs**:
  - `cache/<video_id>/candidates.json`: Ranked candidate timecode ranges.

### Stage 5: Selection & Virality Scoring (`stages/select.py`)
- **Processing**:
  - If `GEMINI_API_KEY` is set: Evaluates candidate transcripts with Gemini 2.5 Flash using structured output.
    Produces titles, hook headlines, virality scores (0-100), categories, and "why viral" explanations.
  - If offline / no key: Uses heuristic statistical ranking from Stage 3 signals.
  - **Auto Clips Mode**: If `target_clips` is set to `0` or `"auto"`, automatically extracts all top viral
    highlights ($1 \le N < 30$) above quality threshold $\ge 75$.
- **Outputs**:
  - `cache/<video_id>/clips.json`: Selected top viral clip candidates.

### Stage 6: Boundary Refinement (`stages/refine.py`)
- **Processing**: Snaps clip start/end times strictly to word boundaries from `transcript.json` to avoid
  cutting sentences mid-word. Trims leading/trailing silences.
- **Outputs**:
  - `cache/<video_id>/refined_clips.json`: Clean, broadcast-ready clip timestamps.

### Stage 7: Motion Render (`stages/render.py`, `stages/layout.py`, `stages/captions.py`)
- **Hardware Encoders**:
  - macOS: `h264_videotoolbox` (Apple Silicon Hardware Encoder).
  - Windows NVIDIA: `h264_nvenc`.
  - Windows Intel: `h264_qsv`.
  - Windows AMD: `h264_amf`.
  - Fallback: `libx264` (CPU).
- **Subtitles & Captions**:
  - Kinetic, word-by-word highlighted ASS subtitles with auto-emojis (💀, 😭, 😱, 🔥, 🏆, 🚗).
  - Custom font loading (`assets/fonts/Anton-Regular.ttf`) and styling presets (`styles/*.yaml`).
- **Layout Generation (3 Variants per Clip)**:
  1. `Reaction Stacked` (`<clip_id>.mp4`): Streamer facecam on top (1:1 square), gameplay below (4:3 / 16:9),
     animated kinetic subtitles sitting directly across the seam.
  2. `Reference Letterbox / Blur-Fill` (`<clip_id>_blur.mp4`): 16:9 stream centered with a dynamic blurred background
     and a bold 3-line top hook headline.
  3. `Content Deframed 9:16` (`<clip_id>_reframed.mp4`): Action-tracked full-bleed vertical crop focused on gameplay.
- **Outputs**:
  - `out/<video_id>/c01.mp4`, `c01_blur.mp4`, `c01_reframed.mp4`, etc.

### Stage 8: Export & Quality Sentinel (`stages/export.py`, `stages/review.py`)
- **Outputs**:
  - `ready_to_post/<video_id>/`: Final selected clips renamed by title.
  - `copy_paste_pack.txt`: Ready-to-copy social media titles, descriptions, viral hooks, and hashtags.
  - `schedule.csv`: Structured publishing schedule spreadsheet.
  - `out/<video_id>/review.html`: Standalone browser review and playback dashboard.

---

## 3. WEB STUDIO ARCHITECTURE (`src/clipit/web/`)

ClipIt Studio provides a real-time reactive Web UI for monitoring pipeline execution, previewing clips,
adjusting crops, and publishing.

- **Frontend Core (`src/clipit/web/static/`)**:
  - `index.html`: Multi-step single page app (Step 1: Ingest & Settings, Step 2: Live Pipeline, Step 3: Studio).
  - `style.css`: Modern Dark/Cobalt theme with responsive layouts (iOS Safari zoom-fix compliant).
  - `app.js`: State manager with browser history support (`history.pushState` / `popstate`), deep-linking
    (`?step=studio&project=...`), and real-time SSE listener.
- **Backend Server (`src/clipit/web/server.py`)**:
  - Native Python HTTP Server running on `http://127.0.0.1:8765`.
  - Server-Sent Events (SSE) endpoint: `GET /api/stream-workflow?session_id=...` emitting:
    `pipeline_start`, `stage_start`, `agent_log`, `stage_complete`, `pipeline_complete`, `pipeline_error`.
  - REST Endpoints:
    - `GET /api/projects`: Lists completed projects.
    - `GET /api/project?video_id=...`: Retrieves clip metadata, layouts, and facecam info.
    - `GET /api/video/<video_id>/<file>`: High-speed video streaming with HTTP `Range: bytes` support.
    - `POST /api/save-layout`: Saves user-adjusted crop boundaries and facecam box.
    - `POST /api/render-preview`: Live ffmpeg-generated preview frame for crop editor.
    - `POST /api/export`: Publishes selected clips to `ready_to_post/`.
    - `POST /api/delete-rejected`: Permanently deletes all rejected clips and their rendered MP4 files from disk.
    - `POST /api/delete-clip`: Permanently deletes a single clip and its variants from disk.
    - `GET /api/retention-status`: Scans all projects and reports creation age and 30-day lifecycle status.
    - `POST /api/retention-clean`: Runs retention cleanup for projects older than 30 days.

---

## 4. COMMAND LINE INTERFACE (CLI) & TOOLS

All features are exposed via the `clipit` CLI:

```bash
# 1. Environment & Hardware Diagnostics (Run this first!)
clipit check

# 2. Run the Full End-to-End Pipeline
clipit run "https://youtu.be/E07OTlhncP0" --target auto --style bold-yellow --profile ishowspeed

# 3. Launch the Web Studio UI
clipit serve --port 8765

# 4. Step-by-Step Granular Commands
clipit ingest "https://youtu.be/E07OTlhncP0"
clipit transcribe cache/yt_E07OTlhncP0
clipit signals cache/yt_E07OTlhncP0
clipit candidates cache/yt_E07OTlhncP0
clipit select cache/yt_E07OTlhncP0 --target auto
clipit render cache/yt_E07OTlhncP0 --style bold-yellow
clipit export cache/yt_E07OTlhncP0
```

---

## 5. WINDOWS SETUP & COLLABORATION GUIDE

If you or your collaborator are running Windows:

### Step 1: Install Python (3.10 to 3.12)
- Download from python.org or via winget:
  ```powershell
  winget install Python.Python.3.11
  ```
  *(Important: Check "Add python.exe to PATH" during installation).*

### Step 2: Install FFmpeg
FFmpeg is mandatory for video demuxing, filtering, and hardware encoding:
```powershell
winget install Gyan.FFmpeg
# Or via Chocolatey:
choco install ffmpeg-full
```
Verify in PowerShell:
```powershell
ffmpeg -version
```
Ensure `ffmpeg` and `ffprobe` are recognized.

### Step 3: Install yt-dlp
```powershell
winget install yt-dlp
# Or via pip:
pip install yt-dlp
```

### Step 4: Clone Repository & Create Virtual Environment
In PowerShell (Run as Administrator or ensure ExecutionPolicy allows script execution):
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

git clone <repo-url>
cd "Clipping GTA 6"

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -e .
```
*Note: `pyproject.toml` automatically installs `faster-whisper` on Windows/Linux, and `mlx-whisper` on macOS.*

### Step 5: Verify the Environment
```powershell
clipit check
```
If you have an NVIDIA GPU, `clipit check` will detect `NVENC (NVIDIA)` and `faster-whisper` automatically.

---

## 6. TROUBLESHOOTING & COMMON PITFALLS

### Issue 1: FFmpeg "subtitles/ass filter not found" or "libass not found"
- **Cause**: Minimal FFmpeg builds lack libass.
- **Fix (Windows)**: Run `winget install Gyan.FFmpeg` (Gyan builds include full libass and hardware encoders).
- **Fix (macOS)**: Run `brew install ffmpeg`.

### Issue 2: Windows Path Colon in FFmpeg Filter (`C:\...`)
- **Cause**: FFmpeg filtergraphs interpret colons `:` as option separators.
- **Resolution**: ClipIt automatically converts paths relative to the current working directory (`cache/...`),
  eliminating drive letters and avoiding colon-escaping issues. Always use `Path.relative_to(Path.cwd()).as_posix()`
  or forward slashes when writing FFmpeg filters.

### Issue 3: "Port 8765 already in use"
- **macOS / Linux**:
  ```bash
  lsof -ti :8765 | xargs kill -9
  ```
- **Windows**:
  ```powershell
  Get-NetTCPConnection -LocalPort 8765 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
  ```

### Issue 4: Gemini API Quota or Offline Mode
- If `GEMINI_API_KEY` is not provided or runs out of quota, ClipIt automatically falls back
  to the offline heuristic selector (`stages/select.py`). It will never crash due to a missing key.
- To set your key:
  - macOS/Linux: `export GEMINI_API_KEY="your-api-key"`
  - Windows PowerShell: `$env:GEMINI_API_KEY="your-api-key"`
  - Or create a `.env` file in the project root: `GEMINI_API_KEY=your-api-key`

### Issue 5: PowerShell "Running Scripts is Disabled on this System"
- Run:
  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  .\.venv\Scripts\Activate.ps1
  ```

---

## 8. 24/7 LIVE STREAM SENTINEL & CONTINUOUS CHUNKING ARCHITECTURE

When processing ongoing live broadcasts (e.g. YouTube Live, Twitch), `stages/live_monitor.py` provides autonomous continuous clipping:

### Core Mechanics:
1. **Periodic Chunking (`--chunk-mins 30`)**:
   - Downloads the live DVR segment using `yt-dlp` with `--download-sections *{start}-{end}`.
   - Operates in isolated part caches (`cache/<video_id>_ptXX/`) so each chunk is clean and atomic.
2. **Lookback Overlap Window (`--overlap-mins 5`)**:
   - Each part slices from `start_sec = max(0, last_edge - overlap_sec)` to `end_sec = last_edge + chunk_sec`.
   - Prevents boundary clips (moments happening right on the 30-min mark) from being split in half or missed.
3. **Boundary Deduplication**:
   - When new candidates are found in the overlap window, `deduplicate_clips()` compares their start timestamps against clips from prior parts.
   - Duplicate moments are discarded; genuinely new viral moments that occurred across the seam are preserved.
4. **Part Naming & Multi-Part Sidecars**:
   - Clips are named `pt01_c01.mp4`, `pt02_c01.mp4`, etc., and staged into both `out/<video_id>/part_XX/` and `out/<video_id>/`.
   - Sidecar metadata is enriched with `part: "Part 2"`, `stream_window: "00:22:07 - 00:57:07"`, and absolute stream timestamps.
   - Master review dashboard (`out/<video_id>/review.html`) consolidates clips across all parts with part filter tabs.
5. **Real-Time Web Studio SSE Bridge**:
   - Every CLI sentinel run emits real-time events (`pipeline_start`, `stage_start`, `agent_log`, `stage_complete`) via `POST /api/internal/emit-event` to the local Studio server (`http://127.0.0.1:8765`).
   - The Studio dashboard automatically displays the live running pipeline in `#v2` with live telemetry.
   - Endpoints:
     - `POST /api/live/start`: Starts live sentinel monitoring in a background thread.
     - `GET /api/live/status`: Returns current watching state and part history.
     - `POST /api/live/stop`: Gracefully stops the sentinel.
6. **Microsecond A/V Synchronization Guarantee**:
   - Video filterchain enforces `[base]setpts=PTS-STARTPTS,ass=...,setpts=PTS-STARTPTS[v]`.
   - Audio filterchain enforces `asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0`.
   - Output uses `-avoid_negative_ts make_zero`.
   - Video streaming responses send `Cache-Control: no-cache, no-store, must-revalidate` to prevent HTTP 206 Range browser desync.

---

## 9. GIT HYGIENE & FILE POLICIES

To keep the repository clean and avoid git bloat:
1. **NEVER COMMIT VIDEOS OR AUDIO**: `.gitignore` is configured to ignore `cache/`, `out/`, `ready_to_post/`,
   `*.mp4`, `*.wav`, `*.mkv`, and `*.mov`. Only `tests/fixtures/sample.mp4` (a tiny 490KB test fixture) is tracked.
2. **NEVER COMMIT SECRETS**: `.env` and `.env.local` are strictly ignored.
3. **PRESERVE SYSTEM INTEGRITY**: Do not alter `.venv/` or external model caches directly.
================================================================================
