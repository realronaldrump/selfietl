from __future__ import annotations

import hashlib
import json
import math
import subprocess
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import binary_dilation, distance_transform_edt, label

from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.pipeline.face_shape import FACE_OVAL
from selfietl.pipeline.images import open_oriented_image
from selfietl.pipeline.canonical import canonical_pixels


ALGORITHM_VERSION = "hair-v1"
HAIR_MODEL_NAME = "hair_segmenter.tflite"
HAIR_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/image_segmenter/"
    "hair_segmenter/float32/latest/hair_segmenter.tflite"
)
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1350
DEFAULT_FPS = 30

Progress = Callable[[str, int, int, str], None]
CancelCheck = Callable[[], None]


def ensure_hair_model(config: AppConfig) -> Path:
    path = config.models_dir / HAIR_MODEL_NAME
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".download")
    try:
        urllib.request.urlretrieve(HAIR_MODEL_URL, temporary)
        if not temporary.exists() or temporary.stat().st_size <= 0:
            raise RuntimeError("downloaded model was empty")
        temporary.replace(path)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"Hair analysis needs a one-time model download. Place {HAIR_MODEL_NAME} at {path} and retry."
        ) from exc
    return path


def create_hair_segmenter(config: AppConfig):
    try:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        running_mode = getattr(vision, "RunningMode", None)
        image_mode = running_mode.IMAGE if running_mode is not None else None
        if image_mode is None:
            from mediapipe.tasks.python.vision.core import vision_task_running_mode

            image_mode = vision_task_running_mode.VisionTaskRunningMode.IMAGE
        options = vision.ImageSegmenterOptions(
            base_options=BaseOptions(model_asset_path=str(ensure_hair_model(config))),
            running_mode=image_mode,
            output_confidence_masks=True,
            output_category_mask=False,
        )
        return vision.ImageSegmenter.create_from_options(options), mp
    except Exception as exc:
        raise RuntimeError(f"MediaPipe hair segmenter could not be initialized: {exc}") from exc


