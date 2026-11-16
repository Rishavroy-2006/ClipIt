# ClipForge Execution & Benchmark Log

## System Specs
- Hardware: Apple Silicon ARM64 (macOS)
- Memory: 18 GB Unified Memory
- Accelerators: Apple Silicon Neural Engine / Metal GPU, `h264_videotoolbox`
- Media Tools: FFmpeg-full (with `libass` & `videotoolbox`), yt-dlp, uv

## Milestone Progress Log

| Milestone | Status | Description | Notes |
|---|---|---|---|
| M0 | Completed | Environment setup, CLI scaffolding, verification script | FFmpeg-full + libass + VideoToolbox installed & verified (`./check_env.sh` passing) |
| M1 | Completed | Ingest + MLX Whisper transcription | Native Metal-accelerated `whisper-large-v3-turbo` model cached and functioning |
| M2 | Completed | Candidate generation & Selection (file + api) | Offline file mode & heuristic signal ranking operational without requiring API key |
| M3 | Completed | MVP Render (blur-fill, kinetic captions, review.html) | Vertical 1080x1920 MP4 rendering verified with hardware acceleration and ASS burn-in |
| M4 | Implemented | Gaming Layouts (blur-fill, stack, split) | FFmpeg filtergraphs configured |
| M5 | Implemented | Multimodal Signals (loudness, chat, speech, cuts) | RMS loudness z-score, word velocity, keyword scoring active |
| M6 | Implemented | Boundary Refinement, Deduplication, Timing Snap | Word snapping, length bounds, and ASS pop animations active |
| M7 | Ready | Evaluation against real streaming footage | Ready for user test clips or YouTube links |

## Test Verification
- Unit Tests: 3/3 passed (`pytest tests/`)
- End-to-End Render: Verified on `tests/fixtures/sample.mp4` -> `out/sample_a4160e9a/c01.mp4` (1080x1920 H.264 / AAC)
- Review UI: `out/sample_a4160e9a/review.html` generated with keyboard navigation and export.
