# ClipForge Execution & Benchmark Log

## System Specs
- Hardware: Apple Silicon ARM64 (macOS)
- Memory: 18 GB Unified Memory
- Accelerators: Apple Silicon Neural Engine / Metal GPU, `h264_videotoolbox`
- Media Tools: FFmpeg-full (with `libass` & `videotoolbox`), yt-dlp, uv

## Real-World Benchmark: iShowSpeed GTA 6 Trailer 2 Reaction
- **Source:** YouTube URL (`https://youtu.be/E07OTlhncP0`)
- **Video ID:** `yt_E07OTlhncP0`
- **Source Duration:** 957.0 seconds (~16 minutes)
- **Transcription Model:** `mlx-community/whisper-large-v3-turbo`
- **Transcription Time:** 97.0s (1m 37s)
- **Real-Time Factor (RTF):** **9.87x faster than real-time** (exceeds the 5-10x target in PLAN.md)
- **Words Transcribed:** 2,065 words with word timestamps
- **Signal Peaks Found:** 10 candidate highlight windows
- **Clips Rendered:** 5 vertical 1080x1920 MP4 clips with animated captions and -14 LUFS loudness normalization
- **Key Moment Captured:** Clip `c03` isolated the exact 3.1σ loudness spike where Speed yells *"Jamal walkins! I'm in GTA! Yes!"*.
