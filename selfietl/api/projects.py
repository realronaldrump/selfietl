from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from selfietl.api.deps import get_config, get_db
from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.jobs.runner import JobsPaused, runner
from selfietl.models import CreateProjectRequest, ProjectResponse, StartJobResponse
from selfietl.pipeline.canonical import compute_canonical_face, project_stats
from selfietl.pipeline.compose import quick_preview
from selfietl.pipeline.detect import detect_project
from selfietl.pipeline.ingest import create_project, scan_project

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse)
def create_project_endpoint(
    payload: CreateProjectRequest,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    source = Path(payload.source_folder).expanduser()
    if not source.exists() or not source.is_dir():
        raise HTTPException(status_code=400, detail=f"Source folder does not exist: {source}")
    project_id = create_project(
        db,
        payload.name,
        str(source.resolve()),
        config_snapshot={"quality": config.quality.model_dump(), "alignment": config.alignment.model_dump()},
    )
    return _project_response(db, project_id)


@router.get("", response_model=list[ProjectResponse])
def list_projects(db: Database = Depends(get_db)):
    rows = db.fetchall("SELECT * FROM projects ORDER BY created_at DESC")
    if not rows:
        return []
    count_rows = db.fetchall(
        """
        SELECT pp.project_id AS project_id,
               COUNT(*) AS total,
               SUM(CASE WHEN p.skipped = 0 THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN p.skipped = 1 THEN 1 ELSE 0 END) AS skipped
        FROM project_photos pp
        JOIN photos p ON p.hash = pp.photo_hash
        GROUP BY pp.project_id
        """
    )
    counts_by_project = {row["project_id"]: row for row in count_rows}
    return [_project_response_from_row(row, counts_by_project.get(row["id"])) for row in rows]


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, db: Database = Depends(get_db)):
    return _project_response(db, project_id)


@router.post("/{project_id}/scan", response_model=StartJobResponse)
async def scan(project_id: int, db: Database = Depends(get_db), config: AppConfig = Depends(get_config)):
    _ensure_project(db, project_id)
    name = f"scan:{project_id}"
    _ensure_no_other_job(name)
    job = _start_job(name, lambda progress, cancel: scan_project(db, config, project_id, progress, cancel))
    return _job_response(job.id)


@router.post("/{project_id}/detect", response_model=StartJobResponse)
async def detect(project_id: int, db: Database = Depends(get_db), config: AppConfig = Depends(get_config)):
    _ensure_project(db, project_id)
    name = f"detect:{project_id}"
    _ensure_no_other_job(name)
    job = _start_job(name, lambda progress, cancel: detect_project(db, config, project_id, progress, cancel_check=cancel))
    return _job_response(job.id)


@router.post("/{project_id}/recompute", response_model=StartJobResponse)
async def recompute(project_id: int, db: Database = Depends(get_db), config: AppConfig = Depends(get_config)):
    _ensure_project(db, project_id)
    name = f"canonical:{project_id}"
    _ensure_no_other_job(name)
    job = _start_job(
        name,
        lambda progress, cancel: {"canonical_path": str(compute_canonical_face(db, config, project_id, progress, cancel_check=cancel))},
    )
    return _job_response(job.id)


@router.get("/{project_id}/preview")
def preview(project_id: int, db: Database = Depends(get_db), config: AppConfig = Depends(get_config)):
    _ensure_project(db, project_id)
    output = quick_preview(db, config, project_id)
    return FileResponse(output, media_type="video/mp4", filename=output.name)


@router.get("/{project_id}/heatmap")
def heatmap(project_id: int, db: Database = Depends(get_db), config: AppConfig = Depends(get_config)):
    _ensure_project(db, project_id)
    path = config.data_dir / "cache" / f"heatmap_project_{project_id}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Heatmap has not been generated")
    return FileResponse(path, media_type="image/png")


@router.get("/{project_id}/avg-face")
def avg_face(project_id: int, db: Database = Depends(get_db), config: AppConfig = Depends(get_config)):
    _ensure_project(db, project_id)
    path = config.data_dir / "cache" / f"avg_face_project_{project_id}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Average face has not been generated")
    return FileResponse(path, media_type="image/png")


@router.get("/{project_id}/stats")
def stats(project_id: int, db: Database = Depends(get_db)):
    _ensure_project(db, project_id)
    return project_stats(db, project_id)


def _project_response(db: Database, project_id: int) -> ProjectResponse:
    row = db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    counts = db.fetchone(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN p.skipped = 0 THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN p.skipped = 1 THEN 1 ELSE 0 END) AS skipped
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ?
        """,
        (project_id,),
    )
    return _project_response_from_row(row, counts)


def _project_response_from_row(row, counts) -> ProjectResponse:
    config_payload = None
    if row["config_json"]:
        try:
            config_payload = json.loads(row["config_json"])
        except json.JSONDecodeError:
            config_payload = None
    return ProjectResponse(
        id=row["id"],
        name=row["name"],
        source_folder=row["source_folder"],
        created_at=row["created_at"],
        canonical_landmarks_path=row["canonical_landmarks_path"],
        config=config_payload,
        photo_count=int(counts["total"]) if counts and counts["total"] is not None else 0,
        active_count=int(counts["active"]) if counts and counts["active"] is not None else 0,
        skipped_count=int(counts["skipped"]) if counts and counts["skipped"] is not None else 0,
    )


def _ensure_project(db: Database, project_id: int) -> None:
    if db.fetchone("SELECT id FROM projects WHERE id = ?", (project_id,)) is None:
        raise HTTPException(status_code=404, detail="Project not found")


def _ensure_no_other_job(name: str) -> None:
    if runner.has_active_jobs(except_name=name):
        raise HTTPException(status_code=409, detail="The app is already working. Cancel or wait for the current step before starting another one.")


def _start_job(name: str, work) -> object:
    try:
        return runner.start(name, work)
    except JobsPaused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _job_response(job_id: str) -> StartJobResponse:
    return StartJobResponse(
        job_id=job_id,
        status_url=f"/api/jobs/{job_id}",
        events_url=f"/api/jobs/{job_id}/events",
    )
