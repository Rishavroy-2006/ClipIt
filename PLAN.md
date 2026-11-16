# ClipForge: Build Plan for Claude Code

Working name: **ClipForge** (rename freely). Put this file in an empty project folder and start with: "Read PLAN.md fully, then begin with Milestone 0."

---

## 0. How to use this document (read first, Claude Code)

- You are the lead engineer. This is a brief written by another AI from limited information (a landing page, a 10-minute free trial, and a pricing screen). It is **not** a spec to follow blindly.
- **Improve on it.** If you see a better architecture, library, prompt, or workflow, use it. Record every meaningful deviation in `DECISIONS.md` with a one-paragraph reason.
- Facts in this plan (library names, model IDs, flags, prices) may be out of date. Verify against current docs before relying on them. Check Anthropic's docs for current model IDs.
- Work milestone by milestone (section 10). After each: run the acceptance checks, commit, and update `README.md`.
- Ask the user a question only when truly blocked. Otherwise state your assumption and continue.
- Keep a running `LOG.md` of: what you ran, timings, token counts, and problems found.
- Do not commit secrets. API keys live in `.env` (git-ignored).

---

## 1. Goal and context

**User:** a solo creator on a MacBook (Apple M3 Pro, 18 GB RAM, macOS). They want to start a YouTube Shorts channel clipping gaming streamers, starting around the **GTA 6 launch on 19 Nov 2026**. Speed matters: clips posted in the first days of launch are worth the most.

**Problem:** a SaaS called ScaleReach (AI video clip generator) does this, but its Pro plan has a quota (about 400 source-minutes/month, 3-hour max file). Full streams run 3-6 hours, so the quota burns fast.

**Goal:** a local tool that takes a video (YouTube link or local file) and produces ready-to-post vertical clips with word-by-word captions, a virality score, a title, and the reason each clip works, with minimal manual work and **no minute quota**. Only Claude usage is a cost.

**Success looks like:**
1. One command turns a 1-hour stream into 8-15 good 20-60 second 9:16 clips plus a review page.
2. Clip quality is equal to or better than ScaleReach on the same source (section 11).
3. Processing is fast enough to use during launch week (targets in section 11).
4. Selection works for gaming streams, including silent, action-heavy moments, not only talky ones.

---

## 2. What the reference product does (inferred)

ScaleReach pipeline, inferred from its page, FAQ and a trial run:

1. **Ingest:** paste a YouTube link or upload a file. The video is stored (24 h on free, 6 months on paid). 1 source minute processed = 1 minute deducted. Regenerating clips costs extra minutes. Max file length 3 hours.
2. **Transcribe:** speech-to-text with word-level timestamps (needed for word-by-word captions). Raw ASR errors are visible (e.g. "202 six", "Chad" for "chat"), so there's no cleanup pass.
3. **Pick clips:** an LLM reads the transcript and returns per clip: title, start/end, score 0-100, hook tags, and a "why this goes viral" blurb. Trial clips ran 39-48 seconds.
4. **Render:** cut, crop to 9:16 with auto-zoom/reframe, animated captions, backgrounds (blur, mirror, zoom, gradient, split-screen with Minecraft/Subway Surfers style footage), hook text overlay, optional AI music. Resolution gated (720p free, 1080p paid) and watermark on free.
5. **Deliver:** library, favorites, brand templates, batch export, social scheduler, API.

---

## 3. Weaknesses observed (beat these)

From one trial on a 10-minute clip of an iShowSpeed stream reacting to the GTA 6 trailer (3 clips returned):

| Observation | What we should do |
|---|---|
| All three picks are spoken reactions; selection is transcript-only | Add audio-energy, chat-activity and optional frame analysis so silent action is found |
| "Why this goes viral" was empty ("No auto-description available") | Always produce a specific reason, enforced by schema validation |
| Hook tags were generic ("Shocking statistic" for a delay reaction) | Gaming-aware tag vocabulary; the model must justify each tag |
| Clip 2 repeats the same sentence 3-4 times | Repetition detection and tightening (jump cuts or choose a better window) |
| Words missing in transcripts ("What the...", "Sh.") | Keep profanity in the transcript; censor only optionally in captions |
| ASR errors on proper nouns | Whisper prompt glossary plus a cheap LLM correction pass on selected clips only |
| 22% of source became clips; no way to ask for more or fewer | `--target-clips`, `--min-score`, configurable length range |
| Regenerating costs minutes | Cache every stage; re-running selection or rendering costs nothing local |

