from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import RenderConfig


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_folder: str = Field(min_length=1)


class ProjectResponse(BaseModel):
    id: int
    name: str | None
    source_folder: str
    created_at: datetime | str | None = None
    canonical_landmarks_path: str | None = None
    config: dict[str, Any] | None = None
    photo_count: int = 0
    active_count: int = 0
    skipped_count: int = 0


class PhotoResponse(BaseModel):
    hash: str
    path: str
    captured_at: datetime | str
    width: int | None = None
    height: int | None = None
    file_size: int | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    perceptual_hash: str | None = None
    detected_at: datetime | str | None = None
    landmarks_path: str | None = None
    quality_score: float | None = None
    yaw: float | None = None
    pitch: float | None = None
    roll: float | None = None
    eye_open_ratio: float | None = None
    mouth_open_ratio: float | None = None
    skipped: bool = False
    skip_reason: str | None = None
    user_override: bool = False
    thumb_url: str | None = None
    image_url: str | None = None


class PhotoListResponse(BaseModel):
    items: list[PhotoResponse]
    total: int
    limit: int
    offset: int


class PatchPhotoRequest(BaseModel):
    skipped: bool | None = None
    user_override: bool | None = None
    skip_reason: str | None = None
    captured_at: str | None = None


class StartJobResponse(BaseModel):
    job_id: str
    status_url: str
    events_url: str


class CapturePreviewItem(BaseModel):
    index: int
    filename: str
    file_size: int
    supported: bool = True
    captured_at: str | None = None
    captured_at_source: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    width: int | None = None
    height: int | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class CapturePreviewResponse(BaseModel):
    items: list[CapturePreviewItem]


class JobResponse(BaseModel):
    id: str
    name: str
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    progress: float = 0
    progress_done: int = 0
    progress_total: int = 0
    stage: str | None = None
    message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RenderRequest(RenderConfig):
    pass


class RenderResponse(BaseModel):
    id: int
    project_id: int
    output_path: str | None = None
    config: dict[str, Any] | None = None
    started_at: datetime | str | None = None
    finished_at: datetime | str | None = None
    status: str
    error: str | None = None


class CapturedPhoto(BaseModel):
    hash: str
    captured_at: datetime | str
    quality_score: float | None = None
    yaw: float | None = None
    pitch: float | None = None
    roll: float | None = None
    eye_open_ratio: float | None = None
    skipped: bool = False
    skip_reason: str | None = None
    user_override: bool = False
    thumb_url: str
    image_url: str
    aligned_url: str | None = None
    warnings: list[str] = Field(default_factory=list)


class TodayProjectSummary(BaseModel):
    id: int
    name: str | None = None
    source_folder: str
    photo_count: int
    active_count: int


class LatestRender(BaseModel):
    id: int
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    output_path: str | None = None
    video_url: str | None = None


class TodayResponse(BaseModel):
    date: str
    has_today: bool
    streak: int
    longest_streak: int
    total_days: int
    today_photo: CapturedPhoto | None = None
    latest_render: LatestRender | None = None
    project: TodayProjectSummary | None = None
    canonical_ready: bool = False


class DayPhotosResponse(BaseModel):
    date: str
    photos: list[CapturedPhoto]


class CaptureResponse(BaseModel):
    hash: str
    deleted: bool = False


class UpdateAutoRenderRequest(BaseModel):
    enabled: bool | None = None
    time: str | None = None
    render_config: dict[str, Any] | None = None


class AutoRenderResponse(BaseModel):
    enabled: bool
    time: str
    next_run_at: str
    last_run_date: str | None = None
    last_checked_date: str | None = None
    last_render_id: int | None = None
    last_attempt_at: str | None = None
    last_error: str | None = None
    last_render: LatestRender | None = None
    render_config: dict[str, Any]
    project_id: int | None = None
    has_pending_changes: bool = False
    scheduler_running: bool = False


class FaceShapePeriod(BaseModel):
    start: str
    end: str


class FaceShapeProfileUpdate(BaseModel):
    lighter: FaceShapePeriod | None = None
    fuller: FaceShapePeriod | None = None


class FaceShapeCompareRequest(BaseModel):
    a: FaceShapePeriod
    b: FaceShapePeriod
