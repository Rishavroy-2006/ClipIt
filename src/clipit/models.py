"""Data contracts and Pydantic models for ClipIt."""

from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator


class WordTimestamp(BaseModel):
    w: str
    start: float
    end: float
    prob: Optional[float] = 1.0


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: List[WordTimestamp] = Field(default_factory=list)


class FullTranscript(BaseModel):
    video_id: str
    duration: float
    language: str
    segments: List[TranscriptSegment] = Field(default_factory=list)


class CandidateWindow(BaseModel):
    id: str
    start: float
    end: float
    score: float
    loudness_z: float = 0.0
    chat_rate: float = 0.0
    speech_rate: float = 0.0
    scene_cuts: int = 0
    keywords_matched: List[str] = Field(default_factory=list)
    reason: str = ""


class ClipItem(BaseModel):
    id: str
    start: float
    end: float
    score: int = Field(ge=0, le=100)
    title: str
    title_alts: List[str] = Field(default_factory=list)
    hook_line: str = Field(description="Short hook overlay (max ~8-10 words)")
    hooks: List[str] = Field(default_factory=list)
    why_viral: str = Field(description="Specific reason why this moment performs")
    category: Literal[
        "reaction", "funny", "fail", "clutch", "story", "rage", "wholesome", "news", "other"
    ] = "reaction"
    signals: Dict[str, Any] = Field(default_factory=dict)
    risk_flags: List[str] = Field(default_factory=list)
    trim_notes: Optional[str] = ""
    layout: Literal["blur-fill", "stack", "face-zoom", "split"] = "blur-fill"
    source_permission: Literal["owner", "granted", "clip-policy", "unknown"] = "clip-policy"

    @field_validator("why_viral")
    @classmethod
    def validate_why_viral(cls, v: str) -> str:
        words = v.strip().split()
        if len(words) < 5:
            raise ValueError(f"why_viral must provide a concrete explanation, got: '{v}'")
        return v


class ClipCollection(BaseModel):
    video_id: str
    source_url_or_file: str
    created_at: str
    clips: List[ClipItem] = Field(default_factory=list)
