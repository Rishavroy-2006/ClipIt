# ClipForge Execution & Benchmark Log

## System Specs
- Hardware: Apple Silicon ARM64 (macOS)
- Memory: 18 GB Unified Memory
- Accelerators: Apple Silicon Neural Engine / Metal GPU, `h264_videotoolbox`
- Media Tools: FFmpeg-full (with `libass` & `videotoolbox`), yt-dlp, uv

## Milestone Progress Log

| Milestone | Status | Description | Notes |
|---|---|---|---|
| M0 | In Progress | Environment setup, CLI scaffolding, verification script | FFmpeg-full + libass + VideoToolbox installed & verified |
| M1 | Planned | Ingest + MLX Whisper transcription | Local Metal accelerated whisper |
| M2 | Planned | Candidate generation & Selection (file + api) | Offline file mode & heuristic ranking |
| M3 | Planned | MVP Render (blur-fill, kinetic captions, review.html) | Launch-ready vertical pipeline |
| M4 | Planned | Gaming Reframing & Facecam Detection | OpenCV face detection & stack layout |
| M5 | Planned | Advanced Signals (loudness, chat, cuts) | Multi-signal peak fusion |
| M6 | Planned | Polish, Deduplication, Tighten & De-repeat | Silence jump cuts, subtitle styling |
| M7 | Planned | Evaluation against reference stream | Quality comparison & tuning |