---

## 4. Environment and constraints

- **Hardware:** Apple M3 Pro, 18 GB unified memory, no CUDA. Use Metal-accelerated tools. Limit concurrent heavy jobs to 2.
- **Disk:** a 1-hour 1080p VOD is roughly 3-6 GB. Check free space before downloading; add a `clean` command.
- **Check, don't assume:** inspect what's installed (Homebrew, Python version, ffmpeg, yt-dlp). Prefer `uv` for Python. Verify ffmpeg has `libass` (`ffmpeg -filters | grep ass`) and `h264_videotoolbox`.
- **Suggested stack (verify and improve):**
  - `yt-dlp`, `ffmpeg`
  - Speech-to-text: `mlx-whisper` or `whisper.cpp` (Metal), model `large-v3-turbo` or similar; benchmark both on a real file and pick
  - `pydantic` (schemas), `typer` + `rich` (CLI), `anthropic` (LLM), `numpy`
  - Face/facecam detection: OpenCV (YuNet) or MediaPipe
  - `pysubs2` for ASS subtitle files, `jinja2` for the review page
  - Optional: `PySceneDetect`, `librosa`
- **Claude API vs Claude Code subscription:** the user has (or will have) a Claude Pro subscription for Claude Code. A script calling the Anthropic **API** is billed separately per token and needs an API key. Therefore build the selector with two modes (section 6.5): `api` (automatic) and `file` (the script writes prompts to disk; Claude Code, running on the subscription, reads them and writes the JSON result). Default to whichever the user has available; ask once.
- **Rights and policy (must respect):**
  - Only process content the user has the right or permission to clip. Streamers' clip policies vary. Add a `sources.yaml` field per source: `permission: owner|granted|clip-policy|unknown`, and warn when `unknown`.
  - Never build DRM circumvention or paywall bypass.
  - YouTube demonetizes channels that only repost others' footage ("reused content"). Support adding original value: commentary slot, titles, edits. Mention this in the README.
  - No copyrighted music. The "AI music" feature, if built, uses only user-supplied or royalty-free tracks.
  - Flag clips with heavy profanity or hate speech in `risk_flags`; do not silently pick them as "top".
  - Background footage for split-screen layouts is user-supplied only.

---

## 5. Architecture

```
 URL / file
     |
 [1 ingest] -> source.mp4 (+ chat replay if available) -> cache
     |
 [2 transcribe] -> transcript.json (words + timestamps)
     |
 [3 signals] -> signals.json (loudness, chat rate, scene cuts, speech rate)
     |
 [4 candidates] -> candidates.json (top windows by combined signal)
     |
 [5 select] -> clips.json (LLM scoring; api or file mode)
     |
 [6 refine] -> clips.json (snap boundaries, dedupe, de-repeat, tighten)
     |
 [7 layout+captions] -> per-clip .ass + layout plan
     |
 [8 render] -> out/<video_id>/clip_01.mp4 ...
     |
 [9 review.html] -> approve / reject / edit titles -> export.json
```

Every stage reads and writes JSON on disk under `cache/<video_id>/`, is idempotent, and skips work if its inputs are unchanged (hash-based). Any stage can be re-run alone: `clipforge run --from select`.

---

## 6. Stage specifications

### 6.1 Ingest
- Accept YouTube URL, other yt-dlp-supported URL, or a local file.
- Download best video up to 1080p plus audio; also extract a 16 kHz mono WAV for ASR.
- If chat replay exists (YouTube live chat via yt-dlp subtitle options; Twitch via a dedicated chat-download tool), fetch it into `chat.json` as `[{t, text, user?}]`. Treat as optional.
- Support `--start/--end` to process only part of a long video (cheap way to focus on the good hours).
- Reject nothing based on length; the 3-hour limit was the SaaS's, not ours. Process in chunks if memory requires.

