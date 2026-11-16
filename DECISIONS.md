# Architecture & Implementation Decisions

## Decision 1: Selector Default Mode and LLM Decoupling
- **Context:** User does not have an active LLM API key to supply initially.
- **Decision:** Default `CLIPFORGE_DEFAULT_SELECTOR` to `file`. In this mode, ClipForge computes all candidates, audio loudness spikes, speech/pause density, chat velocity, and scene cuts locally. It outputs prompt files (`prompts/select_<chunk>.md`) alongside a JSON schema, and also supports fallback heuristic ranking so processing and clip rendering can run even before an LLM API key is attached.
- **Date:** 2026-11-16

## Decision 2: Apple Silicon Metal Acceleration Stack
- **Context:** Running on Apple Silicon (M3 Pro) with 18 GB unified memory.
- **Decision:**
  - Transcription: `mlx-whisper` (or `whisper.cpp` via Metal) utilizing the unified memory and Apple Neural Engine.
  - Video Encoding: `h264_videotoolbox` for accelerated draft renders and preview generation.
  - Video Subtitles: `ffmpeg-full` linked with `libass` and `subtitles` filter support.
- **Date:** 2026-11-16
