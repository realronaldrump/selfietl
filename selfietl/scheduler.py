from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from selfietl.config import AppConfig, RenderConfig
from selfietl.db import Database
from selfietl.jobs.runner import CancellationRequested, runner
from selfietl.pipeline.align import align_project
from selfietl.pipeline.canonical import compute_canonical_face
from selfietl.pipeline.compose import create_render_row, mark_render_failed, render_project

DEFAULT_RENDER_TIME = "03:00"
DEFAULT_RENDER_CONFIG = {
    "morph_mode": "landmark_delaunay",
    "intermediate_frames": 4,
    "fps": 30,
    "resolution": "1080_vertical",
    "aspect_ratio": "9:16",
    "color_normalize": False,
    "fade_in_seconds": 0.4,
    "fade_out_seconds": 0.4,
    "codec": "h264",
    "crf": 20,
    "date_overlay": {
        "enabled": True,
        "format": "%b %Y",
        "position": "bottom-right",
        "font_size_px": 48,
        "opacity": 0.85,
    },
}

logger = logging.getLogger("selfietl.scheduler")


@dataclass
class AutoRenderSettings:
    enabled: bool
    time: str
    last_run_date: str | None
    last_render_id: int | None
    render_config: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "time": self.time,
            "last_run_date": self.last_run_date,
            "last_render_id": self.last_render_id,
            "render_config": self.render_config,
        }


def settings_path(config: AppConfig) -> Path:
    return config.data_dir / "auto_render.json"


def default_settings() -> AutoRenderSettings:
    return AutoRenderSettings(
        enabled=True,
        time=DEFAULT_RENDER_TIME,
        last_run_date=None,
        last_render_id=None,
        render_config=dict(DEFAULT_RENDER_CONFIG),
    )


def load_settings(config: AppConfig) -> AutoRenderSettings:
    path = settings_path(config)
    base = default_settings().to_dict()
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key, value in payload.items():
                if key in base:
                    if key == "render_config" and isinstance(value, dict):
                        merged = dict(base[key])
                        merged.update(value)
                        base[key] = merged
                    else:
                        base[key] = value
        except (OSError, json.JSONDecodeError):
            pass
    return AutoRenderSettings(**base)


def save_settings(config: AppConfig, settings: AutoRenderSettings) -> None:
    path = settings_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2, default=str), encoding="utf-8")


def parse_time(text: str) -> time:
    parts = (text or DEFAULT_RENDER_TIME).split(":")
    hour = max(0, min(23, int(parts[0])))
    minute = max(0, min(59, int(parts[1]))) if len(parts) > 1 else 0
    return time(hour=hour, minute=minute)


def next_run_at(now: datetime, target_time_text: str, last_run_date: str | None) -> datetime:
    """Compute the next datetime the auto-render should fire.

    If today's run already happened (or the time has passed today and today's run did),
    schedule for the next day.
    """
    target = parse_time(target_time_text)
    today_target = datetime.combine(now.date(), target)
    today_iso = now.date().isoformat()
    if last_run_date == today_iso:
        return datetime.combine(now.date() + timedelta(days=1), target)
    if now < today_target:
        return today_target
    return datetime.combine(now.date() + timedelta(days=1), target)


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


def kick_off_auto_render(
    db: Database,
    config: AppConfig,
    project_id: int,
    settings: AutoRenderSettings,
    *,
    job_name: str | None = None,
) -> tuple[str, int]:
    render_config = render_config_from_settings(settings)
    render_id = create_render_row(db, project_id, render_config)

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
            return render_project(db, config, project_id, render_config, render_id, progress, cancel_check)
        except CancellationRequested as exc:
            mark_render_failed(db, render_id, str(exc), status="cancelled")
            raise
        except Exception as exc:
            mark_render_failed(db, render_id, f"{exc.__class__.__name__}: {exc}", status="failed")
            raise

    job = runner.start(job_name or f"auto_render:{render_id}", work)
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
                next_run = next_run_at(datetime.now(), settings.time, settings.last_run_date)
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
                if now < next_run_at(now, settings.time, settings.last_run_date) - timedelta(seconds=30):
                    # We woke early; the time has been moved. Loop again.
                    continue
                if settings.last_run_date == now.date().isoformat():
                    continue
                project_id = primary_project_id(self.db, self.config)
                if project_id is None:
                    logger.info("Auto-render skipped: no project exists yet")
                    settings.last_run_date = now.date().isoformat()
                    save_settings(self.config, settings)
                    continue
                if not _project_has_active_photos(self.db, project_id):
                    logger.info("Auto-render skipped: project has no active photos yet")
                    settings.last_run_date = now.date().isoformat()
                    save_settings(self.config, settings)
                    continue
                try:
                    _job_id, render_id = kick_off_auto_render(self.db, self.config, project_id, settings)
                except Exception as exc:
                    logger.exception("Auto-render kickoff failed: %s", exc)
                    continue
                settings.last_run_date = now.date().isoformat()
                settings.last_render_id = render_id
                save_settings(self.config, settings)
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


def _project_has_active_photos(db: Database, project_id: int) -> bool:
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