### 6.2 Transcribe
- Word-level timestamps, VAD to avoid hallucinated text in silence, language auto-detect with override.
- Seed the model with a **glossary prompt** per source/profile: game title, character names, streamer name, slang (e.g. "GTA 6", "Rockstar", "Vice City", "chat").
- Detect and drop repetition-loop hallucinations (same n-gram repeated many times with no audio energy).
- Keep profanity in the transcript. Provide `censor_captions: true|false` as a render option.
- Output: `{segments:[{start,end,text,words:[{w,start,end,prob}]}]}`.
- Benchmark and log real-time factor on this Mac.

### 6.3 Signals (non-speech cues, the main upgrade)
Compute per 1-second bin (store compactly):
- **Loudness:** short-term loudness (ffmpeg `ebur128` or numpy RMS) and its z-score vs the local 5-minute window. Screams, laughter, and explosions show as spikes.
- **Speech rate and pauses:** words/sec from the transcript; long silence followed by burst.
- **Chat rate:** messages/sec vs rolling baseline (if chat exists). Often the best highlight indicator.
- **Scene cuts / motion:** optional, via frame difference or PySceneDetect.
- **Keyword hits:** configurable list (e.g. "oh my god", "no way", "wait", "what", laugh tokens).
Combine into a `highlight_score(t)` (weighted, normalized, weights in config). Weights are tunable; do not hard-code.

### 6.4 Candidate generation
- Find peaks in `highlight_score`, expand each into a window of roughly 15-90 seconds (extend until the topic or energy settles), merge overlaps.
- Keep the top `3 x target_clips` windows by signal, **plus** whatever the LLM global pass proposes (so talky-but-quiet stories are not lost).
- Each candidate carries its transcript slice, signal summary, and neighboring context (30 s before/after).

### 6.5 LLM selection
Two modes behind one interface (`Selector`):
- `api`: call the Anthropic Messages API (use a Sonnet-class model; verify the current model ID, e.g. `claude-sonnet-5-5`). Validate output with pydantic; on schema failure, retry once with the error message.
- `file`: write `prompts/select_<chunk>.md` and `prompts/schema.json`; wait for `results/select_<chunk>.json` to appear (Claude Code or the user fills it); validate the same way. This makes the whole thing work on a subscription with no API key.

Two passes (improve if you find better):
1. **Recall pass:** transcript chunk(s) with inline markers (e.g. `[LOUD +3.1σ]`, `[CHAT x8]`, `[SILENCE 4s]`) plus candidate windows. Ask for up to N candidate clips.
2. **Rerank pass:** only the shortlisted clips with full text and signals. Ask for final scores, titles, hooks, risk flags, and trimming advice.

Long videos: chunk by ~20-30 minutes with overlap; run chunks independently, then merge in a global rerank. Log tokens per call and total per hour of source.

Optional **vision check** for top silent/action candidates: sample 4-6 frames per window and ask the model what is happening on screen; use only on the top few windows to control cost. Measure tokens before enabling by default.

The full prompt is in Appendix A.

### 6.6 Boundary refinement and cleanup
- Snap `start` to the beginning of a sentence/word (or 0.3 s before the first word) and `end` to 0.4-0.6 s after the last word; prefer ending on the punchline or a natural pause.
- Enforce length: min 15 s, max 60 s (configurable), sweet spot 25-45 s.
- **Repetition check:** compute repeated n-grams within the clip. If a sentence repeats 3+ times, either choose a better window or run `tighten` (jump-cut to the first or best repeat).
- **Tighten mode (optional):** remove internal silences over ~1.2 s with 80-120 ms padding. Keep audio continuous (crossfade 20-30 ms) to avoid clicks.
- Dedupe: if two clips overlap by more than 30%, keep the higher score.
- Sanity check A/V sync after cutting.

