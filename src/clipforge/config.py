"""Configuration loader and schema for ClipForge."""

import os
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
from pydantic import BaseModel, Field


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
    draft_codec: str = "h264_videotoolbox"
    final_codec: str = "libx264"
    crf: int = 18
    audio_lufs: float = -14.0


class SafeZonesConfig(BaseModel):
    bottom_margin: float = 0.20
    right_margin: float = 0.15


class AppConfig(BaseModel):
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    signals: SignalConfig = Field(default_factory=SignalConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    safe_zones: SafeZonesConfig = Field(default_factory=SafeZonesConfig)


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    if config_path is None:
        config_path = Path("config.yaml")

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            return AppConfig(**data)
    return AppConfig()
