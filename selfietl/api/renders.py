from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from selfietl.api.deps import get_config, get_db
from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.jobs.runner import CancellationRequested, JobsPaused, runner
from selfietl.jobs.sse import job_event_stream
from selfietl.models import JobResponse, RenderRequest, RenderResponse, StartJobResponse
from selfietl.pipeline.compose import create_render_row, mark_render_failed, render_project

router = APIRouter(tags=["renders"])

TERMINAL_RENDER_STATUSES = {"done", "failed", "cancelled"}
DEFAULT_CLEANUP_STATUSES = {"failed", "cancelled"}


@router.post("/projects/{project_id}/render", response_model=StartJobResponse)
async def render(
    project_id: int,
    payload: RenderRequest,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    if db.fetchone("SELECT id FROM projects WHERE id = ?", (project_id,)) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if runner.has_active_jobs():
        raise HTTPException(status_code=409, detail="The app is already working. Cancel or wait for the current step before starting another one.")
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

    try:
        job = runner.start(f"render:{render_id}", work)
    except JobsPaused as exc:
        mark_render_failed(db, render_id, str(exc), status="cancelled")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return StartJobResponse(job_id=job.id, status_url=f"/api/jobs/{job.id}", events_url=f"/api/jobs/{job.id}/events")


@router.get("/projects/{project_id}/renders", response_model=list[RenderResponse])
def history(project_id: int, db: Database = Depends(get_db)):
    rows = db.fetchall("SELECT * FROM renders WHERE project_id = ? ORDER BY started_at DESC", (project_id,))
    return [_render_response(row) for row in rows]


@router.delete("/projects/{project_id}/renders")
def delete_render_history(
    project_id: int,
    status: str = Query(default="failed,cancelled", description="Comma-separated render statuses to delete"),
    delete_files: bool = Query(default=True),
    delete_cache: bool = Query(default=True),
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    statuses = _parse_status_filter(status)
    _ensure_terminal_statuses(statuses)
    rows = db.fetchall(
        "SELECT * FROM renders WHERE project_id = ? AND status IN ({})".format(",".join("?" for _ in statuses)),
        (project_id, *sorted(statuses)),
    )
    return _delete_render_rows(rows, db, config, delete_files=delete_files, delete_cache=delete_cache)


@router.api_route("/renders/{render_id}/file", methods=["GET", "HEAD"])
def render_file(render_id: int, db: Database = Depends(get_db)):
    path = _render_file_path(render_id, db)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/renders/{render_id}/poster.jpg")
def render_poster(render_id: int, db: Database = Depends(get_db), config: AppConfig = Depends(get_config)):
    video_path = _render_file_path(render_id, db)
    poster_path = config.render_cache_dir / f"render_{render_id}" / "poster.jpg"
    if not poster_path.exists() or poster_path.stat().st_mtime < video_path.stat().st_mtime:
        _write_render_poster(video_path, poster_path)
    return FileResponse(poster_path, media_type="image/jpeg")


def _render_file_path(render_id: int, db: Database) -> Path:
    row = db.fetchone("SELECT output_path, status FROM renders WHERE id = ?", (render_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Render not found")
    if row["status"] != "done" or not row["output_path"]:
        raise HTTPException(status_code=404, detail="Render file is not ready")
    path = Path(row["output_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Render file missing")
    return path


def _write_render_poster(video_path: Path, poster_path: Path) -> None:
    poster_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = poster_path.with_suffix(".tmp.jpg")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        "1",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=720:-2",
        str(tmp_path),
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        tmp_path.unlink(missing_ok=True)
        detail = "Could not create video poster"
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            detail = f"{detail}: {exc.stderr.decode(errors='replace').strip()}"
        raise HTTPException(status_code=500, detail=detail) from exc
    tmp_path.replace(poster_path)


@router.delete("/renders/{render_id}")
def delete_render(
    render_id: int,
    delete_file: bool = Query(default=True),
    delete_cache: bool = Query(default=True),
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    row = db.fetchone("SELECT * FROM renders WHERE id = ?", (render_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Render not found")
    return _delete_render_rows([row], db, config, delete_files=delete_file, delete_cache=delete_cache)


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs():
    return [JobResponse(**job.public()) for job in runner.list()]


@router.delete("/jobs")
def clear_completed_jobs():
    return {"ok": True, "deleted": runner.clear_terminal()}


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


@router.delete("/render-cache")
def clear_render_cache(config: AppConfig = Depends(get_config)):
    if runner.has_active_jobs():
        raise HTTPException(status_code=409, detail="The app is busy. Wait for the current job before clearing render cache.")
    return _clear_render_cache(config)


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


def _parse_status_filter(value: str) -> set[str]:
    statuses = {item.strip().lower() for item in value.split(",") if item.strip()}
    return statuses or set(DEFAULT_CLEANUP_STATUSES)


def _ensure_terminal_statuses(statuses: set[str]) -> None:
    invalid = statuses - TERMINAL_RENDER_STATUSES
    if invalid:
        raise HTTPException(status_code=400, detail=f"Cannot delete active or unknown render statuses: {', '.join(sorted(invalid))}")


def _delete_render_rows(
    rows: list,
    db: Database,
    config: AppConfig,
    *,
    delete_files: bool,
    delete_cache: bool,
) -> dict:
    for row in rows:
        if row["status"] not in TERMINAL_RENDER_STATUSES:
            raise HTTPException(status_code=409, detail=f"Render {row['id']} is still {row['status']}")

    deleted_files: list[str] = []
    missing_files: list[str] = []
    freed_bytes = 0
    if delete_files:
        for row in rows:
            path_text = row["output_path"]
            if not path_text:
                continue
            path = Path(path_text).expanduser()
            if not path.exists():
                missing_files.append(str(path))
                continue
            if not path.is_file():
                continue
            size = path.stat().st_size
            path.unlink()
            freed_bytes += size
            deleted_files.append(str(path))

    deleted_cache_dirs: list[str] = []
    if delete_cache:
        for row in rows:
            cache_dir = config.render_cache_dir / f"render_{int(row['id'])}"
            if cache_dir.exists():
                freed_bytes += _path_size(cache_dir)
                shutil.rmtree(cache_dir, ignore_errors=True)
                deleted_cache_dirs.append(str(cache_dir))

    ids = [int(row["id"]) for row in rows]
    if ids:
        with db.connect() as conn:
            conn.execute(
                "DELETE FROM renders WHERE id IN ({})".format(",".join("?" for _ in ids)),
                tuple(ids),
            )

    cache_result = _clear_render_cache(config) if delete_cache and not runner.has_active_jobs() else {"deleted_cache_dirs": [], "freed_bytes": 0}
    deleted_cache_dirs.extend(cache_result["deleted_cache_dirs"])
    freed_bytes += int(cache_result["freed_bytes"])

    return {
        "ok": True,
        "deleted_render_ids": ids,
        "deleted_files": deleted_files,
        "missing_files": missing_files,
        "deleted_cache_dirs": deleted_cache_dirs,
        "freed_bytes": freed_bytes,
    }


def _clear_render_cache(config: AppConfig) -> dict:
    deleted_cache_dirs: list[str] = []
    freed_bytes = 0
    if not config.render_cache_dir.exists():
        return {"ok": True, "deleted_cache_dirs": deleted_cache_dirs, "freed_bytes": freed_bytes}
    for path in config.render_cache_dir.iterdir():
        if not path.is_dir() or not (path.name.startswith("render_") or path.name.startswith("preview_")):
            continue
        freed_bytes += _path_size(path)
        shutil.rmtree(path, ignore_errors=True)
        deleted_cache_dirs.append(str(path))
    return {"ok": True, "deleted_cache_dirs": deleted_cache_dirs, "freed_bytes": freed_bytes}


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total
