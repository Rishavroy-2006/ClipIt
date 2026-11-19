"""Ground-truth clip audio alignment engine for frame-accurate subtitles.

Transcribes the rendered clip audio directly using local hardware Whisper
(Metal on macOS via mlx-whisper, CUDA/CPU on Windows via faster-whisper)
and anchors transcript words to exact audio presentation timestamps (PTS).
This eliminates container PTS drift, HLS discontinuity offsets, and speech-onset jitter.
"""

import os
import re
import shutil
import difflib
import statistics
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple

from clipit.models import FullTranscript, TranscriptSegment, WordTimestamp, ClipItem


def extract_clip_audio_wav(media_path: Path, out_wav: Path) -> Path:
    """Extract 16kHz mono PCM audio from a media file for Whisper transcription."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(media_path.resolve()),
        "-vn", "-ar", "16000", "-ac", "1",
        str(out_wav.resolve())
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    return out_wav


def _filter_whisper_repetition_loops(words: List[Tuple[str, str, float, float]]) -> List[Tuple[str, str, float, float]]:
    """Filter out degenerate Whisper hallucination loops (e.g. repeated token loops)."""
    if len(words) <= 3:
        return words
    filtered: List[Tuple[str, str, float, float]] = []
    consec_count = 1
    last_token = None
    for item in words:
        tok = item[0]
        if tok == last_token:
            consec_count += 1
        else:
            consec_count = 1
            last_token = tok
        # Cap any identical consecutive token to at most 2 occurrences
        if consec_count <= 2:
            filtered.append(item)
    return filtered


def transcribe_clip_audio(wav_path: Path) -> List[Tuple[str, str, float, float]]:
    """Transcribe clip audio directly to obtain ground-truth word timestamps.
    
    Returns list of tuples: (clean_token, raw_word, start_sec, end_sec).
    """
    audio_words: List[Tuple[str, str, float, float]] = []

    # 1. macOS Apple Silicon Metal acceleration
    try:
        import mlx_whisper
        res = mlx_whisper.transcribe(
            str(wav_path.resolve()),
            path_or_hf_repo="mlx-community/whisper-tiny",
            word_timestamps=True,
            condition_on_previous_text=False
        )
        for seg in res.get("segments", []):
            for w in seg.get("words", []):
                if float(w.get("probability", 1.0)) < 0.05:
                    continue
                raw = w.get("word", "").strip()
                clean = re.sub(r"[^A-Z0-9\']", "", raw.upper())
                if clean:
                    audio_words.append((clean, raw, float(w.get("start", 0.0)), float(w.get("end", 0.0))))
        if audio_words:
            return _filter_whisper_repetition_loops(audio_words)
    except Exception:
        pass

    # 2. Windows / Linux CUDA or CPU
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("tiny", device="auto", compute_type="int8")
        segments, _ = model.transcribe(
            str(wav_path.resolve()),
            word_timestamps=True,
            condition_on_previous_text=False
        )
        for seg in segments:
            for w in seg.words:
                if float(getattr(w, "probability", 1.0)) < 0.05:
                    continue
                raw = w.word.strip()
                clean = re.sub(r"[^A-Z0-9\']", "", raw.upper())
                if clean:
                    audio_words.append((clean, raw, float(w.start), float(w.end)))
        if audio_words:
            return _filter_whisper_repetition_loops(audio_words)
    except Exception:
        pass

    return audio_words


def align_transcript_to_clip_audio(
    media_path: Path,
    orig_transcript: Optional[FullTranscript] = None,
    clip_start: float = 0.0,
    clip_end: Optional[float] = None,
    clip_dur: Optional[float] = None,
) -> FullTranscript:
    """Align word timestamps directly to the actual audio of media_path.
    
    Produces a FullTranscript where every word's start and end timestamps are
    guaranteed to be relative to t=0.000s of the clip's audio stream.
    """
    if clip_dur is None:
        if clip_end is not None and clip_end > clip_start:
            clip_dur = clip_end - clip_start
        else:
            clip_dur = 30.0

    temp_wav = media_path.parent / f"_align_{media_path.stem}_{os.getpid()}.wav"
    try:
        if media_path.suffix.lower() == ".wav":
            wav_to_read = media_path
        else:
            wav_to_read = extract_clip_audio_wav(media_path, temp_wav)

        audio_words = transcribe_clip_audio(wav_to_read)
    finally:
        if temp_wav.exists():
            try:
                temp_wav.unlink()
            except Exception:
                pass

    if not audio_words:
        # Fallback to shifting original transcript if Whisper yielded no words
        if orig_transcript:
            return _shift_transcript_to_zero(orig_transcript, clip_start, clip_dur)
        return FullTranscript(
            video_id="fallback",
            duration=clip_dur,
            text="",
            segments=[],
            language="en"
        )

    # If no original transcript to preserve spelling from, build directly from audio
    if not orig_transcript:
        return _build_transcript_from_audio_words(audio_words, clip_dur)

    # Collect candidate words from original transcript
    orig_words: List[Tuple[str, str, float, float]] = []
    for seg in orig_transcript.segments:
        for w in seg.words:
            if (clip_end is None or w.start <= clip_end) and w.end >= clip_start:
                clean = re.sub(r"[^A-Z0-9\']", "", w.w.strip().upper())
                if clean:
                    orig_words.append((clean, w.w.strip(), float(w.start) - clip_start, float(w.end) - clip_start))

    if not orig_words:
        return _build_transcript_from_audio_words(audio_words, clip_dur)

    # Match tokens using difflib
    o_tokens = [w[0] for w in orig_words]
    a_tokens = [w[0] for w in audio_words]

    matcher = difflib.SequenceMatcher(None, o_tokens, a_tokens)
    if matcher.ratio() < 0.35:
        # Original transcript had low token agreement or hallucinated text;
        # directly use ground-truth words transcribed from clip audio.
        return _build_transcript_from_audio_words(audio_words, clip_dur)

    aligned_words: List[WordTimestamp] = []

    # Calculate global median offset across all matching words
    diffs = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                diffs.append(audio_words[j1 + k][2] - orig_words[i1 + k][2])

    if not diffs:
        return _build_transcript_from_audio_words(audio_words, clip_dur)

    # Build aligned word stream using exact audio timestamps
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                # High quality original spelling/punctuation + ground truth audio timestamps
                aligned_words.append(WordTimestamp(
                    w=orig_words[i1 + k][1],
                    start=audio_words[j1 + k][2],
                    end=audio_words[j1 + k][3],
                    prob=1.0
                ))
        elif tag == "replace":
            # For substituted words, prefer the audio words timestamps and text
            for k in range(j2 - j1):
                aligned_words.append(WordTimestamp(
                    w=audio_words[j1 + k][1],
                    start=audio_words[j1 + k][2],
                    end=audio_words[j1 + k][3],
                    prob=1.0
                ))
        elif tag == "insert":
            # Audio had extra words spoken (e.g. ad-libs, laughter)
            for k in range(j2 - j1):
                aligned_words.append(WordTimestamp(
                    w=audio_words[j1 + k][1],
                    start=audio_words[j1 + k][2],
                    end=audio_words[j1 + k][3],
                    prob=1.0
                ))
        # Note: "delete" opcodes represent words from the coarse transcript that were NOT
        # detected in the clip audio. We explicitly drop them to avoid phantom subtitles
        # and clamping pileups at t=0.00s.

    # Sort strictly by start time
    aligned_words.sort(key=lambda x: x.start)

    # Segment into cohesive sentences
    segments: List[TranscriptSegment] = []
    if aligned_words:
        segments.append(TranscriptSegment(
            text=" ".join(w.w for w in aligned_words),
            start=aligned_words[0].start,
            end=aligned_words[-1].end,
            words=aligned_words
        ))

    return FullTranscript(
        video_id=orig_transcript.video_id if orig_transcript else "aligned",
        duration=clip_dur,
        text=segments[0].text if segments else "",
        segments=segments,
        language=orig_transcript.language if orig_transcript else "en"
    )


def _build_transcript_from_audio_words(
    audio_words: List[Tuple[str, str, float, float]],
    clip_dur: float
) -> FullTranscript:
    words = [
        WordTimestamp(w=raw, start=st, end=et, prob=1.0)
        for _, raw, st, et in audio_words
    ]
    words.sort(key=lambda x: x.start)
    segments = []
    if words:
        segments.append(TranscriptSegment(
            text=" ".join(w.w for w in words),
            start=words[0].start,
            end=words[-1].end,
            words=words
        ))
    return FullTranscript(
        video_id="aligned",
        duration=clip_dur,
        text=segments[0].text if segments else "",
        segments=segments,
        language="en"
    )


def _shift_transcript_to_zero(
    transcript: FullTranscript,
    clip_start: float,
    clip_dur: float
) -> FullTranscript:
    new_words: List[WordTimestamp] = []
    for seg in transcript.segments:
        for w in seg.words:
            if w.end >= clip_start and w.start <= (clip_start + clip_dur):
                new_words.append(WordTimestamp(
                    w=w.w,
                    start=max(0.0, w.start - clip_start),
                    end=max(0.0, w.end - clip_start),
                    prob=w.prob
                ))
    new_words.sort(key=lambda x: x.start)
    segments = []
    if new_words:
        segments.append(TranscriptSegment(
            text=" ".join(w.w for w in new_words),
            start=new_words[0].start,
            end=new_words[-1].end,
            words=new_words
        ))
    return FullTranscript(
        video_id=transcript.video_id,
        duration=clip_dur,
        text=segments[0].text if segments else "",
        segments=segments,
        language=transcript.language
    )
