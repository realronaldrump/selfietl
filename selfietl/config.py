from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class DetectionConfig(BaseModel):
    model: str = "mediapipe_face_mesh"
    refine_landmarks: bool = True
    min_detection_confidence: float = 0.5
    max_detection_side: int = 2048


class QualityConfig(BaseModel):
    threshold: float = 0.6
    max_yaw_degrees: float = 25
    max_pitch_degrees: float = 20
    max_roll_degrees: float = 20
    min_eye_open_ratio: float = 0.18
    landmark_zscore_threshold: float = 3.0


class AlignmentConfig(BaseModel):
    mode: Literal["similarity", "affine"] = "similarity"
    interpolation: Literal["lanczos4", "cubic", "linear"] = "lanczos4"
    output_format: Literal["jpg", "png"] = "jpg"
    output_quality: int = 95
    preserve_exif: bool = True


class MorphConfig(BaseModel):
    mode: Literal["landmark_delaunay", "rife", "none"] = "landmark_delaunay"
    intermediate_frames: int = 8


class ExportDefaults(BaseModel):
    fps: int = 30
    codec: Literal["h264", "h265"] = "h264"
    crf: int = 18
    pixel_format: str = "yuv420p"


class DateOverlayConfig(BaseModel):
    enabled: bool = True
    # Kept for older saved configs/API payloads; the renderer uses a fixed full-date label.
    format: str = "%B %-d, %Y"
    position: Literal["bottom-right", "bottom-left", "top-right", "top-left"] = "bottom-right"
    font_size_px: int = 48
    opacity: float = 0.85


class RenderConfig(BaseModel):
    alignment_mode: Literal["similarity", "affine"] = "similarity"
    morph_mode: Literal["landmark_delaunay", "rife", "none"] = "landmark_delaunay"
    intermediate_frames: int = Field(default=8, ge=0, le=60)
    start_date: str | None = None
    end_date: str | None = None
    color_normalize: bool = False
    fps: int = Field(default=30, ge=1, le=120)
    resolution: Literal["original", "1080_square", "1080_vertical", "4k_landscape"] = "original"
    aspect_ratio: Literal["original", "square", "9:16", "16:9"] = "original"
    date_overlay: DateOverlayConfig = Field(default_factory=DateOverlayConfig)
    audio_path: str | None = None
    music_sync: bool = False
    fade_in_seconds: float = Field(default=0, ge=0, le=30)
    fade_out_seconds: float = Field(default=0, ge=0, le=30)
    codec: Literal["h264", "h265"] = "h264"
    crf: int = Field(default=18, ge=0, le=51)
    output_path: str | None = None


class AppConfig(BaseModel):
    data_dir: Path
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
    morph: MorphConfig = Field(default_factory=MorphConfig)
    export: ExportDefaults = Field(default_factory=ExportDefaults)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "catalog.db"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.toml"

    @property
    def landmarks_dir(self) -> Path:
        return self.data_dir / "cache" / "landmarks"

    @property
    def aligned_landmarks_dir(self) -> Path:
        return self.data_dir / "cache" / "aligned_landmarks"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "cache" / "thumbs"

    @property
    def render_cache_dir(self) -> Path:
        return self.data_dir / "cache" / "renders"

    @property
    def aligned_dir(self) -> Path:
        return self.data_dir / "aligned"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    def ensure_dirs(self) -> None:
        for path in [
            self.data_dir,
            self.landmarks_dir,
            self.aligned_landmarks_dir,
            self.thumbs_dir,
            self.render_cache_dir,
            self.aligned_dir,
            self.exports_dir,
            self.inbox_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)


def _default_data_dir() -> Path:
    configured = os.environ.get("SELFIE_TL_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".selfietl"


def load_config(data_dir: str | Path | None = None) -> AppConfig:
    root = Path(data_dir).expanduser() if data_dir else _default_data_dir()
    config = AppConfig(data_dir=root)
    config.ensure_dirs()
    if config.config_path.exists():
        with config.config_path.open("rb") as fh:
            payload = tomllib.load(fh)
        payload["data_dir"] = root
        config = AppConfig.model_validate(payload)
        config.ensure_dirs()
    else:
        write_default_config(config)
    return config


def write_default_config(config: AppConfig) -> None:
    config.ensure_dirs()
    text = """[detection]
model = "mediapipe_face_mesh"
refine_landmarks = true
min_detection_confidence = 0.5
max_detection_side = 2048

[quality]
threshold = 0.6
max_yaw_degrees = 25
max_pitch_degrees = 20
max_roll_degrees = 20
min_eye_open_ratio = 0.18
landmark_zscore_threshold = 3.0

[alignment]
mode = "similarity"
interpolation = "lanczos4"
output_format = "jpg"
output_quality = 95
preserve_exif = true

[morph]
mode = "landmark_delaunay"
intermediate_frames = 8

[export]
fps = 30
codec = "h264"
crf = 18
pixel_format = "yuv420p"
"""
    config.config_path.write_text(text, encoding="utf-8")
