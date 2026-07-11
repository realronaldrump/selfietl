from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from selfietl.config import AppConfig, RenderConfig
from selfietl.db import Database
from selfietl.jobs.runner import CancellationRequested, runner
from selfietl.pipeline.align import align_project
from selfietl.pipeline.canonical import compute_canonical_face
from selfietl.pipeline.compose import _active_rows, create_render_row, mark_render_failed, render_project

DEFAULT_RENDER_TIME = "03:00"
AUTO_RENDER_RETRY_DELAY = timedelta(minutes=60)
DEFAULT_RENDER_CONFIG = {
    "morph_mode": "landmark_delaunay",
    "intermediate_frames": 4,
    "fps": 30,
    "resolution": "1080_vertical",
    "aspect_ratio": "9:16",
    "color_normalize": False,
    "codec": "h264",
    "crf": 20,
    "date_overlay": {
        "enabled": True,
        "format": "%B %-d, %Y",
        "position": "bottom-right",
        "font_size_px": 48,
        "opacity": 0.85,
    },
}

logger = logging.getLogger("selfietl.scheduler")
SIGNATURE_VERSION = 1
_SETTINGS_LOCK = threading.RLock()


@dataclass
class AutoRenderSettings:
    enabled: bool
    time: str
    last_run_date: str | None = None
    last_render_id: int | None = None
    last_attempt_at: str | None = None
    last_error: str | None = None
    render_config: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_RENDER_CONFIG))
    last_checked_date: str | None = None
    last_render_signature: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "time": self.time,
            "last_run_date": self.last_run_date,
            "last_render_id": self.last_render_id,
            "last_attempt_at": self.last_attempt_at,
            "last_error": self.last_error,
            "render_config": self.render_config,
            "last_checked_date": self.last_checked_date,
            "last_render_signature": self.last_render_signature,
        }


def settings_path(config: AppConfig) -> Path:
    return config.data_dir / "auto_render.json"


def default_settings() -> AutoRenderSettings:
    return AutoRenderSettings(
        enabled=True,
        time=DEFAULT_RENDER_TIME,
        last_run_date=None,
        last_render_id=None,
        last_attempt_at=None,
        last_error=None,
        render_config=copy.deepcopy(DEFAULT_RENDER_CONFIG),
        last_checked_date=None,
        last_render_signature=None,
    )


def load_settings(config: AppConfig) -> AutoRenderSettings:
    with _SETTINGS_LOCK:
        return _load_settings_unlocked(config)


def _load_settings_unlocked(config: AppConfig) -> AutoRenderSettings:
    settings = default_settings()
    path = settings_path(config)
    if not path.exists():
        return settings
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    if not isinstance(payload, dict):
        return settings

    if isinstance(payload.get("enabled"), bool):
        settings.enabled = payload["enabled"]
    if isinstance(payload.get("time"), str):
        try:
            settings.time = parse_time(payload["time"]).strftime("%H:%M")
        except ValueError:
            pass
    for key in ("last_run_date", "last_attempt_at", "last_error", "last_checked_date", "last_render_signature"):
        value = payload.get(key)
        if value is None or isinstance(value, str):
            setattr(settings, key, value)
    render_id = payload.get("last_render_id")
    if render_id is None or isinstance(render_id, int) and not isinstance(render_id, bool):
        settings.last_render_id = render_id
    if isinstance(payload.get("render_config"), dict):
        merged = copy.deepcopy(DEFAULT_RENDER_CONFIG)
        merged.update(payload["render_config"])
        try:
            settings.render_config = RenderConfig.model_validate(merged).model_dump(mode="json")
        except ValidationError:
            pass
    return settings


def save_settings(config: AppConfig, settings: AutoRenderSettings) -> None:
    with _SETTINGS_LOCK:
        _save_settings_unlocked(config, settings)


