from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from selfietl.api.deps import get_config, get_db
from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.jobs.runner import JobsPaused, runner
from selfietl.models import (
    AutoRenderResponse,
    StartJobResponse,
    UpdateAutoRenderRequest,
)
from selfietl.scheduler import (
    DEFAULT_RENDER_CONFIG,
    kick_off_auto_render,
    load_settings,
    next_run_at,
    parse_time,
    primary_project_id,
    save_settings,
)

router = APIRouter(prefix="/auto-render", tags=["auto-render"])


def _scheduler(request: Request):
    return getattr(request.app.state, "auto_render_scheduler", None)


def _build_response(
    db: Database,
    config: AppConfig,
    *,
    request: Request,
) -> AutoRenderResponse:
    settings = load_settings(config)
    project_id = primary_project_id(db, config)
    next_run = next_run_at(datetime.now(), settings.time, settings.last_run_date, settings.last_attempt_at)
    last_render: dict[str, Any] | None = None
    if settings.last_render_id:
        row = db.fetchone(
            "SELECT id, status, started_at, finished_at, output_path FROM renders WHERE id = ?",
            (settings.last_render_id,),
        )
        if row:
            last_render = {
                "id": int(row["id"]),
                "status": row["status"],
                "started_at": str(row["started_at"]) if row["started_at"] else None,
                "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
                "output_path": row["output_path"],
                "video_url": f"/api/renders/{int(row['id'])}/file" if row["status"] == "done" else None,
            }
    return AutoRenderResponse(
        enabled=settings.enabled,
        time=settings.time,
        next_run_at=next_run.isoformat(timespec="seconds"),
        last_run_date=settings.last_run_date,
        last_render_id=settings.last_render_id,
        last_attempt_at=settings.last_attempt_at,
        last_error=settings.last_error,
        last_render=last_render,
        render_config=settings.render_config,
        project_id=project_id,
        scheduler_running=bool(_scheduler(request)),
    )


@router.get("", response_model=AutoRenderResponse)
def get_auto_render(
    request: Request,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    return _build_response(db, config, request=request)


@router.patch("", response_model=AutoRenderResponse)
def update_auto_render(
    payload: UpdateAutoRenderRequest,
    request: Request,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    settings = load_settings(config)
    if payload.enabled is not None:
        settings.enabled = bool(payload.enabled)
    if payload.time is not None:
        try:
            parse_time(payload.time)
        except (ValueError, IndexError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid time: {exc}") from exc
        settings.time = payload.time
    if payload.render_config is not None:
        merged = dict(DEFAULT_RENDER_CONFIG)
        merged.update(settings.render_config or {})
        merged.update(payload.render_config)
        settings.render_config = merged
    save_settings(config, settings)
    scheduler = _scheduler(request)
    if scheduler is not None:
        scheduler.notify()
    return _build_response(db, config, request=request)


@router.post("/run", response_model=StartJobResponse)
async def run_auto_render_now(
    request: Request,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    project_id = primary_project_id(db, config)
    if project_id is None:
        raise HTTPException(status_code=400, detail="No project exists yet. Take a selfie first.")
    if runner.has_active_jobs():
        raise HTTPException(status_code=409, detail="The app is busy. Try again in a moment.")
    settings = load_settings(config)
    try:
        job_id, render_id = kick_off_auto_render(
            db, config, project_id, settings, job_name=f"manual_auto_render:{project_id}"
        )
    except JobsPaused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return StartJobResponse(
        job_id=job_id,
        status_url=f"/api/jobs/{job_id}",
        events_url=f"/api/jobs/{job_id}/events",
    )