### 6.7 Layouts and reframing
Layouts (selectable per clip, default chosen by analysis):
- `blur-fill`: original centered, blurred and scaled copy as background (safe default, always works).
- `stack`: gameplay on top, facecam on bottom (when a facecam is detected).
- `face-zoom`: crop and zoom on the face/facecam, smoothed.
- `split`: streamer clip on top, **user-supplied** background footage looping on the bottom.
Facecam detection: sample frames across the video, run face detection, find a stable rectangle where a face persists, expand to a sensible crop. Cache per source/streamer in `profiles/<name>.yaml`. Allow manual override of the rectangle.
Reframe smoothing: if tracking a moving subject, smooth the crop path (e.g. exponential smoothing or a Kalman filter) to avoid jitter; limit zoom speed.
Safe zones: keep captions and overlays out of the bottom ~20% and right ~15% (where Shorts UI sits).

### 6.8 Captions
- Generate `.ass` via `pysubs2`: 2-4 words per line, active word highlighted, short "pop" scale animation on word change. Burn in with ffmpeg `ass` filter.
- Style presets in `styles/*.yaml`: font (bundle an OFL-licensed bold font and check its license), size, outline, shadow, colors, highlight color, position.
- Respect `censor_captions`. Optionally correct proper nouns using the glossary and the LLM correction pass (selected clips only).
- Never let captions drift: use word timestamps from the final trimmed timeline (after tighten, remap times).

### 6.9 Hook overlay, titles, metadata
- Hook overlay: one short line (max ~8 words) shown for the first 2-3 seconds, from the clip's `hook_line`. Optional per config.
- Output per clip: `title`, `description`, `hashtags` (including game/event tags), `hook_line`, `why_viral`, `risk_flags`.
- Titles: write 3 variants; mark one as recommended. No clickbait that misrepresents the clip.

### 6.10 Render
- Output 1080x1920, same fps as source up to 60, H.264 High, AAC 192k, `-movflags +faststart`.
- Draft quality: `h264_videotoolbox` (fast preview). Final: `libx264 -crf 18 -preset medium` (compare speed/quality on this Mac and decide).
- Render clips in parallel up to 2 jobs; show progress.
- Audio: loudness-normalize clips (about -14 LUFS) with `loudnorm`.
- Name: `out/<video_id>/NN_<slug>.mp4` plus sidecar `NN_<slug>.json`.

### 6.11 Review page
- Generate a static `review.html` (no server needed) with a video player per clip, score, title, reason, risk flags, and approve/reject/edit controls; export `approved.json`. Keyboard shortcuts for speed (arrow keys, A/R).
- This is where the human spends their few minutes; make it fast.

### 6.12 Optional later
- Upload to YouTube via the official Data API (OAuth), scheduling. Low priority; manual upload is acceptable.
- Watch-folder mode: new VODs dropped in a folder get processed automatically.

---

## 7. Data contracts (pydantic models; suggested shape)

```json
// clips.json item
{
  "id": "c07",
  "start": 1932.4,
  "end": 1974.9,
  "score": 92,
  "title": "Speed Finds His Name in GTA 6",
  "title_alts": ["...", "..."],
  "hook_line": "He found his own name in the game",
  "hooks": ["unexpected reveal", "explosive reaction"],
  "why_viral": "Specific one-or-two-sentence reason, not generic",
  "category": "reaction|funny|fail|clutch|story|rage|wholesome|news",
  "signals": {"loudness_z": 3.2, "chat_rate_x": 6.1, "speech": true},
  "risk_flags": ["profanity:high"],
  "trim_notes": "Start on 'wait, wait'; cut repeated line; end on punchline",
  "layout": "stack",
  "source_permission": "clip-policy"
}
```

Use strict validation: `why_viral` must be at least 20 words minimum 1 sentence; `score` 0-100; `end-start` within configured bounds; timestamps within the video.

---

## 8. CLI and config

```
clipforge run <url|file> [--target-clips 10] [--min-score 70] [--layout auto]
                         [--selector api|file] [--from <stage>] [--start HH:MM:SS --end HH:MM:SS]
                         [--style bold-yellow] [--censor-captions] [--profile <streamer>]
clipforge review <video_id>      # build/open review.html
clipforge clean  <video_id>      # delete cached video, keep JSON
clipforge cost   <video_id>      # token and time report
```
`config.yaml` holds defaults: length bounds, signal weights, models, concurrency, style, safe zones, paths. Everything overridable by CLI flags.

