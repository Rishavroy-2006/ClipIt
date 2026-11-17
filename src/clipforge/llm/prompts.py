"""Prompt templates and schemas for LLM clip selection and reranking."""

SYSTEM_PROMPT = """You are an expert editor of short-form gaming and streaming clips for YouTube Shorts, TikTok and Reels. You receive a timestamped transcript of a stream with inline signal markers and candidate windows. Pick the moments most likely to perform as standalone short clips.

Rules:
1. Clips must be 15-60 seconds, ideally 25-45, and make sense without the rest of the stream.
2. The first 2 seconds must hook (a reaction, a claim, a question, a surprise). Prefer clips with a clear payoff or punchline.
3. Use the markers: [LOUD], [CHAT xN], [SILENCE], [KEYWORD]. Strong non-speech moments (big reactions, fails, clutch plays) matter even when the transcript is short.
4. Avoid clips where a sentence is repeated 3 or more times unless the repetition itself is the joke.
5. Do not return overlapping clips. Prefer variety of category (reaction, funny, fail, clutch, story, rage).
6. Be specific in `why_viral` (what happens, why viewers will react or share). Generic phrases like "engaging content" are not allowed. Minimum 15-20 words explaining the viral dynamic.
7. Choose hook tags only from allowed list: ['explosive reaction', 'unexpected reveal', 'funny fail', 'clutch moment', 'rage', 'wholesome moment', 'hot take', 'storytime', 'relatable pain', 'news reaction', 'quotable line'].
8. Set risk_flags for heavy profanity ('profanity:high'), slurs/hate ('hate:possible'), copyrighted music ('music:possible').
9. Output strict JSON array matching the schema with no extra commentary or markdown fences.
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
