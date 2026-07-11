from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from selfietl.api.deps import get_config, get_db
from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.jobs.runner import JobsPaused, runner
from selfietl.models import (
    HairExportRequest,
    HairFrameUpdate,
    HaircutCreateRequest,
    HaircutUpdateRequest,
    StartJobResponse,
)
from selfietl.pipeline.hair import (
    create_hair_export,
    create_haircut_event,
    ensure_hair_composite,
    get_project_hair,
    recompute_project_hair,
    render_hair_export,
    set_hair_excluded,
    update_haircut_event,
    update_haircut_suggestions,
)


router = APIRouter(tags=["hair"])


@router.get("/projects/{project_id}/hair")
def manifest(
    project_id: int,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    _ensure_project(db, project_id)
    return get_project_hair(db, config, project_id)


@router.post("/projects/{project_id}/hair/recompute", response_model=StartJobResponse)
async def recompute(
    project_id: int,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    _ensure_project(db, project_id)
    name = f"hair_analysis:{project_id}"
    if runner.has_active_jobs(except_name=name):
        raise HTTPException(status_code=409, detail="The app is already working. Wait for the current job before tracing hair.")
    try:
        job = runner.start(
            name,
            lambda progress, cancel: recompute_project_hair(
                db, config, project_id, progress=progress, cancel_check=cancel
            ),
        )
    except JobsPaused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StartJobResponse(job_id=job.id, status_url=f"/api/jobs/{job.id}", events_url=f"/api/jobs/{job.id}/events")


@router.patch("/photos/{photo_hash}/hair")
def update_frame(
    photo_hash: str,
    payload: HairFrameUpdate,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    try:
        set_hair_excluded(db, photo_hash, payload.excluded)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    project_rows = db.fetchall("SELECT project_id FROM project_photos WHERE photo_hash = ?", (photo_hash,))
    for row in project_rows:
        update_haircut_suggestions(db, config, int(row["project_id"]))
    return {"hash": photo_hash, "excluded": payload.excluded}


@router.post("/projects/{project_id}/haircuts")
def add_haircut(project_id: int, payload: HaircutCreateRequest, db: Database = Depends(get_db)):
    _ensure_project(db, project_id)
    try:
        return create_haircut_event(db, project_id, payload.event_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/haircuts/{event_id}")
def edit_haircut(event_id: int, payload: HaircutUpdateRequest, db: Database = Depends(get_db)):
    try:
        return update_haircut_event(
            db,
            event_id,
            event_date=payload.event_date,
            status=payload.status,
        )
    except ValueError as exc:
        status = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/projects/{project_id}/hair/export", response_model=StartJobResponse)
async def export_hair(
    project_id: int,
    payload: HairExportRequest,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    _ensure_project(db, project_id)
    if runner.has_active_jobs():
        raise HTTPException(status_code=409, detail="The app is already working. Wait before building the hair movie.")
    config_payload = payload.model_dump(mode="json")
    export_id = create_hair_export(db, config, project_id, config_payload)
    try:
        job = runner.start(
            f"hair_export:{export_id}",
            lambda progress, cancel: render_hair_export(
                db,
                config,
                project_id,
                export_id,
                config_payload,
                progress=progress,
                cancel_check=cancel,
            ),
        )
    except JobsPaused as exc:
        db.execute("UPDATE hair_exports SET status = 'failed', error = ? WHERE id = ?", (str(exc), export_id))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StartJobResponse(job_id=job.id, status_url=f"/api/jobs/{job.id}", events_url=f"/api/jobs/{job.id}/events")


@router.api_route("/photos/{photo_hash}/hair-composite.png", methods=["GET", "HEAD"])
def composite(
    photo_hash: str,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    try:
        path = ensure_hair_composite(db, config, photo_hash)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")


@router.api_route("/hair-exports/{export_id}/file", methods=["GET", "HEAD"])
def export_file(export_id: int, db: Database = Depends(get_db)):
    path = _export_path(export_id, db)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.api_route("/hair-exports/{export_id}/playback.mp4", methods=["GET", "HEAD"])
def export_playback(
    export_id: int,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    source = _export_path(export_id, db)
    output = config.hair_playback_dir / f"hair-export-{export_id}.mp4"
    if not output.exists() or output.stat().st_mtime < source.stat().st_mtime:
        _write_playback(source, output)
    return FileResponse(output, media_type="video/mp4")


def _ensure_project(db: Database, project_id: int) -> None:
    if db.fetchone("SELECT id FROM projects WHERE id = ?", (project_id,)) is None:
        raise HTTPException(status_code=404, detail="Project not found")


def _export_path(export_id: int, db: Database) -> Path:
    row = db.fetchone("SELECT status, output_path FROM hair_exports WHERE id = ?", (export_id,))
    if row is None or row["status"] != "done" or not row["output_path"]:
        raise HTTPException(status_code=404, detail="Hair export is not ready")
    path = Path(row["output_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Hair export file is missing")
    return path


def _write_playback(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.tmp.mp4")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-vf", "scale=720:900", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary),
    ]
    completed = subprocess.run(command, capture_output=True, timeout=300)
    if completed.returncode != 0:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Could not build hair playback video: {completed.stderr.decode(errors='replace').strip()}")
    temporary.replace(output)

