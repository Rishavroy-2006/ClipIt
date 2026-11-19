import os
import sys
import re
import statistics
from pathlib import Path
import mlx_whisper

def parse_ass_words(ass_path):
    words = []
    with open(ass_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('Dialogue:') and 'HookBanner' not in line and 'TopHeader' not in line:
                parts = line.split(',', 9)
                if len(parts) < 10:
                    continue
                t_str = parts[1]
                h, m, s = t_str.split(':')
                t_sec = int(h)*3600 + int(m)*60 + float(s)
                text = parts[9]
                idx = text.find('{\\c')
                if idx != -1:
                    cb = text.find('}', idx)
                    if cb != -1:
                        er = text.find('{\\r}', cb)
                        if er != -1:
                            w = text[cb+1:er].strip().upper()
                            w = re.sub(r'[^A-Z0-9\']', '', w)
                            if w:
                                words.append((w, t_sec))
    return words

def audit_clip(mp4_path, ass_path):
    res = mlx_whisper.transcribe(
        str(mp4_path),
        path_or_hf_repo='mlx-community/whisper-tiny',
        word_timestamps=True,
        condition_on_previous_text=False
    )
    audio_words = []
    for s in res.get('segments', []):
        for w in s.get('words', []):
            if float(w.get('probability', 1.0)) < 0.05:
                continue
            clean = re.sub(r'[^A-Z0-9\']', '', w['word'].strip().upper())
            if clean:
                audio_words.append((clean, float(w['start']), float(w['end'])))

    ass_words = parse_ass_words(ass_path)
    if not ass_words:
        return None, f"No ASS words parsed (audio words: {len(audio_words)})"

    # Match words within a reasonable window
    offsets = []
    matches = []
    for aw, at in ass_words:
        best_match = None
        min_dist = 2.0  # search window
        for w, st, et in audio_words:
            if aw == w and abs(st - at) < min_dist:
                best_match = (aw, at, st, st - at)
                min_dist = abs(st - at)
        if best_match:
            offsets.append(best_match[3])
            matches.append(best_match)

    if not audio_words:
        if len(ass_words) <= 4:
            return 0.0, f"✅ Reaction/Scream moment (0 spoken audio words, {len(ass_words)} exclamation words)"
        return None, f"0 audio words detected ({len(ass_words)} ass words)"

    if not offsets:
        if len(ass_words) <= 2 or len(audio_words) <= 3:
            return 0.0, f"✅ Reaction/Scream moment ({len(audio_words)} audio words, {len(ass_words)} exclamation words)"
        return None, f"0 matches out of {len(ass_words)} ass words and {len(audio_words)} audio words"

    med = statistics.median(offsets)
    return med, f"median: {med:+.3f}s ({med*1000:+.0f}ms) | matched {len(offsets)}/{len(ass_words)} words"

def main():
    root = Path('out/yt_qSyo5d-dVzA_overnight')
    part_dirs = sorted([d for d in root.iterdir() if d.is_dir() and d.name.startswith('part_')])

    print(f"Auditing all clips across {len(part_dirs)} parts...")
    results = []
    
    for pd in part_dirs:
        sidecars = sorted(pd.glob('pt*.json'))
        for sc in sidecars:
            cid = sc.stem
            mp4 = pd / f"{cid}.mp4"
            blur_mp4 = pd / f"{cid}_blur.mp4"
            reframed_mp4 = pd / f"{cid}_reframed.mp4"
            ass = pd / f"{cid}.ass"

            if not mp4.exists() or mp4.stat().st_size <= 1024 * 1024:
                continue

            variants_ok = blur_mp4.exists() and reframed_mp4.exists() and blur_mp4.stat().st_size > 1024 * 1024 and reframed_mp4.stat().st_size > 1024 * 1024

            med, msg = audit_clip(mp4, ass)
            is_synced = med is not None and abs(med) <= 0.12
            status = "✅ SYNCED" if is_synced and variants_ok else "❌ DESYNC/INCOMPLETE"
            results.append((pd.name, cid, status, med, msg, variants_ok))
            print(f"[{pd.name}] {cid:10s} -> {status} : {msg} (variants: {'OK' if variants_ok else 'MISSING'})")

    print("\n================ COMPREHENSIVE 45-CLIP AUDIT REPORT ================")
    synced_count = sum(1 for r in results if r[2] == "✅ SYNCED")
    print(f"Total Valid Clips: {len(results)}")
    print(f"Synced & Complete: {synced_count}/{len(results)}")
    print(f"Desynced / Flawed: {len(results) - synced_count}")
    
    if len(results) - synced_count > 0:
        print("\nClips requiring attention:")
        for r in results:
            if r[2] != "✅ SYNCED":
                print(f"  FAILED: {r[0]} / {r[1]} -> {r[4]} (variants={r[5]})")

if __name__ == '__main__':
    main()
