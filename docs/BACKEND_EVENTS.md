# ClipIt Backend Pipeline Events Specification (`metrics` extensions)

This document specifies the schema for optional structured metrics emitted in `agent_log` SSE events by ClipIt backend stages.

## SSE Event Flow
ClipIt UI listens on `GET /api/stream-workflow?session_id={id}` for Server-Sent Events (SSE):

1. `pipeline_start { "video_id": string, "url": string }`
2. `stage_start { "stage_id": string, "name": string, "stage_index": int, "badge": string }`
3. `agent_log { "stage_id": string, "log": string, "metrics": { ... } }`
4. `stage_complete { "stage_id": string, "progress_pct": int }`
5. `pipeline_complete { "message": string, "video_id": string }`
6. `pipeline_error { "error": string }`

---

## Stage Identifiers (`stage_id`)
The 8 canonical sequential agents in the pipeline:
1. `ingest` — Ingest Demuxer (yt-dlp + ffmpeg)
2. `whisper` — Whisper Audio Alignment (mlx-whisper / whisper-large-v3)
3. `signals` — Multi-Signal Intelligence (scipy + numpy spectral analysis)
4. `slicer` — Candidate Window Generator (Sliding window optimization)
5. `director` — Gemini AI Director (Gemini 2.5/3.0 virality scoring)
6. `reframer` — Vision Reframing & Facecam (OpenCV YuNet + Gemini Vision)
7. `renderer` — Hardware Motion Render (Apple VideoToolbox / NVENC)
8. `export` — Export Packager (Ready-to-Post artifacts & metadata)

---

## Optional `metrics` Schema

When emitting `agent_log`, include any of the following structured metrics in the `metrics` object. All fields are optional; the UI degrades gracefully if any are omitted.

```json
{
  "model": "whisper-large-v3",
  "device": "GPU",
  "task": "Transcribing audio segment",
  "item": 4,
  "of": 12,
  "vod_ts": [8073, 8110],
  "vod_duration": 14400,
  "db": -18.2,
  "chat_velocity": 74,
  "thumb_url": "/api/frame/...",
  "bbox": [820, 540, 320, 180],
  "fps": 58,
  "eta_s": 41,
  "words": [
    { "t": 8073.2, "w": "what", "conf": 0.98 },
    { "t": 8073.5, "w": "just", "conf": 0.95 },
    { "t": 8073.8, "w": "happened", "conf": 0.99 }
  ]
}
```

### Field Definitions

| Field | Type | Description | Primary Emitting Stage(s) |
|---|---|---|---|
| `model` | `string` | Model or engine identifier (e.g. `whisper-large-v3-turbo`, `gemini-2.5-flash`, `opencv-yunet`, `h264_videotoolbox`) | All |
| `device` | `string` | Execution device / hardware accelerator (e.g. `GPU`, `Apple Neural Engine`, `CPU`, `VideoToolbox`) | All |
| `task` | `string` | Specific task description (e.g. `Demuxing 1080p stream`, `Scoring candidate window`, `Encoding variant 1`) | All |
| `item` | `integer` | Current 1-based item index being processed | Slicer, Director, Reframer, Renderer |
| `of` | `integer` | Total items in the current stage's work queue | Slicer, Director, Reframer, Renderer |
| `vod_ts` | `[number, number]` | Current window `[start_seconds, end_seconds]` being analyzed | Slicer, Director, Reframer, Renderer |
| `vod_duration` | `number` | Total duration of the source VOD in seconds | Ingest, Whisper, Signals |
| `db` | `number` | Real-time audio energy measurement in decibels (e.g. `-14.5` dB) | Whisper, Signals |
| `chat_velocity` | `number` | Real-time chat messages / second or burst velocity | Signals |
| `thumb_url` | `string` | Frame image URL for visual reframing / facecam preview | Reframer, Director |
| `bbox` | `[number, number, number, number]` | Bounding box coordinates `[x, y, width, height]` (or normalized 0-1000) for tracked facecam / focal region | Reframer |
| `fps` | `number` | Instantaneous hardware render speed in frames/sec | Renderer |
| `eta_s` | `number` | Estimated seconds remaining for current clip or stage | Renderer |
| `words` | `Array<{t: number, w: string, conf: number}>` | Live transcribed words with relative timestamp `t` and confidence `0.0..1.0` | Whisper |

