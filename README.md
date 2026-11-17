# ClipForge Studio 🎬⚡

**ClipForge** is a local-first, privacy-conscious AI video clipping engine and Web Studio. It transforms raw 2–12 hour gaming streams and long VODs (via YouTube link or local MP4) into high-energy, ready-to-publish vertical 9:16 clips for **YouTube Shorts**, **TikTok**, and **Instagram Reels** with zero minute quotas and zero cloud render fees.

Works on **macOS (Apple Silicon Metal)** and **Windows (NVIDIA NVENC / AMD / Intel / CPU)**.

---

## ⚡ Highlights

* **100% Local Video & Audio Pipeline**: High-speed demuxing, audio normalization, face tracking, and hardware rendering run entirely on your local machine via FFmpeg.
* **Smart Highlight Detection**: Evaluates vocal excitement, audio loudness Z-scores, chat velocity spikes, gaming keywords, and scene cuts.
* **Auto Virality Selection**:
  * **Auto Mode**: Finds the top high-scoring clips ($1 \le N < 30$) automatically without filler.
  * **AI Director Mode**: Optional Gemini 2.5 Flash integration for hook headlines, virality scores (0–100), and custom titles.
  * **Offline Mode**: Operates 100% offline without any API keys using statistical signal scoring.
* **3 Broadcast Layout Variants per Clip**:
  1. **Reaction Stacked**: Streamer facecam on top, reframed gameplay on bottom, bouncing kinetic captions across divider.
  2. **Reference Letterbox / Blur-Fill**: 16:9 stream centered with dynamic blurred background and bold 3-line top hook headline.
  3. **Content Deframed 9:16**: Full-bleed vertical action crop focused on gameplay.
* **Kinetic Word-by-Word Subtitles**: High-impact ASS subtitles with color pops and auto-emojis (💀, 😭, 😱, 🔥, 🏆, 🚗).
* **ClipForge Web Studio**: Built-in zero-build interactive web app (`clipforge serve --port 8765`) with live pipeline visualization, deep linking, 9:16 vertical crop adjustments, and one-click publishing.
* **Full AI Agent Context**: Pre-configured [.agents](file:///.agents) / [AGENTS.md](file:///AGENTS.md) guide so any AI assistant (Cursor, Windsurf, Claude Code, Antigravity) can maintain, run, and extend all tools.

---

## 🚀 Quickstart

### 1. Prerequisites

* **Python**: 3.10 to 3.12
* **FFmpeg**: Full build with `libass` and hardware encoders
  * macOS: `brew install ffmpeg`
  * Windows: `winget install Gyan.FFmpeg`
* **yt-dlp**:
  * macOS: `brew install yt-dlp`
  * Windows: `winget install yt-dlp` or `pip install yt-dlp`

### 2. Setup

```bash
# Clone the repository
git clone <repo-url>
cd "Clipping GTA 6"

# Create and activate virtual environment
# macOS / Linux:
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell):
# python -m venv .venv
# .\.venv\Scripts\Activate.ps1

# Install ClipForge in editable mode
pip install -e .
```

### 3. Verify Environment

```bash
# Universal environment & hardware check (macOS & Windows)
clipforge check

# Or use platform scripts:
# macOS: ./check_env.sh
# Windows: .\check_env.bat  or  .\check_env.ps1
```

---

## 🖥️ ClipForge Studio (Web UI)

Launch the interactive studio dashboard in your browser:

```bash
clipforge serve --port 8765
```

Navigate to `http://localhost:8765`:
1. **Ingest**: Paste a YouTube URL or drop a local MP4 file, choose your clipping preset and target clips (`Auto`, `3`, `5`, `10`).
2. **Live Pipeline**: Real-time SSE monitor showing stage progress, active models, VOD scan head, and audio/chat velocity graphs.
3. **Studio**: Preview all rendered variants, fine-tune vertical 9:16 crops with instant live preview, adjust facecam positioning, and export approved clips.

---

## 💻 CLI Usage

You can also run everything headlessly from the command line:

```bash
# 1. Full Autonomous Run
clipforge run "https://youtu.be/E07OTlhncP0" --target auto --style bold-yellow --profile ishowspeed

# 2. Granular Stage Execution
clipforge ingest "https://youtu.be/E07OTlhncP0"
clipforge transcribe cache/yt_E07OTlhncP0
clipforge signals cache/yt_E07OTlhncP0
clipforge candidates cache/yt_E07OTlhncP0
clipforge select cache/yt_E07OTlhncP0 --target auto
clipforge render cache/yt_E07OTlhncP0 --style bold-yellow
clipforge export cache/yt_E07OTlhncP0
```

---

## 📁 Publishing Output (`ready_to_post/`)

When clips are exported, ClipForge organizes everything in `ready_to_post/<video_id>/`:
* Final `.mp4` video files organized with viral titles and variant suffixes.
* `copy_paste_pack.txt`: Ready-to-use YouTube Shorts / TikTok titles, descriptions, hashtags, and viral hooks.
* `schedule.csv`: Structured publishing schedule spreadsheet for social media planners.

---

## 🤖 Context for AI Coding Assistants

If you or your collaborators are working with an AI assistant (Cursor, Claude Code, Windsurf, Antigravity, Copilot), the project includes:
* **[.agents](file:///.agents)** / **[AGENTS.md](file:///AGENTS.md)**: Exhaustive context covering the 8-stage architecture, data contracts, REST/SSE APIs, Windows vs macOS hardware encoders, path escaping, and troubleshooting recipes.

---

## 🔒 Git Hygiene

* Video files (`.mp4`, `.wav`), intermediate caches (`cache/`), and exports (`ready_to_post/`) are ignored by `.gitignore` to keep the repository lightweight.
* Never commit `.env` or personal API keys.

---

## 🧪 Testing

Run automated tests:

```bash
pytest tests/
```
