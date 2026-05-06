from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from selfietl.api.deps import get_config, get_db
from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.jobs.runner import CancellationRequested, runner
from selfietl.jobs.sse import job_event_stream
from selfietl.models import JobResponse, RenderRequest, RenderResponse, StartJobResponse
from selfietl.pipeline.compose import create_render_row, mark_render_failed, render_project

router = APIRouter(tags=["renders"])


@router.post("/projects/{project_id}/render", response_model=StartJobResponse)
async def render(
    project_id: int,
    payload: RenderRequest,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    if db.fetchone("SELECT id FROM projects WHERE id = ?", (project_id,)) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    render_id = create_render_row(db, project_id, payload)

    def work(progress, cancel_check):
        try:
            return render_project(db, config, project_id, payload, render_id, progress, cancel_check)
        except CancellationRequested as exc:
            mark_render_failed(db, render_id, str(exc), status="cancelled")
            raise
        except Exception as exc:
            mark_render_failed(db, render_id, f"{exc.__class__.__name__}: {exc}", status="failed")
            raise

    job = runner.start(f"render:{render_id}", work)
    return StartJobResponse(job_id=job.id, status_url=f"/api/jobs/{job.id}", events_url=f"/api/jobs/{job.id}/events")


@router.get("/projects/{project_id}/renders", response_model=list[RenderResponse])
def history(project_id: int, db: Database = Depends(get_db)):
    rows = db.fetchall("SELECT * FROM renders WHERE project_id = ? ORDER BY started_at DESC", (project_id,))
    return [_render_response(row) for row in rows]


@router.get("/renders/{render_id}/file")
def render_file(render_id: int, db: Database = Depends(get_db)):
    row = db.fetchone("SELECT output_path, status FROM renders WHERE id = ?", (render_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Render not found")
    if row["status"] != "done" or not row["output_path"]:
        raise HTTPException(status_code=404, detail="Render file is not ready")
    path = Path(row["output_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Render file missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def job_status(job_id: str):
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse(**job.public())


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(job_event_stream(job), media_type="text/event-stream")


@router.delete("/jobs/{job_id}")
def cancel_job(job_id: str):
    if not runner.cancel(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


def _render_response(row) -> RenderResponse:
    config = None
    if row["config_json"]:
        try:
            config = json.loads(row["config_json"])
        except json.JSONDecodeError:
            config = None
    return RenderResponse(
        id=row["id"],
        project_id=row["project_id"],
        output_path=row["output_path"],
        config=config,
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        error=row["error"],
    )