---

## Example Emitted Payloads by Stage

### 1. Ingest
```json
{
  "stage_id": "ingest",
  "log": "[INGEST] Downloading VOD metadata and 1080p60 stream...",
  "metrics": {
    "model": "yt-dlp",
    "device": "Network",
    "task": "Downloading source stream",
    "vod_duration": 10300,
    "res": "1920x1080@60"
  }
}
```

### 2. Whisper
```json
{
  "stage_id": "whisper",
  "log": "[WHISPER] Aligning timestamps at 01:24:12...",
  "metrics": {
    "model": "whisper-large-v3-turbo",
    "device": "GPU",
    "task": "Transcribing speech",
    "vod_ts": [5052, 5064],
    "vod_duration": 10300,
    "words": [
      { "t": 5052.1, "w": "BRO", "conf": 0.99 },
      { "t": 5052.4, "w": "OH", "conf": 0.98 },
      { "t": 5052.7, "w": "GOD", "conf": 0.97 }
    ]
  }
}
```

### 3. Signal Intelligence
```json
{
  "stage_id": "signals",
  "log": "[SIGNALS] Energy spike detected at 01:24:30 (db: -12.4, chat: 88 msgs/s)",
  "metrics": {
    "model": "scipy-spectral",
    "device": "CPU",
    "task": "Spectral audio & chat density analysis",
    "vod_ts": [5070, 5085],
    "vod_duration": 10300,
    "db": -12.4,
    "chat_velocity": 88
  }
}
```

### 4. Candidate Slicer
```json
{
  "stage_id": "slicer",
  "log": "[SLICER] Generated 14 candidate windows; merging overlapping bursts...",
  "metrics": {
    "model": "sliding-window-opt",
    "device": "CPU",
    "task": "Window optimization",
    "item": 5,
    "of": 14,
    "vod_ts": [5050, 5095],
    "vod_duration": 10300
  }
}
```

### 5. Gemini AI Director
```json
{
  "stage_id": "director",
  "log": "[DIRECTOR] Scoring candidate 5 of 12: 'Going over the GTA 6 launch plan again' (Score: 88)",
  "metrics": {
    "model": "gemini-2.5-flash",
    "device": "Vertex AI",
    "task": "Virality reasoning & hook generation",
    "item": 5,
    "of": 12,
    "vod_ts": [5050, 5095],
    "score": 88,
    "verdict": "keep"
  }
}
```

### 6. Vision & Facecam Reframer
```json
{
  "stage_id": "reframer",
  "log": "[REFRAMER] Facecam detected in Bottom-Left corner [x: 400, y: 0, w: 950, h: 280]",
  "metrics": {
    "model": "opencv-yunet",
    "device": "CPU",
    "task": "Facecam box tracking",
    "item": 5,
    "of": 12,
    "vod_ts": [5050, 5095],
    "bbox": [400, 0, 950, 280],
    "thumb_url": "/api/video/sample/vision_c07_mid.jpg"
  }
}
```

### 7. Hardware Motion Renderer
```json
{
  "stage_id": "renderer",
  "log": "[RENDER] Encoding clip 5/12 at 64 fps (ETA: 12s)...",
  "metrics": {
    "model": "h264_videotoolbox",
    "device": "Apple VideoToolbox",
    "task": "Encoding 3 vertical layouts",
    "item": 5,
    "of": 12,
    "vod_ts": [5050, 5095],
    "fps": 64,
    "eta_s": 12
  }
}
```

### 8. Export Packager
```json
{
  "stage_id": "export",
  "log": "[EXPORT] Ready-to-Post pack generated with copy_paste_pack.txt",
  "metrics": {
    "model": "packager",
    "device": "IO",
    "task": "Assembling export directory",
    "item": 12,
    "of": 12
  }
}
```
