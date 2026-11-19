import re
import statistics
from pathlib import Path
import mlx_whisper

def audit_full(clip_id):
    mp4 = Path(f'out/yt_qSyo5d-dVzA_overnight/{clip_id}.mp4')
    ass = Path(f'out/yt_qSyo5d-dVzA_overnight/{clip_id}.ass')
    if not mp4.exists() or not ass.exists():
        print(f"Skipping {clip_id}: file not found")
        return

    print(f"================ {clip_id} ================")
    res = mlx_whisper.transcribe(str(mp4), path_or_hf_repo='mlx-community/whisper-tiny', word_timestamps=True)
    audio_words = []
    for s in res.get('segments', []):
        for w in s.get('words', []):
            audio_words.append((w['word'].strip().upper().replace('.', '').replace(',', ''), w['start'], w['end']))

    ass_active_words = []
    with open(ass, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('Dialogue:') and 'HookBanner' not in line:
                parts = line.split(',', 9)
                t_str = parts[1]
                h, m, s = t_str.split(':')
                t_sec = int(h)*3600 + int(m)*60 + float(s)
                text = parts[9]
                # Match {\c&H...}WORD{\r}
                idx1 = text.find('{\\c')
                if idx1 != -1:
                    close_brace = text.find('}', idx1)
                    if close_brace != -1:
                        end_r = text.find('{\\r}', close_brace)
                        if end_r != -1:
                            w_text = text[close_brace+1:end_r].strip().upper().replace('.', '').replace(',', '')
                            ass_active_words.append((w_text, t_sec))

    print(f"Audio words: {len(audio_words)}, ASS active words: {len(ass_active_words)}")
    
    offsets = []
    for aw, at in ass_active_words[:25]:
        for w, st, et in audio_words:
            if aw == w and abs(st - at) < 1.0:
                diff = st - at
                offsets.append((aw, at, st, diff))
                print(f"  Word {aw:12s}: Subtitle={at:6.2f}s | Audio={st:6.2f}s | Offset={diff:+.2f}s ({diff*1000:+.0f}ms)")
                break

    if offsets:
        med = statistics.median([o[3] for o in offsets])
        print(f">> {clip_id} MEDIAN OFFSET: {med:+.3f}s ({med*1000:+.0f}ms)")
        if abs(med) < 0.20:
            print(f">> STATUS: PERFECT SYNC (within {abs(med)*1000:.0f}ms, threshold <200ms)")
        else:
            print(f">> STATUS: DRIFT DETECTED ({med*1000:+.0f}ms)")

if __name__ == '__main__':
    for cid in ['pt07_c01', 'pt07_c02', 'pt08_c01', 'pt08_c02', 'pt08_c03']:
        audit_full(cid)