---

## 9. Suggested repo layout

```
clipforge/
  pyproject.toml  README.md  PLAN.md  DECISIONS.md  LOG.md  .env.example  config.yaml
  src/clipforge/
    cli.py  config.py  cache.py  models.py
    stages/ ingest.py transcribe.py signals.py candidates.py select.py refine.py layout.py captions.py render.py review.py
    llm/ selector_api.py selector_file.py prompts.py
  prompts/  styles/  profiles/  assets/fonts/
  tests/  (unit tests for refine, captions timing, schema validation; small fixture video)
```

---

## 10. Milestones and acceptance criteria

**Launch-week fast path = M0 to M3.** The user needs usable clips within the first session or two. Get a working end-to-end version first, then improve.

- **M0 Environment:** verify tools, create venv, `clipforge --help` works, ffmpeg has libass and videotoolbox. *Accept:* a script prints versions and free disk space.
- **M1 Ingest + transcribe:** download or read a file, produce `transcript.json` with word timestamps. Benchmark speed. *Accept:* 10-minute sample transcribes; timing logged; glossary effect checked.
- **M2 Select (file and api modes):** prompts, schema validation, merge of chunks. *Accept:* 10-minute sample yields clips with specific `why_viral`, valid JSON, no overlaps.
- **M3 Render MVP:** cut, `blur-fill` layout, word-by-word captions, 1080x1920 output, `review.html`. *Accept:* 3 clips play correctly, captions in sync, audio normalized, no black bars or clicks. **This is the launch-week tool.**
- **M4 Gaming layouts:** facecam detection, `stack`, `face-zoom`, `split` with user background. *Accept:* correct facecam rectangle found on a sample stream; fallback works.
- **M5 Signals:** loudness, chat, keyword scoring, candidate windows. *Accept:* on a stream with known highlights, signal-only top windows include them; a silent-action clip is found that transcript-only selection missed.
- **M6 Polish and quality:** repetition fixes, tighten mode, hook overlay, title variants, risk flags, LLM glossary correction. *Accept:* the repeat-sentence problem is fixed on the test case; captions match audio.
- **M7 Evaluation and tuning:** run the comparison in section 11, tune weights and prompt. *Accept:* report committed as `EVAL.md`.
- **M8 Optional:** upload/scheduler, watch folder.

---

## 11. Evaluation

**Golden test:** the user has a 10-minute segment of an iShowSpeed GTA 6 trailer-reaction stream that ScaleReach processed. It returned three clips: (1) he finds his own name in the game (about 39 s, score 96), (2) he loses it over the 2026 delay (about 43 s, 93), (3) he calls his dad about the news (about 48 s, 88). Ask the user for that same source file or link. Then check:
- Do we find those three moments? Do we find additional ones that are better?
- Blind rating: user rates our clips and ScaleReach's from 1-5 without labels.
- Caption accuracy, sync, no repeated-line problem.

**Performance targets (log actuals; adjust if unrealistic):**
- Transcription real-time factor: aim for roughly 5-10x faster than real time.
- Render: under about 1 minute per 45-second clip in draft mode.
- End-to-end for a 1-hour stream: aim for under about 30 minutes unattended.
- Memory: stays within 18 GB with concurrency 2.

**Cost:** log tokens per stage and per hour of source. Earlier rough estimates (verify by measurement): about 20k-35k tokens per hour for transcript-only selection; vision frames add roughly hundreds of tokens each, so use sparingly.

---

## 12. Token and usage budget

- Building this project consumes the user's Claude Code allowance (shared with chat; five-hour and weekly limits). Check `/usage` regularly; keep tool output short, avoid dumping big files into context, and work in small, testable steps.
- Running the tool: `file` mode uses subscription usage; `api` mode bills per token. Make `clipforge cost` accurate so the user can decide.
- Cache aggressively so re-selection never repeats the transcription or download.

---

## 13. Risks and open questions

