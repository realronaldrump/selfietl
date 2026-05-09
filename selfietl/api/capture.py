from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from selfietl.api.deps import get_config, get_db
from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.jobs.runner import JobsPaused, runner
from selfietl.models import (
    CapturePreviewResponse,
    CaptureResponse,
    DayPhotosResponse,
    StartJobResponse,
    TodayResponse,
)
from selfietl.pipeline.images import exif_metadata, image_dimensions, is_supported_image
from selfietl.pipeline.ingest import create_project
from selfietl.pipeline.single import discard_photo, import_to_inbox, process_single_photo
from selfietl.scheduler import primary_project_id, streak_summary

router = APIRouter(tags=["capture"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_BATCH_FILES = 40
MAX_BATCH_BYTES = 250 * 1024 * 1024


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


@router.post("/capture/preview", response_model=CapturePreviewResponse)
async def preview_capture_uploads(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="Select at least one photo")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(status_code=413, detail=f"Upload at most {MAX_BATCH_FILES} photos at once")

    items: list[dict[str, Any]] = []
    total_bytes = 0
    with TemporaryDirectory(prefix="selfietl-preview-") as temp_dir:
        root = Path(temp_dir)
        for index, file in enumerate(files):
            contents = await file.read()
            filename = file.filename or f"photo-{index + 1}.jpg"
            total_bytes += len(contents)
            if not contents:
                items.append(_preview_error(index, filename, 0, "Empty file upload"))
                continue
            if len(contents) > MAX_UPLOAD_BYTES:
                items.append(_preview_error(index, filename, len(contents), "Photo is larger than the 25 MB limit"))
                continue
            if total_bytes > MAX_BATCH_BYTES:
                raise HTTPException(status_code=413, detail="Combined upload is larger than the 250 MB batch limit")

            path = _temporary_upload_path(root, index, filename)
            path.write_bytes(contents)
            items.append(_preview_upload(index, filename, path, len(contents)))

    return CapturePreviewResponse(items=items)


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
    captured_dt = _parse_capture_datetime(captured_at)

    if runner.has_active_jobs():
        raise HTTPException(status_code=409, detail="The app is busy. Try again in a moment.")

    inferred_dt = _infer_upload_captured_at(contents, file.filename or "selfie.jpg")

    saved_path = import_to_inbox(
        config,
        contents=contents,
        filename=file.filename or "selfie.jpg",
        captured_at=captured_dt or inferred_dt,
    )
    project_id = _ensure_primary_project(db, config)

    def work(progress, cancel_check):
        return process_single_photo(db, config, project_id, saved_path, progress, cancel_check, captured_at=captured_dt)

    try:
        job = runner.start(f"capture:{saved_path.name}", work)
    except JobsPaused as exc:
        saved_path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return StartJobResponse(
        job_id=job.id,
        status_url=f"/api/jobs/{job.id}",
        events_url=f"/api/jobs/{job.id}/events",
    )


@router.post("/capture/batch", response_model=StartJobResponse)
async def capture_photo_batch(
    files: list[UploadFile] = File(...),
    metadata: str = Form(default="[]", description="JSON array with optional captured_at overrides"),
    db: Database = Depends(get_db),
    config: AppConfig = Depends(get_config),
):
    if not files:
        raise HTTPException(status_code=400, detail="Select at least one photo")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(status_code=413, detail=f"Upload at most {MAX_BATCH_FILES} photos at once")
    if runner.has_active_jobs():
        raise HTTPException(status_code=409, detail="The app is busy. Try again in a moment.")

    captured_overrides = _parse_batch_metadata(metadata, len(files))
    total_bytes = 0
    saved: list[dict[str, Any]] = []
    for index, file in enumerate(files):
        contents = await file.read()
        filename = file.filename or f"photo-{index + 1}.jpg"
        total_bytes += len(contents)
        if not contents:
            _cleanup_saved_uploads(saved)
            raise HTTPException(status_code=400, detail=f"{filename} is empty")
        if len(contents) > MAX_UPLOAD_BYTES:
            _cleanup_saved_uploads(saved)
            raise HTTPException(status_code=413, detail=f"{filename} is larger than the 25 MB limit")
        if total_bytes > MAX_BATCH_BYTES:
            _cleanup_saved_uploads(saved)
            raise HTTPException(status_code=413, detail="Combined upload is larger than the 250 MB batch limit")

        inferred_dt = _infer_upload_captured_at(contents, filename)
        captured_dt = captured_overrides[index]
        saved_path = import_to_inbox(
            config,
            contents=contents,
            filename=filename,
            captured_at=captured_dt or inferred_dt,
        )
        saved.append(
            {
                "index": index,
                "filename": filename,
                "path": saved_path,
                "captured_at": captured_dt,
            }
        )

    project_id = _ensure_primary_project(db, config)

    def work(progress, cancel_check):
        results = []
        succeeded = 0
        failed = 0
        duplicates = 0
        total_steps = max(1, len(saved) * 5)
        for item_index, item in enumerate(saved):
            cancel_check()

            def item_progress(stage: str, done: int, total: int, message: str, *, idx: int = item_index) -> None:
                progress(
                    stage,
                    idx * 5 + min(done, 5),
                    total_steps,
                    f"{idx + 1}/{len(saved)} {Path(item['filename']).name}: {message}",
                )

            try:
                result = process_single_photo(
                    db,
                    config,
                    project_id,
                    item["path"],
                    item_progress,
                    cancel_check,
                    captured_at=item["captured_at"],
                )
                result.update({"index": item["index"], "filename": item["filename"]})
                results.append(result)
                if result.get("duplicate_of"):
                    duplicates += 1
                succeeded += 1
            except Exception as exc:
                failed += 1
                results.append(
                    {
                        "index": item["index"],
                        "filename": item["filename"],
                        "path": str(item["path"]),
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                )
        return {
            "photos": results,
            "total": len(saved),
            "succeeded": succeeded,
            "failed": failed,
            "duplicates": duplicates,
        }

    try:
        job = runner.start(f"capture-batch:{len(saved)}:{datetime.now().isoformat()}", work)
    except JobsPaused as exc:
        _cleanup_saved_uploads(saved)
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return StartJobResponse(
        job_id=job.id,
        status_url=f"/api/jobs/{job.id}",
        events_url=f"/api/jobs/{job.id}/events",
    )


def _parse_capture_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid captured_at: {exc}") from exc


def _parse_batch_metadata(raw: str, file_count: int) -> list[datetime | None]:
    if not raw:
        return [None] * file_count
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid metadata JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise HTTPException(status_code=400, detail="metadata must be a JSON array")

    parsed: list[datetime | None] = []
    for index in range(file_count):
        value: str | None = None
        if index < len(payload):
            item = payload[index]
            if isinstance(item, dict):
                raw_value = item.get("captured_at")
                value = str(raw_value) if raw_value else None
            elif isinstance(item, str):
                value = item
            elif item is not None:
                raise HTTPException(status_code=400, detail=f"metadata[{index}] must be an object, string, or null")
        parsed.append(_parse_capture_datetime(value))
    return parsed


def _temporary_upload_path(root: Path, index: int, filename: str) -> Path:
    safe_name = Path(filename).name or f"photo-{index + 1}.jpg"
    candidate = root / f"{index}-{safe_name}"
    if candidate.suffix:
        return candidate
    return candidate.with_suffix(".jpg")


def _preview_upload(index: int, filename: str, path: Path, size: int) -> dict[str, Any]:
    if not is_supported_image(path):
        return _preview_error(index, filename, size, f"Unsupported image format: {path.suffix or 'unknown'}")
    try:
        metadata = exif_metadata(path)
        width, height = image_dimensions(path)
    except Exception as exc:
        return _preview_error(index, filename, size, f"{exc.__class__.__name__}: {exc}")
    return {
        "index": index,
        "filename": filename,
        "file_size": size,
        "supported": True,
        "captured_at": metadata["captured_at"].isoformat(sep=" "),
        "captured_at_source": metadata.get("captured_at_source"),
        "camera_make": metadata.get("camera_make"),
        "camera_model": metadata.get("camera_model"),
        "width": width,
        "height": height,
        "warnings": metadata.get("warnings") or [],
    }


def _preview_error(index: int, filename: str, size: int, error: str) -> dict[str, Any]:
    return {
        "index": index,
        "filename": filename,
        "file_size": size,
        "supported": False,
        "captured_at": None,
        "captured_at_source": None,
        "warnings": ["preview_failed"],
        "error": error,
    }


def _infer_upload_captured_at(contents: bytes, filename: str) -> datetime | None:
    with TemporaryDirectory(prefix="selfietl-upload-meta-") as temp_dir:
        path = _temporary_upload_path(Path(temp_dir), 0, filename)
        path.write_bytes(contents)
        if not is_supported_image(path):
            return None
        try:
            return exif_metadata(path)["captured_at"]
        except Exception:
            return None


def _cleanup_saved_uploads(saved: list[dict[str, Any]]) -> None:
    for item in saved:
        path = item.get("path")
        if isinstance(path, Path):
            path.unlink(missing_ok=True)


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
            ORDER BY p.skipped ASC, p.captured_at DESC
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
               MAX(CASE WHEN p.skipped = 0 THEN p.captured_at || '|' || p.hash END) AS active_key,
               MAX(p.captured_at || '|' || p.hash) AS any_key
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
        active_hash = _hash_from_calendar_key(row["active_key"] or row["any_key"]) or row["hash"]
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


def _hash_from_calendar_key(value: str | None) -> str | None:
    if not value or "|" not in value:
        return value
    return value.rsplit("|", 1)[1]


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
