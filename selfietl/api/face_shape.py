from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from selfietl.api.deps import get_db
from selfietl.db import Database
from selfietl.jobs.runner import JobsPaused, runner
from selfietl.models import FaceShapeCompareRequest, FaceShapeProfileUpdate, StartJobResponse
from selfietl.pipeline.face_shape import (
    compare_periods,
    get_project_trend,
    recompute_project,
    update_calibration,
)


router = APIRouter(prefix="/projects", tags=["face-shape"])


@router.get("/{project_id}/face-shape")
def trend(project_id: int, db: Database = Depends(get_db)):
    _ensure_project(db, project_id)
    return get_project_trend(db, project_id)


@router.put("/{project_id}/face-shape/profile")
def profile(project_id: int, payload: FaceShapeProfileUpdate, db: Database = Depends(get_db)):
    _ensure_project(db, project_id)
    try:
        return update_calibration(
            db,
            project_id,
            payload.lighter.model_dump() if payload.lighter else None,
            payload.fuller.model_dump() if payload.fuller else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{project_id}/face-shape/compare")
def compare(project_id: int, payload: FaceShapeCompareRequest, db: Database = Depends(get_db)):
    _ensure_project(db, project_id)
    try:
        return compare_periods(db, project_id, payload.a.model_dump(), payload.b.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{project_id}/face-shape/recompute", response_model=StartJobResponse)
async def recompute(
    project_id: int,
    rebuild_baseline: bool = Query(False),
    db: Database = Depends(get_db),
):
    _ensure_project(db, project_id)
    name = f"face_shape:{project_id}"
    if runner.has_active_jobs(except_name=name):
        raise HTTPException(status_code=409, detail="The app is already working. Wait for the current step before analyzing face shape.")
    try:
        job = runner.start(
            name,
            lambda progress, cancel: recompute_project(
                db,
                project_id,
                progress=progress,
                cancel_check=cancel,
                rebuild_baseline=rebuild_baseline,
            ),
        )
    except JobsPaused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StartJobResponse(
        job_id=job.id,
        status_url=f"/api/jobs/{job.id}",
        events_url=f"/api/jobs/{job.id}/events",
    )


def _ensure_project(db: Database, project_id: int) -> None:
    if db.fetchone("SELECT id FROM projects WHERE id = ?", (project_id,)) is None:
        raise HTTPException(status_code=404, detail="Project not found")
