# ClipForge

**ClipForge** is a local, privacy-first AI video clipping tool tailored for Apple Silicon (M-series Macs). It takes long gaming streams and VODs (via YouTube URL or local file) and produces ready-to-publish vertical 9:16 clips for YouTube Shorts, Instagram Reels, and TikTok with zero minute quotas.

## Key Features
- **Zero Cloud Quotas:** Transcription runs 100% locally on Apple Silicon Metal via MLX Whisper (`large-v3-turbo`).
- **Multimodal Highlight Detection:** Audio loudness peaks (`ebur128` / RMS), chat velocity spikes, speech bursts, and scene cuts.
- **Intelligent Framing & Layouts:**
  - `blur-fill`: Original 16:9 gameplay centered with smoothed, blurred background.
  - `stack`: Automated facecam detection and cropping placed above or below gameplay.
  - `split`: Streamer gameplay on top, user-supplied background footage on bottom.
- **Animated Word-by-Word Captions:** Rendered with `.ass` typography (color pop highlights, customizable styles) and hardware-accelerated via `h264_videotoolbox`.
- **Decoupled Selection Engine:**
  - `file` mode: Completely offline / subscription mode. Writes structured Markdown prompts for review or processing without needing a direct API key.
  - `api` mode: Direct integration with Anthropic Claude, Google Gemini, or OpenAI API.
- **Static Review Dashboard:** Browser-based `review.html` for instant keyboard review (`A` to approve, `R` to reject).

## Quickstart

```bash
# Verify environment and system tools
./check_env.sh

# Run CLI help
clipforge --help

# Process a video or YouTube link
clipforge run "https://www.youtube.com/watch?v=..." --target-clips 5
```
