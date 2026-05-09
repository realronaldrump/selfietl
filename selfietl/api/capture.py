from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from selfietl.api.deps import get_config, get_db
from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.jobs.runner import JobsPaused, runner
from selfietl.models import (
    CaptureResponse,
    DayPhotosResponse,
    StartJobResponse,
    TodayResponse,
)
from selfietl.pipeline.ingest import create_project
from selfietl.pipeline.single import discard_photo, import_to_inbox, process_single_photo
from selfietl.scheduler import primary_project_id, streak_summary

router = APIRouter(tags=["capture"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _ensure_primary_project(db: Database, config: AppConfig) -> int:
    project_id = primary_project_id(db, config)
    if project_id is not None:
        return project_id
    inbox_path = (config.data_dir / "inbox").resolve()
    inbox_path.mkdir(parents=True, exist_ok=True)
    return create_project(
        db,
        "Daily selfie",
        str(inbox_path),
        config_snapshot={
            "quality": config.quality.model_dump(),
            "alignment": config.alignment.model_dump(),
        },
    )


@router.post("/capture", response_model=StartJobResponse)
async def capture_selfie(
    file: UploadFile = File(...),
    captured_at: str | None = Query(default=None, description="Optional override for capture time, ISO 8601"),
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file upload")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Photo is larger than the 25 MB limit")
    captured_dt: datetime | None = None
    if captured_at:
        try:
            captured_dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            if captured_dt.tzinfo is not None:
                captured_dt = captured_dt.astimezone().replace(tzinfo=None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid captured_at: {exc}") from exc

    saved_path = import_to_inbox(
        config,
        contents=contents,
        filename=file.filename or "selfie.jpg",
        captured_at=captured_dt,
    )
    project_id = _ensure_primary_project(db, config)

    if runner.has_active_jobs():
        raise HTTPException(status_code=409, detail="The app is busy. Try again in a moment.")

    def work(progress, cancel_check):
        return process_single_photo(db, config, project_id, saved_path, progress, cancel_check)

    try:
        job = runner.start(f"capture:{saved_path.name}", work)
    except JobsPaused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return StartJobResponse(
        job_id=job.id,
        status_url=f"/api/jobs/{job.id}",
        events_url=f"/api/jobs/{job.id}/events",
    )


@router.get("/today", response_model=TodayResponse)
def today_status(
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    today = date.today()
    project_id = primary_project_id(db, config)
    today_photo: dict[str, Any] | None = None
    streak: dict[str, Any] = {
        "today": today.isoformat(),
        "has_today": False,
        "streak": 0,
        "longest_streak": 0,
        "total_days": 0,
    }
    latest_render: dict[str, Any] | None = None
    project_summary: dict[str, Any] | None = None
    canonical_ready = False

    if project_id is not None:
        streak = streak_summary(db, project_id, today)
        rows = db.fetchall(
            """
            SELECT p.*
            FROM photos p
            JOIN project_photos pp ON pp.photo_hash = p.hash
            WHERE pp.project_id = ? AND date(p.captured_at) = ?
            ORDER BY p.captured_at DESC
            """,
            (project_id, today.isoformat()),
        )
        if rows:
            today_photo = _photo_payload(rows[0])
        latest = db.fetchone(
            "SELECT * FROM renders WHERE project_id = ? AND status = 'done' ORDER BY finished_at DESC LIMIT 1",
            (project_id,),
        )
        if latest:
            latest_render = _render_payload(latest)
        project_row = db.fetchone(
            "SELECT id, name, source_folder, canonical_landmarks_path FROM projects WHERE id = ?",
            (project_id,),
        )
        if project_row:
            canonical_ready = bool(project_row["canonical_landmarks_path"]) and Path(
                project_row["canonical_landmarks_path"]
            ).exists()
            counts = db.fetchone(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN p.skipped = 0 THEN 1 ELSE 0 END) AS active
                FROM photos p
                JOIN project_photos pp ON pp.photo_hash = p.hash
                WHERE pp.project_id = ?
                """,
                (project_id,),
            )
            project_summary = {
                "id": int(project_row["id"]),
                "name": project_row["name"],
                "source_folder": project_row["source_folder"],
                "photo_count": int(counts["total"] or 0),
                "active_count": int(counts["active"] or 0),
            }

    return TodayResponse(
        date=today.isoformat(),
        has_today=streak["has_today"],
        streak=streak["streak"],
        longest_streak=streak["longest_streak"],
        total_days=streak["total_days"],
        today_photo=today_photo,
        latest_render=latest_render,
        project=project_summary,
        canonical_ready=canonical_ready,
    )


@router.get("/photos/by-date/{day}", response_model=DayPhotosResponse)
def photos_by_date(
    day: str,
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    try:
        target = date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD") from exc
    project_id = primary_project_id(db, config)
    if project_id is None:
        return DayPhotosResponse(date=target.isoformat(), photos=[])
    rows = db.fetchall(
        """
        SELECT p.*
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND date(p.captured_at) = ?
        ORDER BY p.captured_at
        """,
        (project_id, target.isoformat()),
    )
    return DayPhotosResponse(
        date=target.isoformat(),
        photos=[_photo_payload(row) for row in rows],
    )


@router.get("/calendar")
def selfie_calendar(
    start: str | None = Query(default=None, description="YYYY-MM-DD"),
    end: str | None = Query(default=None, description="YYYY-MM-DD"),
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    project_id = primary_project_id(db, config)
    if project_id is None:
        return {"days": []}
    today = date.today()
    end_date = date.fromisoformat(end) if end else today
    start_date = date.fromisoformat(start) if start else end_date - timedelta(days=180)
    rows = db.fetchall(
        """
        SELECT date(p.captured_at) AS day,
               MIN(p.hash) AS hash,
               COUNT(*) AS n,
               MAX(CASE WHEN p.skipped = 0 THEN 1 ELSE 0 END) AS has_active,
               AVG(p.quality_score) AS quality,
               MIN(CASE WHEN p.skipped = 0 THEN p.hash END) AS active_hash
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND date(p.captured_at) BETWEEN ? AND ?
        GROUP BY day
        ORDER BY day
        """,
        (project_id, start_date.isoformat(), end_date.isoformat()),
    )
    days = []
    for row in rows:
        active_hash = row["active_hash"] or row["hash"]
        days.append(
            {
                "date": row["day"],
                "count": int(row["n"] or 0),
                "has_active": bool(row["has_active"]),
                "quality": float(row["quality"]) if row["quality"] is not None else None,
                "thumb_url": f"/api/photos/{active_hash}/thumb" if active_hash else None,
                "hash": active_hash,
            }
        )
    return {"days": days, "start": start_date.isoformat(), "end": end_date.isoformat()}


@router.delete("/capture/{photo_hash}", response_model=CaptureResponse)
def delete_capture(photo_hash: str, db: Database = Depends(get_db), config: AppConfig = Depends(get_config)):
    if not discard_photo(db, config, photo_hash):
        raise HTTPException(status_code=404, detail="Photo not found")
    return CaptureResponse(hash=photo_hash, deleted=True)


def _photo_payload(row) -> dict[str, Any]:
    warnings = []
    if "warnings_json" in row.keys():
        raw = row["warnings_json"]
        if isinstance(raw, str) and raw:
            try:
                warnings = json.loads(raw)
            except json.JSONDecodeError:
                warnings = []
    return {
        "hash": row["hash"],
        "captured_at": str(row["captured_at"]),
        "quality_score": row["quality_score"],
        "yaw": row["yaw"],
        "pitch": row["pitch"],
        "roll": row["roll"],
        "eye_open_ratio": row["eye_open_ratio"],
        "skipped": bool(row["skipped"]),
        "skip_reason": row["skip_reason"],
        "user_override": bool(row["user_override"]),
        "thumb_url": f"/api/photos/{row['hash']}/thumb",
        "image_url": f"/api/photos/{row['hash']}/image",
        "aligned_url": f"/api/photos/{row['hash']}/aligned",
        "warnings": warnings,
    }


def _render_payload(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "status": row["status"],
        "started_at": str(row["started_at"]) if row["started_at"] else None,
        "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
        "output_path": row["output_path"],
        "video_url": f"/api/renders/{int(row['id'])}/file" if row["status"] == "done" else None,
    }
