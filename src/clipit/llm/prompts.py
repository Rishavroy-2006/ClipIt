"""Prompt templates and schemas for LLM clip selection and reranking."""

SYSTEM_PROMPT = """You are an expert editor of short-form gaming and streaming clips for YouTube Shorts, TikTok and Reels. You receive a timestamped transcript of a stream with inline signal markers and candidate windows. Pick the moments most likely to perform as standalone short clips.

Rules:
1. CLIPS DURATION: Clips must be 15-60 seconds, ideally 25-45, and cut on clean sentence or speech boundaries.
2. THE 2-SECOND TEST: The first 2 seconds MUST stop a cold viewer from scrolling. If the opening 2 seconds would not stop a viewer, adjust the start time closer to the explosive moment or hook.
3. STANDS ALONE: The clip MUST make complete sense to someone who has seen nothing else. If it opens on an ambiguous pronoun ("that", "so anyway...", "and then he did it"), move the start back to where the idea begins. Never sacrifice the payoff to gain context: start earlier, never cut the punchline short.
4. HOOK PLAYBOOK: Write a scroll-stopping `hook_line` (max 10 words) naming what happens in THIS specific moment using proven patterns:
   - Open question ("Why did he actually do this?")
   - Hot take / controversy ("Nobody expected this reaction.")
   - Fact / detail shock ("99% of people missed this.")
   - Story loop / cliffhanger ("This went completely wrong instantly.")
   - POV / pattern interrupt ("POV: You witness the impossible.")
5. USE SIGNAL MARKERS: [LOUD], [CHAT xN], [SILENCE], [KEYWORD]. Strong non-speech moments (big screaming reactions, fails, clutch plays) matter even when the transcript is short.
6. AVOID DUPLICATION & FILLER: Never return two clips that land the same joke or cover the exact same gameplay beat. Reserve scores >= 80 for moments with genuine viral payoff, and place filler/rambling under 60.
7. SPECIFICITY IN `why_viral`: Exactly what happens and why viewers will watch to completion and share. Minimum 15-20 words explaining the retention mechanism.
8. ALLOWED HOOK TAGS: ['explosive reaction', 'unexpected reveal', 'funny fail', 'clutch moment', 'rage', 'wholesome moment', 'hot take', 'storytime', 'relatable pain', 'news reaction', 'quotable line'].
9. RISK FLAGS: Set risk_flags for heavy profanity ('profanity:high'), slurs/hate ('hate:possible'), copyrighted music ('music:possible').
10. STRICT JSON: Output a strict JSON array matching the schema with no extra commentary or markdown fences.
"""

def build_selection_prompt(
    video_title: str,
    streamer: str,
    target_clips: int,
    candidates_text: str,
    transcript_text: str,
) -> str:
    target_desc = "Auto (Select the truly viral, top-tier clips only, between 1 and 29 clips maximum; do not include mediocre filler)" if target_clips <= 0 else f"{target_clips} clips"
    instruction_line = (
        "Automatically identify and return ONLY the truly exceptional, viral highlight clips (minimum 1, strictly less than 30 clips total). "
        "Sort them strictly from highest virality score to lowest. Only include clips with clear hooks, great energy, or comedic payoff."
        if target_clips <= 0
        else f"Please return the top {target_clips} clips in valid JSON format matching the schema."
    )
    return f"""# Source Stream Information
- Title: {video_title}
- Streamer: {streamer or 'Gaming Streamer'}
- Target Clips: {target_desc}

# Top Candidate Signal Windows:
{candidates_text}

# Transcript with Signal Context:
{transcript_text}

{instruction_line}
"""