def source_signature(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha256(
        f"{ALGORITHM_VERSION}|{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()


def alignment_signature(aligned_landmarks_path: Path) -> str | None:
    if not aligned_landmarks_path.exists():
        return None
    stat = aligned_landmarks_path.stat()
    return hashlib.sha256(
        f"{ALGORITHM_VERSION}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()


def analyze_photo_hair(
    db: Database,
    config: AppConfig,
    photo_hash: str,
    *,
    segmenter=None,
    mediapipe_module=None,
    force: bool = False,
) -> dict[str, Any]:
    row = db.fetchone("SELECT hash, path FROM photos WHERE hash = ?", (photo_hash,))
    if row is None:
        raise ValueError(f"Photo not found: {photo_hash}")
    path = Path(row["path"])
    signature = source_signature(path)
    existing = db.fetchone("SELECT * FROM hair_measurements WHERE photo_hash = ?", (photo_hash,))
    owns_segmenter = False
    source_path = config.hair_source_masks_dir / f"{photo_hash}.npz"
    confidence: np.ndarray | None = None
    reasons: list[str] = []

    if not force and existing and existing["source_signature"] == signature and source_path.exists():
        with np.load(source_path) as payload:
            confidence = np.asarray(payload["confidence"], dtype=np.float32)
    else:
        if segmenter is None:
            segmenter, mediapipe_module = create_hair_segmenter(config)
            owns_segmenter = True
        try:
            with open_oriented_image(path) as image:
                rgb = np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8))
            mp = mediapipe_module
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = segmenter.segment(mp_image)
            masks = [np.asarray(mask.numpy_view(), dtype=np.float32) for mask in (result.confidence_masks or [])]
            if not masks:
                raise RuntimeError("segmenter returned no confidence mask")
            confidence = masks[1] if len(masks) > 1 else masks[0]
            if confidence.ndim == 3:
                confidence = np.squeeze(confidence)
            source_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(source_path, confidence=confidence.astype(np.float16))
        finally:
            if owns_segmenter:
                segmenter.close()

    refined, quality, mask_reasons = refine_confidence_mask(confidence)
    reasons.extend(mask_reasons)
    eligible = bool(refined.any()) and "implausible_hair_area" not in reasons
    aligned_path, aligned_sig, metrics = _write_aligned_mask(db, config, photo_hash, refined)
    if aligned_path is None:
        reasons.append("alignment_not_ready")
    now = datetime.now().isoformat(sep=" ")
    excluded = int(existing["user_excluded"]) if existing else 0
    db.execute(
        """
        INSERT INTO hair_measurements (
            photo_hash, algorithm_version, source_signature, alignment_signature,
            source_mask_path, aligned_mask_path, metrics_json, quality_score,
            eligible, user_excluded, reasons_json, computed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(photo_hash) DO UPDATE SET
            algorithm_version = excluded.algorithm_version,
            source_signature = excluded.source_signature,
            alignment_signature = excluded.alignment_signature,
            source_mask_path = excluded.source_mask_path,
            aligned_mask_path = excluded.aligned_mask_path,
            metrics_json = excluded.metrics_json,
            quality_score = excluded.quality_score,
            eligible = excluded.eligible,
            reasons_json = excluded.reasons_json,
            computed_at = excluded.computed_at,
            updated_at = excluded.updated_at
        """,
        (
            photo_hash,
            ALGORITHM_VERSION,
            signature,
            aligned_sig,
            str(source_path),
            str(aligned_path) if aligned_path else None,
            json.dumps(metrics, separators=(",", ":")),
            quality,
            int(eligible),
            excluded,
            json.dumps(reasons, separators=(",", ":")),
            now,
            now,
        ),
    )
    _invalidate_composite(config, photo_hash)
    return {"hash": photo_hash, "eligible": eligible, "quality": quality, "reasons": reasons}


def refine_confidence_mask(confidence: np.ndarray) -> tuple[np.ndarray, float, list[str]]:
    values = np.nan_to_num(np.asarray(confidence, dtype=np.float32), nan=0.0, posinf=1.0, neginf=0.0)
    values = np.clip(values, 0.0, 1.0)
    strong = values >= 0.55
    weak = values >= 0.30
    components, count = label(weak)
    keep = np.zeros_like(weak)
    minimum = max(16, int(values.size * 0.0005))
    for component in range(1, count + 1):
        candidate = components == component
        if int(candidate.sum()) >= minimum and np.any(strong & candidate):
            keep |= candidate
    if strong.any() and not keep.any():
        keep = binary_dilation(strong, iterations=1) & weak
    area_ratio = float(keep.mean())
    quality = float(values[keep].mean()) if keep.any() else 0.0
    reasons: list[str] = []
    if area_ratio < 0.002 or area_ratio > 0.65:
        reasons.append("implausible_hair_area")
    if quality < 0.55:
        reasons.append("low_hair_confidence")
    border_ratio = float(
        (keep[0].sum() + keep[-1].sum() + keep[:, 0].sum() + keep[:, -1].sum())
        / max(2 * keep.shape[0] + 2 * keep.shape[1], 1)
    )
    if border_ratio > 0.08:
        reasons.append("hair_touches_frame_edge")
    return keep, round(quality, 4), reasons


def recompute_project_hair(
    db: Database,
    config: AppConfig,
    project_id: int,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
    force: bool = False,
) -> dict[str, Any]:
    rows = db.fetchall(
        """
        SELECT p.hash
        FROM photos p JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND p.landmarks_path IS NOT NULL
        ORDER BY p.captured_at, p.hash
        """,
        (project_id,),
    )
    segmenter = mp = None
    processed = failed = 0
    failures: list[dict[str, str]] = []
    try:
        segmenter, mp = create_hair_segmenter(config)
        for index, row in enumerate(rows):
            if cancel_check:
                cancel_check()
            if progress:
                progress("hair_analysis", index + 1, len(rows), "Tracing hair silhouettes")
            try:
                analyze_photo_hair(
                    db,
                    config,
                    row["hash"],
                    segmenter=segmenter,
                    mediapipe_module=mp,
                    force=force,
                )
                processed += 1
            except Exception as exc:
                failed += 1
                failures.append({"hash": row["hash"], "error": f"{exc.__class__.__name__}: {exc}"})
    finally:
        if segmenter is not None:
            segmenter.close()
    suggestions = update_haircut_suggestions(db, config, project_id)
    return {"total": len(rows), "processed": processed, "failed": failed, "failures": failures, "suggestions": suggestions}


def refresh_project_hair_alignment(
    db: Database,
    config: AppConfig,
    project_id: int,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, int]:
    rows = db.fetchall(
        """
        SELECT m.photo_hash, m.source_mask_path
        FROM hair_measurements m JOIN project_photos pp ON pp.photo_hash = m.photo_hash
        WHERE pp.project_id = ? AND m.source_mask_path IS NOT NULL
        ORDER BY m.photo_hash
        """,
        (project_id,),
    )
    refreshed = 0
    for index, row in enumerate(rows):
        if cancel_check:
            cancel_check()
        if progress:
            progress("hair_alignment", index + 1, len(rows), "Anchoring hair silhouettes")
        source_path = Path(row["source_mask_path"])
        if not source_path.exists():
            continue
        with np.load(source_path) as payload:
            confidence = np.asarray(payload["confidence"], dtype=np.float32)
        refined, _, _ = refine_confidence_mask(confidence)
        aligned_path, aligned_sig, metrics = _write_aligned_mask(db, config, row["photo_hash"], refined)
        if aligned_path:
            db.execute(
                "UPDATE hair_measurements SET alignment_signature = ?, aligned_mask_path = ?, metrics_json = ?, updated_at = ? WHERE photo_hash = ?",
                (
                    aligned_sig,
                    str(aligned_path),
                    json.dumps(metrics, separators=(",", ":")),
                    datetime.now().isoformat(sep=" "),
                    row["photo_hash"],
                ),
            )
            _invalidate_composite(config, row["photo_hash"])
            refreshed += 1
    update_haircut_suggestions(db, config, project_id)
    return {"total": len(rows), "refreshed": refreshed}


def _write_aligned_mask(
    db: Database,
    config: AppConfig,
    photo_hash: str,
    source_mask: np.ndarray,
) -> tuple[Path | None, str | None, dict[str, float]]:
    photo = db.fetchone("SELECT path FROM photos WHERE hash = ?", (photo_hash,))
    landmark_path = config.aligned_landmarks_dir / f"{photo_hash}.npz"
    signature = alignment_signature(landmark_path)
    if photo is None or signature is None:
        return None, None, {}
    with np.load(landmark_path) as payload:
        matrix = np.asarray(payload["matrix"], dtype=np.float32)
        target_size = tuple(int(value) for value in np.asarray(payload["target_size"]).tolist())
        aligned_landmarks = np.asarray(payload["landmarks"], dtype=np.float32)
    with open_oriented_image(photo["path"]) as image:
        source_size = image.size
    source_image = Image.fromarray((source_mask.astype(np.uint8) * 255), mode="L").resize(source_size, Image.Resampling.BILINEAR)
    try:
        import cv2

        aligned = cv2.warpAffine(
            np.asarray(source_image, dtype=np.uint8),
            matrix,
            target_size,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
    except Exception:
        affine = np.vstack([matrix, [0, 0, 1]])
        inverse = np.linalg.inv(affine)
        coeff = tuple(float(value) for value in (inverse[0, 0], inverse[0, 1], inverse[0, 2], inverse[1, 0], inverse[1, 1], inverse[1, 2]))
        aligned = np.asarray(source_image.transform(target_size, Image.Transform.AFFINE, coeff, Image.Resampling.BILINEAR))
    output = config.hair_aligned_masks_dir / f"{photo_hash}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(aligned, mode="L").save(output, optimize=True)
    return output, signature, hair_metrics(aligned >= 128, aligned_landmarks)


def hair_metrics(mask: np.ndarray, landmarks: np.ndarray) -> dict[str, float]:
    binary = np.asarray(mask, dtype=bool)
    if not binary.any() or len(landmarks) <= 362:
        return {}
    left_eye = (landmarks[33, :2] + landmarks[133, :2]) / 2
    right_eye = (landmarks[263, :2] + landmarks[362, :2]) / 2
    eye_mid = (left_eye + right_eye) / 2
    interocular = max(float(np.linalg.norm(right_eye - left_eye)), 1.0)
    ys, xs = np.nonzero(binary)
    vertical_edges = np.logical_xor(binary[:, 1:], binary[:, :-1]).sum()
    horizontal_edges = np.logical_xor(binary[1:, :], binary[:-1, :]).sum()
    return {
        "area": round(float(binary.sum()) / (interocular * interocular), 5),
        "top_extent": round(float(eye_mid[1] - ys.min()) / interocular, 5),
        "lower_extent": round(float(ys.max() - eye_mid[1]) / interocular, 5),
        "left_extent": round(float(eye_mid[0] - xs.min()) / interocular, 5),
        "right_extent": round(float(xs.max() - eye_mid[0]) / interocular, 5),
        "perimeter": round(float(vertical_edges + horizontal_edges) / interocular, 5),
    }


def project_hair_revision(db: Database, config: AppConfig, project_id: int) -> str:
    digest = hashlib.sha256()
    rows = db.fetchall(
        """
        SELECT p.hash, p.captured_at, p.skipped, m.algorithm_version, m.source_signature,
               COALESCE(m.alignment_signature, ''), m.eligible, m.user_excluded, m.updated_at
        FROM hair_measurements m
        JOIN photos p ON p.hash = m.photo_hash
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? ORDER BY p.hash
        """,
        (project_id,),
    )
    for row in rows:
        digest.update("|".join(str(value) for value in row).encode("utf-8"))
    events = db.fetchall(
        "SELECT id, event_date, source, status, COALESCE(score, 0), updated_at FROM haircut_events WHERE project_id = ? ORDER BY id",
        (project_id,),
    )
    for event in events:
        digest.update("|".join(str(value) for value in event).encode("utf-8"))
    project = db.fetchone("SELECT canonical_landmarks_path FROM projects WHERE id = ?", (project_id,))
    if project and project["canonical_landmarks_path"]:
        path = Path(project["canonical_landmarks_path"])
        if path.exists():
            stat = path.stat()
            digest.update(f"{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8"))
    return digest.hexdigest()


def get_project_hair(db: Database, config: AppConfig, project_id: int) -> dict[str, Any]:
    project = db.fetchone("SELECT id, canonical_landmarks_path FROM projects WHERE id = ?", (project_id,))
    if project is None:
        raise ValueError("Project not found")
    total = int(db.fetchone(
        "SELECT COUNT(*) AS n FROM photos p JOIN project_photos pp ON pp.photo_hash = p.hash WHERE pp.project_id = ? AND p.landmarks_path IS NOT NULL",
        (project_id,),
    )["n"])
    rows = _hair_rows(db, project_id, include_excluded=True)
    if not rows:
        return {
            "status": "not_ready" if total else "insufficient",
            "analysis_version": ALGORITHM_VERSION,
            "analysis_revision": None,
            "coverage": {"available": 0, "included": 0, "excluded": 0, "total_photos": total},
            "face_outline": [],
            "frames": [],
            "haircuts": [],
            "latest_export": None,
        }
    revision = project_hair_revision(db, config, project_id)
    stale = any(_row_alignment_stale(config, row) for row in rows)
    frames = []
    used_days: set[str] = set()
    for row in rows:
        day = str(row["captured_at"])[:10]
        if day in used_days:
            continue
        used_days.add(day)
        reasons = _json_list(row["reasons_json"])
        frames.append(
            {
                "hash": row["hash"],
                "date": day,
                "captured_at": str(row["captured_at"]),
                "quality": round(float(row["hair_quality"] or 0), 3),
                "eligible": bool(row["eligible"]),
                "excluded": bool(row["user_excluded"]),
                "reasons": reasons,
                "thumb_url": f"/api/photos/{row['hash']}/thumb",
                "source_url": f"/api/photos/{row['hash']}/image",
                "composite_url": f"/api/photos/{row['hash']}/hair-composite.png?v={revision[:10]}",
            }
        )
    haircuts = [dict(row) for row in db.fetchall(
        "SELECT id, event_date, first_after_photo_hash, source, status, score FROM haircut_events WHERE project_id = ? AND status IN ('suggested', 'confirmed') ORDER BY event_date, id",
        (project_id,),
    )]
    latest = db.fetchone("SELECT * FROM hair_exports WHERE project_id = ? AND status = 'done' ORDER BY id DESC LIMIT 1", (project_id,))
    latest_export = None
    if latest:
        export_config = _json_dict(latest["config_json"])
        latest_export = {
            "id": int(latest["id"]),
            "status": latest["status"],
            "stale": latest["analysis_revision"] != revision,
            "file_url": f"/api/hair-exports/{latest['id']}/file",
            "playback_url": f"/api/hair-exports/{latest['id']}/playback.mp4",
            "finished_at": str(latest["finished_at"]) if latest["finished_at"] else None,
            "config": export_config,
        }
    included = sum(frame["eligible"] and not frame["excluded"] for frame in frames)
    return {
        "status": "stale" if stale else "ready",
        "analysis_version": ALGORITHM_VERSION,
        "analysis_revision": revision,
        "coverage": {
            "available": len(frames),
            "included": included,
            "excluded": sum(frame["excluded"] for frame in frames),
            "total_photos": total,
        },
        "face_outline": canonical_face_outline(project["canonical_landmarks_path"]),
        "frames": frames,
        "haircuts": haircuts,
        "latest_export": latest_export,
    }


def canonical_face_outline(canonical_path: str | None) -> list[list[float]]:
    if not canonical_path or not Path(canonical_path).exists():
        return []
    with np.load(canonical_path) as payload:
        points = np.asarray(payload["landmarks"], dtype=np.float64)
    if len(points) <= max(FACE_OVAL):
        return []
    oval = points[list(FACE_OVAL), :2]
    return np.round(oval, 6).tolist()


def set_hair_excluded(db: Database, photo_hash: str, excluded: bool) -> None:
    if db.fetchone("SELECT photo_hash FROM hair_measurements WHERE photo_hash = ?", (photo_hash,)) is None:
        raise ValueError("Hair analysis is not ready for this photo")
    db.execute(
        "UPDATE hair_measurements SET user_excluded = ?, updated_at = ? WHERE photo_hash = ?",
        (int(excluded), datetime.now().isoformat(sep=" "), photo_hash),
    )


def create_haircut_event(db: Database, project_id: int, event_date: str) -> dict[str, Any]:
    parsed = date.fromisoformat(event_date).isoformat()
    now = datetime.now().isoformat(sep=" ")
    event_id = db.execute(
        "INSERT INTO haircut_events (project_id, event_date, source, status, created_at, updated_at) VALUES (?, ?, 'manual', 'confirmed', ?, ?)",
        (project_id, parsed, now, now),
    )
    return dict(db.fetchone("SELECT * FROM haircut_events WHERE id = ?", (event_id,)))


def update_haircut_event(
    db: Database,
    event_id: int,
    *,
    event_date: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    row = db.fetchone("SELECT * FROM haircut_events WHERE id = ?", (event_id,))
    if row is None:
        raise ValueError("Haircut event not found")
    next_date = date.fromisoformat(event_date).isoformat() if event_date else row["event_date"]
    next_status = status or row["status"]
    db.execute(
        "UPDATE haircut_events SET event_date = ?, status = ?, updated_at = ? WHERE id = ?",
        (next_date, next_status, datetime.now().isoformat(sep=" "), event_id),
    )
    return dict(db.fetchone("SELECT * FROM haircut_events WHERE id = ?", (event_id,)))


def update_haircut_suggestions(db: Database, config: AppConfig, project_id: int) -> int:
    rows = [row for row in _hair_rows(db, project_id) if bool(row["eligible"]) and not bool(row["user_excluded"])]
    if len(rows) < 2:
        return 0
    changes: list[float] = []
    pairs: list[dict[str, Any]] = []
    for index in range(1, len(rows)):
        old, new = rows[index - 1], rows[index]
        old_mask = _load_binary_mask(old["aligned_mask_path"])
        new_mask = _load_binary_mask(new["aligned_mask_path"])
        if old_mask is None or new_mask is None or old_mask.shape != new_mask.shape:
            continue
        overlap = mask_iou(old_mask, new_mask)
        old_metrics = _json_dict(old["metrics_json"])
        new_metrics = _json_dict(new["metrics_json"])
        old_area = max(float(old_metrics.get("area", 0)), 1e-6)
        size_drop = (old_area - float(new_metrics.get("area", old_area))) / old_area
        old_extent = sum(float(old_metrics.get(key, 0)) for key in ("top_extent", "lower_extent", "left_extent", "right_extent"))
        new_extent = sum(float(new_metrics.get(key, 0)) for key in ("top_extent", "lower_extent", "left_extent", "right_extent"))
        extent_drop = (old_extent - new_extent) / max(old_extent, 1e-6)
        change = 1.0 - overlap
        changes.append(change)
        pairs.append({"index": index, "old": old, "new": new, "iou": overlap, "size_drop": size_drop, "extent_drop": extent_drop, "change": change})
    if not pairs:
        return 0
    center = float(np.median(changes))
    scale = 1.4826 * float(np.median(np.abs(np.asarray(changes) - center)))
    threshold = 0.18 if len(changes) < 5 else max(0.18, center + 2.5 * max(scale, 0.01))
    candidates: dict[str, tuple[str, float]] = {}
    provisional: dict[str, tuple[str, float]] = {}
    for pair in pairs:
        if pair["iou"] > 0.82 or pair["change"] < threshold:
            continue
        if pair["size_drop"] < 0.08 and pair["extent_drop"] < 0.08:
            continue
        index = pair["index"]
        new = pair["new"]
        future = [candidate for candidate in rows[index + 1:index + 3] if (date.fromisoformat(str(candidate["captured_at"])[:10]) - date.fromisoformat(str(new["captured_at"])[:10])).days <= 21]
        persistent = False
        new_mask = _load_binary_mask(new["aligned_mask_path"])
        old_mask = _load_binary_mask(pair["old"]["aligned_mask_path"])
        for candidate in future:
            future_mask = _load_binary_mask(candidate["aligned_mask_path"])
            if future_mask is not None and mask_iou(new_mask, future_mask) >= mask_iou(old_mask, future_mask):
                persistent = True
                break
        score = round((pair["change"] - center) / max(scale, 0.05), 3)
        item = (str(new["captured_at"])[:10], score)
        if persistent:
            candidates[new["hash"]] = item
        elif not future:
            provisional[new["hash"]] = item
    now = datetime.now().isoformat(sep=" ")
    with db.connect() as conn:
        existing = {
            row["first_after_photo_hash"]: row
            for row in conn.execute("SELECT * FROM haircut_events WHERE project_id = ? AND source = 'automatic'", (project_id,)).fetchall()
        }
        for photo_hash, (event_date, score) in {**provisional, **candidates}.items():
            desired = "suggested" if photo_hash in candidates else "provisional"
            prior = existing.get(photo_hash)
            if prior and prior["status"] in {"confirmed", "dismissed"}:
                continue
            if prior:
                conn.execute(
                    "UPDATE haircut_events SET event_date = ?, status = ?, score = ?, updated_at = ? WHERE id = ?",
                    (event_date, desired, score, now, prior["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO haircut_events (project_id, event_date, first_after_photo_hash, source, status, score, created_at, updated_at) VALUES (?, ?, ?, 'automatic', ?, ?, ?, ?)",
                    (project_id, event_date, photo_hash, desired, score, now, now),
                )
        active_hashes = set(candidates) | set(provisional)
        for photo_hash, prior in existing.items():
            if photo_hash not in active_hashes and prior["status"] in {"provisional", "suggested"}:
                conn.execute("DELETE FROM haircut_events WHERE id = ?", (prior["id"],))
    return len(candidates)


def mask_iou(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 1.0


def create_hair_export(db: Database, config: AppConfig, project_id: int, payload: dict[str, Any]) -> int:
    revision = project_hair_revision(db, config, project_id)
    now = datetime.now().isoformat(sep=" ")
    return db.execute(
        "INSERT INTO hair_exports (project_id, analysis_revision, config_json, started_at, status) VALUES (?, ?, ?, ?, 'queued')",
        (project_id, revision, json.dumps(payload, separators=(",", ":")), now),
    )


def render_hair_export(
    db: Database,
    config: AppConfig,
    project_id: int,
    export_id: int,
    payload: dict[str, Any],
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    start = payload.get("start_date")
    end = payload.get("end_date")
    seconds = float(payload.get("seconds_per_selfie", 1.0))
    width = int(payload.get("width", DEFAULT_WIDTH))
    height = int(payload.get("height", DEFAULT_HEIGHT))
    rows = [
        row for row in _hair_rows(db, project_id)
        if bool(row["eligible"]) and not bool(row["user_excluded"])
        and (not start or str(row["captured_at"])[:10] >= start)
        and (not end or str(row["captured_at"])[:10] <= end)
    ]
    if len(rows) < 2:
        raise RuntimeError("At least two included hair frames are required")
    output = config.exports_dir / f"hair-timeline-{project_id}-{export_id}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.mp4")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(DEFAULT_FPS), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary),
    ]
    db.execute("UPDATE hair_exports SET status = 'running' WHERE id = ?", (export_id,))
    confirmed = {str(row["event_date"]): True for row in db.fetchall(
        "SELECT event_date FROM haircut_events WHERE project_id = ? AND status = 'confirmed'", (project_id,)
    )}
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    hold_frames = max(1, int(round(DEFAULT_FPS * seconds * 0.6)))
    transition_frames = max(1, int(round(DEFAULT_FPS * seconds * 0.4)))
    total_frames = len(rows) * hold_frames + (len(rows) - 1) * transition_frames
    written = 0
    try:
        current_mask, base = _canvas_assets(db, config, rows[0]["hash"], width, height)
        current_sdf = _signed_distance(current_mask)
        for index, row in enumerate(rows):
            if cancel_check:
                cancel_check()
            day = str(row["captured_at"])[:10]
            keyframe = _compose_canvas(base, current_mask, day, confirmed.get(day, False))
            for _ in range(hold_frames):
                _write_video_frame(process, keyframe)
                written += 1
            if index + 1 < len(rows):
                next_row = rows[index + 1]
                next_mask, next_base = _canvas_assets(db, config, next_row["hash"], width, height)
                next_sdf = _signed_distance(next_mask)
                next_day = str(next_row["captured_at"])[:10]
                for step in range(1, transition_frames + 1):
                    amount = step / (transition_frames + 1)
                    interpolated = ((1.0 - amount) * current_sdf + amount * next_sdf) >= 0
                    frame = _compose_canvas(base if amount < 0.5 else next_base, interpolated, day if amount < 0.5 else next_day, False)
                    _write_video_frame(process, frame)
                    written += 1
                current_mask, base, current_sdf = next_mask, next_base, next_sdf
            if progress:
                progress("hair_export", written, total_frames, f"Animating {day}")
        assert process.stdin is not None
        process.stdin.close()
        stderr = process.stderr.read() if process.stderr else b""
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "FFmpeg failed")
        temporary.replace(output)
        now = datetime.now().isoformat(sep=" ")
        db.execute(
            "UPDATE hair_exports SET output_path = ?, status = 'done', finished_at = ?, error = NULL WHERE id = ?",
            (str(output), now, export_id),
        )
        return {"export_id": export_id, "output_path": str(output), "frames": written}
    except Exception as exc:
        if process.poll() is None:
            process.kill()
        temporary.unlink(missing_ok=True)
        db.execute(
            "UPDATE hair_exports SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
            (f"{exc.__class__.__name__}: {exc}", datetime.now().isoformat(sep=" "), export_id),
        )
        raise


def ensure_hair_composite(db: Database, config: AppConfig, photo_hash: str) -> Path:
    row = db.fetchone(
        "SELECT p.captured_at, m.updated_at FROM photos p JOIN hair_measurements m ON m.photo_hash = p.hash WHERE p.hash = ?",
        (photo_hash,),
    )
    if row is None:
        raise ValueError("Hair analysis is not ready for this photo")
    output = config.hair_composites_dir / f"{photo_hash}.png"
    if output.exists() and output.stat().st_mtime >= _timestamp(row["updated_at"]):
        return output
    mask, base = _canvas_assets(db, config, photo_hash, DEFAULT_WIDTH, DEFAULT_HEIGHT)
    image = _compose_canvas(base, mask, str(row["captured_at"])[:10], False)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)
    return output


def _canvas_assets(db: Database, config: AppConfig, photo_hash: str, width: int, height: int) -> tuple[np.ndarray, Image.Image]:
    row = db.fetchone("SELECT aligned_mask_path FROM hair_measurements WHERE photo_hash = ?", (photo_hash,))
    landmark_path = config.aligned_landmarks_dir / f"{photo_hash}.npz"
    if row is None or not row["aligned_mask_path"] or not Path(row["aligned_mask_path"]).exists() or not landmark_path.exists():
        raise RuntimeError(f"Aligned hair mask is missing for {photo_hash}")
    project = db.fetchone(
        """
        SELECT p.canonical_landmarks_path
        FROM projects p JOIN project_photos pp ON pp.project_id = p.id
        WHERE pp.photo_hash = ? AND p.canonical_landmarks_path IS NOT NULL
        ORDER BY p.id LIMIT 1
        """,
        (photo_hash,),
    )
    if project and project["canonical_landmarks_path"] and Path(project["canonical_landmarks_path"]).exists():
        landmarks, _ = canonical_pixels(Path(project["canonical_landmarks_path"]))
        landmarks = np.asarray(landmarks, dtype=np.float64)
    else:
        with np.load(landmark_path) as payload:
            landmarks = np.asarray(payload["landmarks"], dtype=np.float64)
    mask = np.asarray(Image.open(row["aligned_mask_path"]).convert("L"), dtype=np.uint8)
    transform = _canvas_transform(landmarks, width, height)
    warped = _warp_array(mask, transform, (width, height)) >= 128
    base = Image.new("L", (width, height), 255)
    oval = np.asarray([landmarks[index, :2] for index in FACE_OVAL if index < len(landmarks)], dtype=np.float64)
    if len(oval) >= 3:
        transformed = _apply_affine(oval, transform)
        points = [tuple(map(float, point)) for point in transformed]
        ImageDraw.Draw(base).line([*points, points[0]], fill=0, width=max(3, width // 180), joint="curve")
    return warped, base


def _canvas_transform(landmarks: np.ndarray, width: int, height: int) -> np.ndarray:
    left_eye = (landmarks[33, :2] + landmarks[133, :2]) / 2
    right_eye = (landmarks[263, :2] + landmarks[362, :2]) / 2
    eye_mid = (left_eye + right_eye) / 2
    interocular = max(float(np.linalg.norm(right_eye - left_eye)), 1.0)
    scale = (width * 0.18) / interocular
    return np.array(
        [[scale, 0.0, width * 0.5 - scale * eye_mid[0]], [0.0, scale, height * 0.39 - scale * eye_mid[1]]],
        dtype=np.float32,
    )


def _warp_array(values: np.ndarray, matrix: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    try:
        import cv2

        return cv2.warpAffine(values, matrix, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    except Exception:
        affine = np.vstack([matrix, [0, 0, 1]])
        inverse = np.linalg.inv(affine)
        coeff = tuple(float(value) for value in (inverse[0, 0], inverse[0, 1], inverse[0, 2], inverse[1, 0], inverse[1, 1], inverse[1, 2]))
        return np.asarray(Image.fromarray(values).transform(size, Image.Transform.AFFINE, coeff, Image.Resampling.BILINEAR))


def _apply_affine(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return points @ matrix[:, :2].T + matrix[:, 2]


def _signed_distance(mask: np.ndarray) -> np.ndarray:
    binary = np.asarray(mask, dtype=bool)
    return distance_transform_edt(binary).astype(np.float32) - distance_transform_edt(~binary).astype(np.float32)


def _compose_canvas(base: Image.Image, hair: np.ndarray, day: str, haircut: bool) -> Image.Image:
    image = base.copy()
    pixels = np.asarray(image).copy()
    pixels[np.asarray(hair, dtype=bool)] = 0
    image = Image.fromarray(pixels, mode="L")
    draw = ImageDraw.Draw(image)
    font = _font(max(18, image.width // 32))
    small = _font(max(14, image.width // 45))
    draw.text((image.width * 0.06, image.height * 0.91), day, fill=0, font=font)
    if haircut:
        draw.text((image.width * 0.06, image.height * 0.955), "HAIRCUT", fill=0, font=small)
    return image


def _font(size: int):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _write_video_frame(process: subprocess.Popen, image: Image.Image) -> None:
    if process.stdin is None:
        raise RuntimeError("FFmpeg input closed unexpectedly")
    process.stdin.write(np.asarray(image.convert("RGB"), dtype=np.uint8).tobytes())


def _hair_rows(db: Database, project_id: int, include_excluded: bool = False):
    condition = "" if include_excluded else "AND m.user_excluded = 0"
    return db.fetchall(
        f"""
        SELECT p.hash, p.captured_at, p.skipped, m.eligible, m.user_excluded,
               m.aligned_mask_path, m.alignment_signature, m.metrics_json,
               m.quality_score AS hair_quality, m.reasons_json, m.updated_at
        FROM hair_measurements m
        JOIN photos p ON p.hash = m.photo_hash
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND p.skipped = 0 {condition}
        ORDER BY p.captured_at, p.hash
        """,
        (project_id,),
    )


def _row_alignment_stale(config: AppConfig, row) -> bool:
    expected = alignment_signature(config.aligned_landmarks_dir / f"{row['hash']}.npz")
    return expected is None or expected != row["alignment_signature"]


def _load_binary_mask(path: str | None) -> np.ndarray | None:
    if not path or not Path(path).exists():
        return None
    return np.asarray(Image.open(path).convert("L"), dtype=np.uint8) >= 128


def _json_dict(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
        return [str(item) for item in value] if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


def _invalidate_composite(config: AppConfig, photo_hash: str) -> None:
    (config.hair_composites_dir / f"{photo_hash}.png").unlink(missing_ok=True)


def _timestamp(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0