- `yt-dlp` can break when sites change; pin a version and make updating easy. Downloading may violate a platform's terms; the user is responsible for having rights to the source.
- Twitch VOD/chat access may need extra tooling; treat chat as optional.
- Whisper can hallucinate in silence or music; VAD and loop-detection are required.
- Face detection may fail on stylized or tiny facecams; always keep a manual rectangle override.
- ASS rendering differs across ffmpeg builds; test fonts and animations on this machine.
- 18 GB RAM: large models plus parallel renders can cause memory pressure; benchmark and set limits.
- **Ask the user once:** (1) do they have an Anthropic API key, or should `file` mode be the default? (2) which streamers and what language? (3) what permission do they have to clip them? (4) which platforms to post on (YouTube Shorts only, or also TikTok/Reels)?

---

## 14. Ideas beyond this plan (use your judgment)

- **Performance feedback loop:** record which posted clips did well and feed the best as few-shot examples into the selection prompt.
- **Trend tags:** attach event-specific tags and titles for the GTA 6 launch window.
- **Hook A/B variants:** render two hook lines and let the user pick.
- **Translated captions** for other languages.
- **Auto thumbnails** (best frame plus text).
- **Per-streamer profiles:** learned facecam position, glossary, preferred style, scoring tweaks.
- **Smarter cut points:** use audio features to end exactly on laughter or reaction peaks.
- **Local tiny model for pre-filtering** to cut token use.
Anything better you find: do it, and note it in `DECISIONS.md`.

---

## Appendix A: Selection prompt (starting point; improve it)

**System:**
You are an expert editor of short-form gaming and streaming clips for YouTube Shorts, TikTok and Reels. You receive a timestamped transcript of a stream or video with inline signal markers, and candidate windows. Pick the moments most likely to perform as standalone short clips.

**Rules:**
1. Clips must be 15-60 seconds, ideally 25-45, and make sense without the rest of the stream.
2. The first 2 seconds must hook (a reaction, a claim, a question, a surprise). Prefer clips with a clear payoff or punchline.
3. Use the markers: `[LOUD]`, `[CHAT xN]`, `[SILENCE]`, `[KEYWORD]`. Strong non-speech moments (big reactions, fails, clutch plays) matter even when the transcript is short.
4. Avoid clips where a sentence is repeated 3 or more times unless the repetition itself is the joke; if so, say so and suggest the trim.
5. Do not return overlapping clips. Prefer variety of type.
6. Be specific in `why_viral` (what happens, why viewers will react or share). Generic phrases like "engaging content" are not allowed.
7. Choose hook tags only from the allowed list and only if they truly apply: `explosive reaction`, `unexpected reveal`, `funny fail`, `clutch moment`, `rage`, `wholesome moment`, `hot take`, `storytime`, `relatable pain`, `news reaction`, `quotable line`.
8. Set `risk_flags` for heavy profanity (`profanity:high`), slurs or hate (`hate:possible`), copyrighted music audible (`music:possible`), or personal information (`pii:possible`).
9. Give exact `start` and `end` in seconds from the transcript timestamps, aligned to sentence or word boundaries, plus `trim_notes`.
10. Score 0-100 calibrated so that 90+ is rare. Explain scoring in `why_viral`.

**User:** includes `{video_title}`, `{streamer}`, `{game_or_event}`, `{glossary}`, `{target_clips}`, `{chunk_transcript_with_markers}`, `{candidate_windows}`.

**Output:** a JSON array only, matching the schema in section 7 (no prose, no markdown fences).

**Rerank pass:** give the shortlisted clips with full text and signals. Ask for: final score, 3 title variants (one recommended), hook line (max 8 words), hashtags, risk flags, and the final trim advice. Penalize near-duplicates and anything that needs extra context to understand.

## Appendix B: Caption style preset example

```yaml
name: bold-yellow
font: <bundled OFL font>
size: 84
primary_color: "#FFFFFF"
highlight_color: "#FFD60A"
outline: 6
shadow: 2
words_per_line: 3
position: {x: center, y: 0.62}   # fraction of height; keep out of the bottom 20%
animation: pop                    # ~120 ms scale-in on each new word
uppercase: true
```
