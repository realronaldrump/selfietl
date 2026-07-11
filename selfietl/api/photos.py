from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from selfietl.api.deps import get_config, get_db
from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.models import PatchPhotoRequest, PhotoListResponse, PhotoResponse

router = APIRouter(tags=["photos"])


@router.get("/projects/{project_id}/photos", response_model=PhotoListResponse)
def list_photos(
    project_id: int,
    limit: int = Query(80, ge=1, le=500),
    offset: int = Query(0, ge=0),
    skipped: bool | None = None,
    db: Database = Depends(get_db),
):
    where = ["pp.project_id = ?"]
    params: list = [project_id]
    if skipped is not None:
        where.append("p.skipped = ?")
        params.append(1 if skipped else 0)
    where_sql = " AND ".join(where)
    total = db.fetchone(
        f"SELECT COUNT(*) AS total FROM photos p JOIN project_photos pp ON pp.photo_hash = p.hash WHERE {where_sql}",
        tuple(params),
    )["total"]
    rows = db.fetchall(
        f"""
        SELECT p.*
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE {where_sql}
        ORDER BY p.captured_at
        LIMIT ? OFFSET ?
        """,
        tuple(params + [limit, offset]),
    )
    return PhotoListResponse(
        items=[_photo_response(row) for row in rows],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.patch("/photos/{photo_hash}", response_model=PhotoResponse)
def patch_photo(photo_hash: str, payload: PatchPhotoRequest, db: Database = Depends(get_db)):
    row = db.fetchone("SELECT * FROM photos WHERE hash = ?", (photo_hash,))
    if row is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    skipped = row["skipped"] if payload.skipped is None else int(payload.skipped)
    user_override = row["user_override"] if payload.user_override is None else int(payload.user_override)
    skip_reason = payload.skip_reason if "skip_reason" in payload.model_fields_set else row["skip_reason"]
    captured_at = row["captured_at"]
    warnings_json = row["warnings_json"] if "warnings_json" in row.keys() else "[]"
    if payload.skipped is False:
        user_override = 1
        skip_reason = None
    elif payload.skipped is True and skip_reason is None:
        skip_reason = "user_skipped"
    if payload.captured_at is not None:
        captured_at = _parse_capture_datetime(payload.captured_at).isoformat(sep=" ")
        user_override = 1
        warnings = _parse_warnings(warnings_json)
        if "captured_at_user_override" not in warnings:
            warnings.append("captured_at_user_override")
        warnings_json = json.dumps(warnings)
    db.execute(
        "UPDATE photos SET skipped = ?, user_override = ?, skip_reason = ?, captured_at = ?, warnings_json = ? WHERE hash = ?",
        (skipped, user_override, skip_reason, captured_at, warnings_json, photo_hash),
    )
    updated = db.fetchone("SELECT * FROM photos WHERE hash = ?", (photo_hash,))
    return _photo_response(updated)


@router.get("/photos/{photo_hash}/thumb")
def thumb(photo_hash: str, config: AppConfig = Depends(get_config)):
    path = config.thumbs_dir / f"{photo_hash}.jpg"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/photos/{photo_hash}/image")
def image(photo_hash: str, db: Database = Depends(get_db)):
    row = db.fetchone("SELECT path FROM photos WHERE hash = ?", (photo_hash,))
    if row is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Original file missing")
    media_type = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else None
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/photos/{photo_hash}/aligned")
def aligned(photo_hash: str, config: AppConfig = Depends(get_config)):
    for suffix in ("jpg", "png"):
        path = config.aligned_dir / f"{photo_hash}.{suffix}"
        if path.exists():
            return FileResponse(path, media_type=f"image/{'jpeg' if suffix == 'jpg' else 'png'}")
    raise HTTPException(status_code=404, detail="Aligned output not found")


@router.get("/photos/{photo_hash}/landmarks")
def landmarks(photo_hash: str, db: Database = Depends(get_db)):
    row = db.fetchone("SELECT landmarks_path FROM photos WHERE hash = ?", (photo_hash,))
    if row is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    if not row["landmarks_path"] or not Path(row["landmarks_path"]).exists():
        raise HTTPException(status_code=404, detail="Landmarks not found")
    payload = np.load(row["landmarks_path"])
    points = np.asarray(payload["landmarks"], dtype=float)
    return {"hash": photo_hash, "points": points[:, :3].round(6).tolist()}


def _photo_response(row) -> PhotoResponse:
    return PhotoResponse(
        hash=row["hash"],
        path=row["path"],
        captured_at=row["captured_at"],
        width=row["width"],
        height=row["height"],
        file_size=row["file_size"],
        camera_make=row["camera_make"],
        camera_model=row["camera_model"],
        perceptual_hash=row["perceptual_hash"],
        detected_at=row["detected_at"],
        landmarks_path=row["landmarks_path"],
        quality_score=row["quality_score"],
        yaw=row["yaw"],
        pitch=row["pitch"],
        roll=row["roll"],
        eye_open_ratio=row["eye_open_ratio"],
        mouth_open_ratio=row["mouth_open_ratio"],
        skipped=bool(row["skipped"]),
        skip_reason=row["skip_reason"],
        user_override=bool(row["user_override"]),
        thumb_url=f"/api/photos/{row['hash']}/thumb",
        image_url=f"/api/photos/{row['hash']}/image",
    )


def _parse_capture_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid captured_at: {exc}") from exc


def _parse_warnings(raw: object) -> list[str]:
    if not isinstance(raw, str) or not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload]
