# Debug Session: Audio/Video Desynchronization & Part 4 Rejected Clips Resurrection

## Frontmatter
- **Status**: resolved
- **Trigger**: "I am noticing the audio and video dsync again. Fix it along with it. I am seeing new clips for part four, like the ones which I rejected are coming back. Check why this is happening, like conduct a full audit of what is wrong and and fix them systematically /gsd-debug"
- **Created**: 2026-11-19T02:28:14+05:30
- **Updated**: 2026-11-19T02:40:00+05:30

---

## 1. Symptoms & Incident Report

1. **Symptom 1 (A/V Desync)**:
   - **Expected**: Audio and video in all vertical rendered layouts (Reaction Stacked, Blur-Fill, Reframed) must remain in 100% microsecond frame-accurate sync with both spoken speech and Ass subtitle overlays.
   - **Actual**: Audio and video drifted out of sync by approximately 0.75 to 1.65 seconds depending on clip location, with video leading the audio.
   - **Trigger / Timeline**: Regressed when fine-seek output trimming (`-ss {fine_seek}`) was removed in favor of in-filtergraph trimming (`trim` / `atrim`) in commit `dac1795`.

2. **Symptom 2 (Part 4 Rejected Clips Coming Back)**:
   - **Expected**: Clips rejected by the user and permanently deleted from disk must not reappear in Studio Step 3.
   - **Actual**: All 8 clips from Part 4 reappeared in the Studio UI clip list as unrendered / pending clips.
   - **Trigger / Timeline**: Occurred after the previous session executed a script in step 8915 that re-populated `live_session.json` from `cache/yt_qSyo5d-dVzA_pt04/*.json`.

---

## 2. Root Cause Analysis (Scientific Method)

### Issue A: Audio/Video Desynchronization
- **Hypothesis Tested**:
  Does input seeking (`-ss {fast_seek}`) before `-i` without `-copyts` cause video and audio decoders to output different starting presentation timestamps (PTS)?
- **Experimental Verification**:
  Using `ffprobe` and `ffmpeg` with `showinfo` / `ashowinfo` on `cache/yt_qSyo5d-dVzA_pt06/source.mp4`:
  ```bash
  ffmpeg -ss 42.9 -i source.mp4 -filter_complex "[0:v]showinfo[v];[0:a]ashowinfo[a]" ...
  ```
  **Result**:
  - Video initial PTS delivered by decoder: `pts_time: 0.75s` (due to keyframe interval and initial stream delay)
  - Audio initial PTS delivered by decoder: `pts_time: 0.00s`
  When `trim=start=5.000` was applied to video, it dropped video frames from `0.75s` to `5.000s` (only 4.25 seconds of video dropped!).
  When `atrim=start=5.000` was applied to audio, it dropped audio samples from `0.00s` to `5.000s` (5.00 seconds of audio dropped!).
  **Conclusion**: Video had 0.75s LESS media trimmed than audio, causing video to lead audio by exactly 0.75s! In multi-part downloads where container video `start_time` was 3.05s and audio was 1.40s, this skew was up to 1.65 seconds.

### Issue B: Part 4 Rejected Clips Resurrection
- **Hypothesis Tested**:
  Were Part 4 clips revived because `live_session.json` had their entries re-injected, and did candidate JSONs linger in `cache/yt_qSyo5d-dVzA_pt04/`?
- **Experimental Verification**:
  - `out/yt_qSyo5d-dVzA/part_04` had NO MP4 files (they were successfully deleted previously).
  - But `live_session.json` in both `out/` and `cache/` had `parts[3].clips` filled with 8 entries.
  - Candidate sidecars `cache/yt_qSyo5d-dVzA_pt04/pt04_c*.json` had not been deleted by `delete_single_clip` (only `.ass` and `.mp4` were targeted).
  **Conclusion**: Step 8915 re-populated `live_session.json` with Part 4 clips, causing `handle_get_project_detail` to load them as ghost clips.

---

## 3. Systematic Fix & Remediation

### Fix 1: Microsecond Lockstep A/V Sync via `-copyts`
In `src/clipit/stages/render.py`:
1. Added `-copyts` immediately after `-ss {fast_seek}` in FFmpeg input options.
2. Trimming inside `filter_complex` and `-af` now operates on absolute container timestamps:
   ```python
   filter_complex = (
       f"{layout_filter};"
       f"[base]trim=start={clip.start:.3f}:duration={clip_dur:.3f},setpts=PTS-STARTPTS,"
       f"ass=filename='{escaped_ass}':fontsdir='{escaped_fonts}',setsar=1,setpts=PTS-STARTPTS[v]"
   )
   audio_filter = (
       f"atrim=start={clip.start:.3f}:duration={clip_dur:.3f},asetpts=PTS-STARTPTS,"
       f"aresample=async=1:first_pts=0,..."
   )
   ```
3. Because `-copyts` preserves original stream timestamps, both video and audio are sliced at the EXACT same millisecond (`clip.start`).
4. `setpts=PTS-STARTPTS` and `asetpts=PTS-STARTPTS` reset both timelines to `0.000` simultaneously, aligning 100% with the ASS subtitle events.

### Fix 2: Purge Part 4 and Harden Retention
1. Removed all Part 4 clips from `out/yt_qSyo5d-dVzA/live_session.json` and `cache/yt_qSyo5d-dVzA/live_session.json`.
2. Permanently deleted all stale `.json` and `.ass` files from `cache/yt_qSyo5d-dVzA_pt04/` and `out/yt_qSyo5d-dVzA/part_04/`.
3. Updated `src/clipit/retention.py` so `delete_single_clip` removes `{clip_id}.json` and `{stripped_cid}.json` from cache directories, preventing orphan candidate resurrections.

---

## 4. Verification

- All 15 active approved and Part 6 clips re-rendered with `-copyts`.
- `ffprobe` verified output durations: Video and Audio durations match within 0.02s (single frame boundary).
- Subtitle events match audio speech timestamps exactly from frame 0.000.
- Studio API (`GET /api/project/yt_qSyo5d-dVzA`) returns exactly 15 clips; zero Part 4 clips exist.
