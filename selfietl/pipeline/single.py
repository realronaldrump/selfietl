from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.pipeline.align import align_photo, aligned_path
from selfietl.pipeline.canonical import canonical_pixels
from selfietl.pipeline.detect import detect_landmarks
from selfietl.pipeline.images import (
    exif_metadata,
    file_size,
    image_dimensions,
    is_supported_image,
    perceptual_hash,
    sha1_file,
    write_thumbnail,
)
from selfietl.pipeline.score import compute_quality_score

Progress = Callable[[str, int, int, str], None]
CancelCheck = Callable[[], None]


def import_to_inbox(
    config: AppConfig,
    *,
    contents: bytes,
    filename: str,
    captured_at: datetime | None = None,
) -> Path:
    """Save uploaded bytes into the app inbox using a date-stamped filename.

    Returns the absolute path of the saved file.
    """
    inbox = config.data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff"}:
        suffix = ".jpg"
    stamp = (captured_at or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    candidate = inbox / f"selfie_{stamp}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = inbox / f"selfie_{stamp}_{counter}{suffix}"
        counter += 1
    candidate.write_bytes(contents)
    return candidate


def process_single_photo(
    db: Database,
    config: AppConfig,
    project_id: int,
    source_path: Path,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
    captured_at: datetime | None = None,
) -> dict:
    """Run scan + detect + align for a single newly captured photo.

    This is the fast path for daily captures. Canonical recomputation is left for
    the daily auto-render so that one good selfie does not move the anchor for
    every prior frame.
    """
    if not is_supported_image(source_path):
        raise RuntimeError(f"Unsupported image format: {source_path.suffix}")

    if progress:
        progress("capture", 1, 5, "Reading photo")
    if cancel_check:
        cancel_check()

    photo_hash = sha1_file(source_path)
    width, height = image_dimensions(source_path)
    meta = exif_metadata(source_path)
    if captured_at is not None:
        meta = _with_captured_at_override(meta, captured_at)
    phash = perceptual_hash(source_path)

    duplicate_of: str | None = None
    with db.connect() as conn:
        existing = conn.execute("SELECT hash FROM photos WHERE hash = ?", (photo_hash,)).fetchone()
        if existing is None and phash:
            phash_match = conn.execute(
                "SELECT hash FROM photos WHERE perceptual_hash = ? LIMIT 1", (phash,)
            ).fetchone()
            if phash_match:
                duplicate_of = phash_match["hash"]
        if existing is None:
            conn.execute(
                """
                INSERT INTO photos (
                    hash, path, captured_at, width, height, file_size,
                    camera_make, camera_model, perceptual_hash, warnings_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    photo_hash,
                    str(source_path),
                    meta["captured_at"].isoformat(sep=" "),
                    width,
                    height,
                    file_size(source_path),
                    meta["camera_make"],
                    meta["camera_model"],
                    phash,
                    json.dumps(meta["warnings"]),
                ),
            )
            write_thumbnail(source_path, config.thumbs_dir / f"{photo_hash}.jpg")
        else:
            conn.execute(
                "UPDATE photos SET path = ? WHERE hash = ?",
                (str(source_path), photo_hash),
            )

        conn.execute(
            "INSERT OR IGNORE INTO project_photos (project_id, photo_hash, added_at) VALUES (?, ?, ?)",
            (project_id, photo_hash, datetime.now().isoformat(sep=" ")),
        )

    if progress:
        progress("capture", 2, 5, "Finding face landmarks")
    if cancel_check:
        cancel_check()

    detection = detect_landmarks(source_path, config)
    detected_at = datetime.now().isoformat(sep=" ")

    if detection.landmarks is None:
        skip_reason = "no_face_detected" if "no_face_detected" in detection.warnings else "landmarks_unavailable"
        db.execute(
            """
            UPDATE photos
            SET detected_at = ?, skipped = 1, skip_reason = ?, quality_score = 0,
                user_override = 0
            WHERE hash = ?
            """,
            (detected_at, skip_reason, photo_hash),
        )
        return {
            "hash": photo_hash,
            "captured_at": meta["captured_at"].isoformat(sep=" "),
            "skipped": True,
            "skip_reason": skip_reason,
            "duplicate_of": duplicate_of,
            "aligned": False,
            "warnings": detection.warnings,
            "path": str(source_path),
        }

    landmarks_path = config.landmarks_dir / f"{photo_hash}.npz"
    landmarks_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        landmarks_path,
        landmarks=detection.landmarks.astype(np.float32),
        bbox=np.array(detection.bbox or (0, 0, 0, 0), dtype=np.float32),
        image_size=np.array([width, height], dtype=np.int32),
        confidence=np.array([detection.confidence], dtype=np.float32),
        method=np.array([detection.method]),
    )

    quality = compute_quality_score(
        confidence=detection.confidence,
        yaw=detection.yaw,
        pitch=detection.pitch,
        roll=detection.roll,
        eye_open_ratio=detection.eye_open_ratio,
        landmark_zscore=None,
        config=config.quality,
    ).score
    should_skip = quality < config.quality.threshold
    skip_reason = "low_quality" if should_skip else None
    replaced_count = 0

    with db.connect() as conn:
        conn.execute(
            """
            UPDATE photos
            SET detected_at = ?, landmarks_path = ?, quality_score = ?,
                yaw = ?, pitch = ?, roll = ?, eye_open_ratio = ?, mouth_open_ratio = ?,
                skipped = ?, skip_reason = ?, user_override = 0
            WHERE hash = ?
            """,
            (
                detected_at,
                str(landmarks_path),
                quality,
                detection.yaw,
                detection.pitch,
                detection.roll,
                detection.eye_open_ratio,
                detection.mouth_open_ratio,
                1 if should_skip else 0,
                skip_reason,
                photo_hash,
            ),
        )
        if not should_skip:
            replaced_count = _mark_other_active_captures_for_day(
                conn,
                project_id=project_id,
                keep_hash=photo_hash,
                captured_at=meta["captured_at"],
            )

    aligned = False
    project_row = db.fetchone(
        "SELECT canonical_landmarks_path FROM projects WHERE id = ?", (project_id,)
    )
    canonical_path = project_row["canonical_landmarks_path"] if project_row else None
    if canonical_path and Path(canonical_path).exists() and not should_skip:
        if progress:
            progress("capture", 4, 5, "Aligning to anchor")
        if cancel_check:
            cancel_check()
        target_landmarks, target_size = canonical_pixels(canonical_path)
        align_photo(
            source_path=source_path,
            landmarks_path=landmarks_path,
            target_landmarks=target_landmarks,
            target_size=target_size,
            output_path=aligned_path(config, photo_hash),
            aligned_landmarks_path=config.aligned_landmarks_dir / f"{photo_hash}.npz",
            mode=config.alignment.mode,
            quality=config.alignment.output_quality,
            preserve_exif=config.alignment.preserve_exif,
        )
        aligned = True

    if progress:
        progress("capture", 5, 5, "Selfie added")

    return {
        "hash": photo_hash,
        "captured_at": meta["captured_at"].isoformat(sep=" "),
        "skipped": should_skip,
        "skip_reason": skip_reason,
        "duplicate_of": duplicate_of,
        "aligned": aligned,
        "replaced_count": replaced_count,
        "quality_score": quality,
        "yaw": detection.yaw,
        "pitch": detection.pitch,
        "roll": detection.roll,
        "eye_open_ratio": detection.eye_open_ratio,
        "warnings": detection.warnings,
        "path": str(source_path),
    }


def discard_photo(db: Database, config: AppConfig, photo_hash: str) -> bool:
    """Delete a captured selfie, its caches, and its catalog row.

    Returns True if a row was deleted.
    """
    row = db.fetchone("SELECT path FROM photos WHERE hash = ?", (photo_hash,))
    if row is None:
        return False
    candidates: list[Path] = []
    if row["path"]:
        source_path = Path(row["path"])
        if _is_inside(config.inbox_dir, source_path):
            candidates.append(source_path)
    candidates.append(config.thumbs_dir / f"{photo_hash}.jpg")
    candidates.append(config.landmarks_dir / f"{photo_hash}.npz")
    candidates.append(config.aligned_landmarks_dir / f"{photo_hash}.npz")
    for suffix in ("jpg", "png"):
        candidates.append(config.aligned_dir / f"{photo_hash}.{suffix}")
    for path in candidates:
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
    with db.connect() as conn:
        conn.execute("DELETE FROM project_photos WHERE photo_hash = ?", (photo_hash,))
        conn.execute("DELETE FROM photos WHERE hash = ?", (photo_hash,))
    return True


def _mark_other_active_captures_for_day(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    keep_hash: str,
    captured_at: datetime,
) -> int:
    """Keep one active frame per local calendar date for daily captures."""
    cursor = conn.execute(
        """
        UPDATE photos
        SET skipped = 1,
            skip_reason = 'replaced_by_newer_capture',
            user_override = 0
        WHERE hash IN (
            SELECT p.hash
            FROM photos p
            JOIN project_photos pp ON pp.photo_hash = p.hash
            WHERE pp.project_id = ?
              AND p.hash != ?
              AND p.skipped = 0
              AND date(p.captured_at) = ?
        )
        """,
        (project_id, keep_hash, captured_at.date().isoformat()),
    )
    return int(cursor.rowcount or 0)


def _with_captured_at_override(meta: dict, captured_at: datetime) -> dict:
    updated = dict(meta)
    warnings = list(updated.get("warnings") or [])
    original = updated.get("captured_at")
    if original != captured_at and "captured_at_user_override" not in warnings:
        warnings.append("captured_at_user_override")
    updated["captured_at"] = captured_at
    updated["captured_at_source"] = "user_override"
    updated["warnings"] = warnings
    return updated


def _is_inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


__all__ = ["import_to_inbox", "process_single_photo", "discard_photo"]