def _save_settings_unlocked(config: AppConfig, settings: AutoRenderSettings) -> None:
    path = settings_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(settings.to_dict(), indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def update_settings(
    config: AppConfig,
    mutation: Callable[[AutoRenderSettings], None],
) -> AutoRenderSettings:
    with _SETTINGS_LOCK:
        settings = _load_settings_unlocked(config)
        mutation(settings)
        _save_settings_unlocked(config, settings)
        return settings


def parse_time(text: str) -> time:
    match = re.fullmatch(r"(\d{2}):(\d{2})", text or "")
    if match is None:
        raise ValueError("time must use 24-hour HH:MM format")
    hour, minute = (int(part) for part in match.groups())
    if hour > 23 or minute > 59:
        raise ValueError("time must be between 00:00 and 23:59")
    return time(hour=hour, minute=minute)


def next_run_at(
    now: datetime,
    target_time_text: str,
    last_run_date: str | None,
    last_attempt_at: str | None = None,
    last_checked_date: str | None = None,
) -> datetime:
    """Compute the next datetime the auto-render should fire.

    If today's render or unchanged-input check already finished, schedule
    tomorrow. If the target time was missed and no check is recorded, return an
    immediate catch-up time unless a recent attempt is still inside the retry
    window.
    """
    target = parse_time(target_time_text)
    today_target = datetime.combine(now.date(), target)
    today_iso = now.date().isoformat()
    if last_run_date == today_iso or last_checked_date == today_iso:
        return datetime.combine(now.date() + timedelta(days=1), target)
    if now < today_target:
        return today_target
    last_attempt = _parse_settings_datetime(last_attempt_at)
    if last_attempt and last_attempt.date() == now.date():
        retry_at = last_attempt + AUTO_RENDER_RETRY_DELAY
        if retry_at > now:
            return retry_at
    return now


def primary_project_id(db: Database, config: AppConfig) -> int | None:
    candidates = {
        str(config.inbox_dir),
        str(config.inbox_dir.resolve()),
    }
    for candidate in candidates:
        row = db.fetchone(
            "SELECT id FROM projects WHERE source_folder = ? ORDER BY created_at DESC LIMIT 1",
            (candidate,),
        )
        if row:
            return int(row["id"])
    row = db.fetchone("SELECT id FROM projects ORDER BY created_at DESC LIMIT 1")
    return int(row["id"]) if row else None


def render_config_from_settings(settings: AutoRenderSettings) -> RenderConfig:
    payload = dict(DEFAULT_RENDER_CONFIG)
    payload.update(settings.render_config or {})
    return RenderConfig.model_validate(payload)


def render_input_signature(
    db: Database,
    config: AppConfig,
    project_id: int,
    render_config: RenderConfig,
) -> str:
    """Fingerprint the inputs that can change an automatic render's output."""
    canonical_rows = db.fetchall(
        """
        SELECT p.hash, p.captured_at, p.width, p.height, p.detected_at, p.landmarks_path
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND p.skipped = 0 AND p.landmarks_path IS NOT NULL
        ORDER BY p.captured_at, p.hash
        """,
        (project_id,),
    )
    render_rows = _active_rows(db, project_id, render_config)
    payload = {
        "version": SIGNATURE_VERSION,
        "project_id": project_id,
        "render_config": render_config.model_dump(mode="json"),
        "alignment_config": config.alignment.model_dump(mode="json"),
        "canonical_photos": [
            _signature_row(row, ["hash", "captured_at", "width", "height", "detected_at", "landmarks_path"])
            for row in canonical_rows
        ],
        "render_photos": [
            _signature_row(row, ["hash", "captured_at", "quality_score"])
            for row in render_rows
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_signature_value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def auto_render_has_pending_changes(
    db: Database,
    config: AppConfig,
    project_id: int,
    settings: AutoRenderSettings,
) -> bool:
    render_config = render_config_from_settings(settings)
    return render_input_signature(db, config, project_id, render_config) != settings.last_render_signature


def kick_off_auto_render(
    db: Database,
    config: AppConfig,
    project_id: int,
    settings: AutoRenderSettings,
    *,
    job_name: str | None = None,
    input_signature: str | None = None,
) -> tuple[str, int]:
    render_config = render_config_from_settings(settings)
    input_signature = input_signature or render_input_signature(db, config, project_id, render_config)
    render_id = create_render_row(db, project_id, render_config)
    attempt_at = datetime.now()
    run_date = attempt_at.date().isoformat()

    def work(progress, cancel_check):
        try:
            compute_canonical_face(db, config, project_id, progress=progress, cancel_check=cancel_check)
            align_project(
                db,
                config,
                project_id,
                mode=render_config.alignment_mode,
                progress=progress,
                force=True,
                cancel_check=cancel_check,
            )
            result = render_project(db, config, project_id, render_config, render_id, progress, cancel_check)
            _record_auto_render_success(config, render_id, run_date, input_signature)
            return result
        except CancellationRequested as exc:
            mark_render_failed(db, render_id, str(exc), status="cancelled")
            _record_auto_render_error(config, render_id, str(exc))
            raise
        except Exception as exc:
            mark_render_failed(db, render_id, f"{exc.__class__.__name__}: {exc}", status="failed")
            _record_auto_render_error(config, render_id, f"{exc.__class__.__name__}: {exc}")
            raise

    try:
        job = runner.start(job_name or f"auto_render:{render_id}", work)
    except Exception:
        mark_render_failed(db, render_id, "Auto-render job could not be started", status="cancelled")
        raise
    _record_auto_render_attempt(config, render_id, attempt_at)
    return job.id, render_id


class AutoRenderScheduler:
    def __init__(self, db: Database, config: AppConfig):
        self.db = db
        self.config = config
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="selfietl.auto_render")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

    def notify(self) -> None:
        """Wake the scheduler so it re-reads settings and recomputes its sleep."""
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                settings = load_settings(self.config)
                if not settings.enabled:
                    await self._wait(min_seconds=60, max_seconds=300)
                    continue
                next_run = next_run_at(
                    datetime.now(),
                    settings.time,
                    settings.last_run_date,
                    settings.last_attempt_at,
                    settings.last_checked_date,
                )
                wait_seconds = max(1.0, (next_run - datetime.now()).total_seconds())
                logger.info(
                    "Auto-render scheduled for %s (in %.0f minutes)",
                    next_run.isoformat(timespec="minutes"),
                    wait_seconds / 60,
                )
                await self._wait(min_seconds=1, max_seconds=wait_seconds)
                # Re-read settings; a notify() may have changed them.
                settings = load_settings(self.config)
                if not settings.enabled:
                    continue
                now = datetime.now()
                if now < next_run_at(
                    now,
                    settings.time,
                    settings.last_run_date,
                    settings.last_attempt_at,
                    settings.last_checked_date,
                ) - timedelta(seconds=30):
                    # We woke early; the time has been moved. Loop again.
                    continue
                if settings.last_run_date == now.date().isoformat():
                    continue
                project_id = primary_project_id(self.db, self.config)
                if project_id is None:
                    logger.info("Auto-render skipped: no project exists yet")
                    _record_auto_render_check(self.config, now.date().isoformat())
                    continue
                if not project_has_active_photos(self.db, project_id):
                    logger.info("Auto-render skipped: project has no active photos yet")
                    _record_auto_render_check(self.config, now.date().isoformat())
                    continue
                if runner.has_active_jobs():
                    logger.info("Auto-render delayed: another job is active")
                    await self._wait(min_seconds=300, max_seconds=300)
                    continue
                render_config = render_config_from_settings(settings)
                input_signature = render_input_signature(self.db, self.config, project_id, render_config)
                if input_signature == settings.last_render_signature:
                    logger.info("Auto-render skipped: inputs unchanged since last successful render")
                    _record_auto_render_check(self.config, now.date().isoformat(), clear_error=True)
                    continue
                try:
                    _job_id, _render_id = kick_off_auto_render(
                        self.db,
                        self.config,
                        project_id,
                        settings,
                        input_signature=input_signature,
                    )
                except Exception as exc:
                    logger.exception("Auto-render kickoff failed: %s", exc)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Auto-render loop error: %s", exc)
                await self._wait(min_seconds=60, max_seconds=120)

    async def _wait(self, *, min_seconds: float, max_seconds: float) -> None:
        deadline = max(min_seconds, max_seconds)
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=deadline)
        except asyncio.TimeoutError:
            pass


def project_has_active_photos(db: Database, project_id: int) -> bool:
    row = db.fetchone(
        """
        SELECT COUNT(*) AS n
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND p.skipped = 0 AND p.landmarks_path IS NOT NULL
        """,
        (project_id,),
    )
    return bool(row and int(row["n"] or 0) > 0)


def _record_auto_render_attempt(config: AppConfig, render_id: int, attempt_at: datetime) -> None:
    def mutate(settings: AutoRenderSettings) -> None:
        settings.last_render_id = render_id
        settings.last_attempt_at = attempt_at.isoformat(sep=" ", timespec="seconds")
        settings.last_error = None

    update_settings(config, mutate)


def _record_auto_render_success(config: AppConfig, render_id: int, run_date: str, input_signature: str) -> None:
    def mutate(settings: AutoRenderSettings) -> None:
        settings.last_run_date = run_date
        settings.last_checked_date = run_date
        settings.last_render_id = render_id
        settings.last_render_signature = input_signature
        settings.last_error = None

    update_settings(config, mutate)


def _record_auto_render_check(config: AppConfig, run_date: str, *, clear_error: bool = False) -> None:
    def mutate(settings: AutoRenderSettings) -> None:
        settings.last_checked_date = run_date
        if clear_error:
            settings.last_error = None

    update_settings(config, mutate)


def _record_auto_render_error(config: AppConfig, render_id: int, error: str) -> None:
    def mutate(settings: AutoRenderSettings) -> None:
        settings.last_render_id = render_id
        settings.last_error = error

    update_settings(config, mutate)


def _signature_row(row, fields: list[str]) -> dict[str, Any]:
    return {field: _signature_value(row[field]) for field in fields}


def _signature_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _parse_settings_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def streak_summary(db: Database, project_id: int, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    rows = db.fetchall(
        """
        SELECT DISTINCT date(p.captured_at) AS d
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND p.skipped = 0
        ORDER BY d DESC
        """,
        (project_id,),
    )
    days = [row["d"] for row in rows if row["d"]]
    day_set = {d for d in days}
    has_today = today.isoformat() in day_set
    streak = 0
    cursor = today if has_today else today - timedelta(days=1)
    while cursor.isoformat() in day_set:
        streak += 1
        cursor -= timedelta(days=1)
    longest = 0
    current = 0
    previous: date | None = None
    for d_iso in sorted(day_set):
        d = date.fromisoformat(d_iso)
        if previous is not None and (d - previous).days == 1:
            current += 1
        else:
            current = 1
        previous = d
        longest = max(longest, current)
    return {
        "today": today.isoformat(),
        "has_today": has_today,
        "streak": streak,
        "longest_streak": longest,
        "total_days": len(day_set),
    }
