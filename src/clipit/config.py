"""Configuration loader and schema for ClipIt."""

import os
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
from pydantic import BaseModel, Field, model_validator


def get_available_encoder(requested: Optional[str] = None) -> str:
    """Return best available hardware or software h264 encoder."""
    import sys
    import shutil
    import subprocess

    if requested and requested not in ("auto", "h264_videotoolbox"):
        return requested

    # If specifically asked for videotoolbox on macOS, verify we are on macOS
    if requested == "h264_videotoolbox" and sys.platform == "darwin":
        return "h264_videotoolbox"

    if not shutil.which("ffmpeg"):
        return "libx264"

    try:
        res = subprocess.run(
            ["ffmpeg", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        encoders = res.stdout
        if sys.platform == "darwin" and "h264_videotoolbox" in encoders:
            return "h264_videotoolbox"
        if "h264_nvenc" in encoders:
            return "h264_nvenc"
        if "h264_qsv" in encoders:
            return "h264_qsv"
        if "h264_amf" in encoders:
            return "h264_amf"
        if "libx264" in encoders:
            return "libx264"
    except Exception:
        pass

    return "libx264"


class SignalWeights(BaseModel):
    loudness_z: float = 0.35
    chat_rate: float = 0.30
    speech_rate: float = 0.15
    scene_cuts: float = 0.10
    keywords: float = 0.10


class SignalConfig(BaseModel):
    weights: SignalWeights = Field(default_factory=SignalWeights)
    loudness_window_sec: int = 300
    tighten_silence_threshold_sec: float = 1.2
    tighten_padding_ms: int = 100


class DefaultsConfig(BaseModel):
    target_clips: int = 10
    min_score: int = 70
    min_length: float = 15.0
    max_length: float = 60.0
    preferred_min: float = 25.0
    preferred_max: float = 45.0
    selector: str = "file"
    layout: str = "blur-fill"
    style: str = "bold-yellow"
    censor_captions: bool = False
    concurrency: int = 2


class WhisperConfig(BaseModel):
    model: str = "mlx-community/whisper-large-v3-turbo"
    batch_size: int = 12
    word_timestamps: bool = True
    language: Optional[str] = None


class VideoConfig(BaseModel):
    output_width: int = 1080
    output_height: int = 1920
    target_fps: int = 60
    draft_codec: str = "auto"
    final_codec: str = "libx264"
    crf: int = 18
    audio_lufs: float = -14.0

    @model_validator(mode="after")
    def resolve_codec(self):
        self.draft_codec = get_available_encoder(self.draft_codec)
        return self


class SafeZonesConfig(BaseModel):
    bottom_margin: float = 0.20
    right_margin: float = 0.15


class RetentionConfig(BaseModel):
    enabled: bool = True
    auto_cleanup_days: int = 30
    clean_source_vod: bool = True
    clean_rendered_clips: bool = True


class AppConfig(BaseModel):
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    signals: SignalConfig = Field(default_factory=SignalConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    safe_zones: SafeZonesConfig = Field(default_factory=SafeZonesConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    if config_path is None:
        config_path = Path("config.yaml")

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return AppConfig(**data)
    return AppConfig()
