from __future__ import annotations

import hashlib
import json
import logging
import math
import subprocess
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import binary_fill_holes, distance_transform_edt, label

from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.pipeline.face_shape import FACE_OVAL
from selfietl.pipeline.images import open_oriented_image
from selfietl.pipeline.canonical import canonical_pixels


ALGORITHM_VERSION = "hair-v2"
SEGMENTATION_VERSION = "hair-segmenter-float32-1"
HAIR_MODEL_NAME = "hair_segmenter.tflite"
HAIR_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/image_segmenter/"
    "hair_segmenter/float32/1/hair_segmenter.tflite"
)
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1350
DEFAULT_FPS = 30

Progress = Callable[[str, int, int, str], None]
CancelCheck = Callable[[], None]
logger = logging.getLogger(__name__)
BLOCKING_REASONS = {
    "implausible_hair_area", "low_hair_confidence", "uncertain_hair_boundary",
    "hair_touches_frame_edge", "alignment_crops_hair", "alignment_not_ready",
    "alignment_distorts_hair",
    "invalid_face_landmarks", "head_pose", "low_photo_quality", "hair_analysis_failed",
}


def ensure_hair_model(config: AppConfig) -> Path:
    path = config.models_dir / HAIR_MODEL_NAME
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.download")
    try:
        with urllib.request.urlopen(HAIR_MODEL_URL, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        with temporary.open("rb") as downloaded:
            if downloaded.read(8)[4:8] != b"TFL3":
                raise RuntimeError("downloaded file is not a TensorFlow Lite model")
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


def _file_signature(path: Path, version: str) -> str:
    stat = path.stat()
    return hashlib.sha256(
        f"{version}|{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
    ).hexdigest()


def source_signature(path: Path, landmarks_path: Path | None = None) -> str:
    source = _file_signature(path, SEGMENTATION_VERSION)
    landmarks = _file_signature(landmarks_path, "landmarks") if landmarks_path and landmarks_path.exists() else "missing"
    return hashlib.sha256(f"{ALGORITHM_VERSION}|{source}|{landmarks}".encode()).hexdigest()


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
    segmenter_factory: Callable[[], tuple[Any, Any]] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    row = db.fetchone("SELECT * FROM photos WHERE hash = ?", (photo_hash,))
    if row is None:
        raise ValueError(f"Photo not found: {photo_hash}")
    path = Path(row["path"])
    landmarks_path = Path(row["landmarks_path"]) if row["landmarks_path"] else None
    signature = source_signature(path, landmarks_path)
    existing = db.fetchone("SELECT * FROM hair_measurements WHERE photo_hash = ?", (photo_hash,))
    owns_segmenter = False
    source_path = config.hair_source_masks_dir / f"{photo_hash}.npz"
    confidence: np.ndarray | None = None
    reasons: list[str] = []

    if not force and source_path.exists():
        try:
            with np.load(source_path, allow_pickle=False) as payload:
                cached_signature = str(payload["segmentation_signature"].item()) if "segmentation_signature" in payload else None
                legacy_current = existing and existing["source_signature"] == _file_signature(path, "hair-v1")
                if cached_signature == _file_signature(path, SEGMENTATION_VERSION) or legacy_current or (existing and existing["source_signature"] == signature):
                    confidence = np.asarray(payload["confidence"], dtype=np.float32)
                    if confidence.ndim != 2 or not confidence.size or not np.isfinite(confidence).all():
                        confidence = None
        except (OSError, ValueError, KeyError):
            logger.warning("Rebuilding unreadable hair cache for %s", photo_hash)
    if confidence is None:
        if segmenter is None:
            segmenter, mediapipe_module = segmenter_factory() if segmenter_factory else create_hair_segmenter(config)
            owns_segmenter = segmenter_factory is None
        try:
            with open_oriented_image(path) as image:
                rgb = np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8))
            mp = mediapipe_module
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = segmenter.segment(mp_image)
            masks = result.confidence_masks or []
            if len(masks) != 2:
                raise RuntimeError("Hair model must return background and hair confidence masks")
            # The pinned model's categories are background=0, hair=1.
            confidence = np.asarray(masks[1].numpy_view(), dtype=np.float32).copy()
            if confidence.ndim == 3 and confidence.shape[-1] == 1:
                confidence = confidence[..., 0]
            if confidence.ndim != 2 or not confidence.size or not np.isfinite(confidence).all():
                raise RuntimeError("Hair model returned an invalid confidence mask")
            source_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = source_path.with_name(f".{photo_hash}.{uuid.uuid4().hex}.npz")
            try:
                np.savez_compressed(temporary, confidence=confidence.astype(np.float16), segmentation_signature=_file_signature(path, SEGMENTATION_VERSION))
                temporary.replace(source_path)
            finally:
                temporary.unlink(missing_ok=True)
        finally:
            if owns_segmenter:
                segmenter.close()

    source_landmarks = _source_landmarks(landmarks_path, confidence.shape)
    if source_landmarks is None:
        reasons.append("invalid_face_landmarks")
    refined, quality, mask_reasons = refine_confidence_mask(confidence, source_landmarks)
    reasons.extend(mask_reasons)
    if source_landmarks is not None:
        _ensure_hair_alignment(db, config, row)
    aligned_path, aligned_sig, metrics = _write_aligned_mask(db, config, photo_hash, refined)
    if aligned_path is None:
        reasons.append("alignment_not_ready")
    elif not metrics:
        reasons.append("invalid_face_landmarks")
    elif metrics.get("retained_area", 1.0) < 0.90:
        reasons.append("alignment_crops_hair")
    if metrics.get("alignment_anisotropy", 1.0) > 1.08:
        reasons.append("alignment_distorts_hair")
    for key, limit in (("yaw", 20), ("pitch", 20), ("roll", 25)):
        if row[key] is not None and (not math.isfinite(float(row[key])) or abs(float(row[key])) > limit):
            reasons.append("head_pose")
            break
    if row["quality_score"] is not None and float(row["quality_score"]) < 0.6:
        reasons.append("low_photo_quality")
    reasons = list(dict.fromkeys(reasons))
    eligible = bool(refined.any()) and bool(metrics) and not BLOCKING_REASONS.intersection(reasons)
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


def _source_landmarks(path: Path | None, shape: tuple[int, ...]) -> np.ndarray | None:
    if path is None or not path.exists():
        return None
    try:
        with np.load(path, allow_pickle=False) as payload:
            points = np.asarray(payload["landmarks"], dtype=np.float32)[:, :2]
        if points.ndim != 2 or len(points) <= max(FACE_OVAL) or not np.isfinite(points).all():
            return None
        points = points * np.array([shape[1], shape[0]], dtype=np.float32)
        _eye_geometry(points)
        return points
    except (OSError, ValueError, KeyError, IndexError):
        return None


def _eye_geometry(landmarks: np.ndarray) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    points = np.asarray(landmarks, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] < 2 or len(points) <= 362 or not np.isfinite(points[:, :2]).all():
        raise ValueError("Invalid face landmarks")
    left = (points[33, :2] + points[133, :2]) / 2
    right = (points[263, :2] + points[362, :2]) / 2
    distance = float(np.linalg.norm(right - left))
    if distance < 4:
        raise ValueError("Eyes are too close to measure hair")
    horizontal = (right - left) / distance
    return (left + right) / 2, distance, horizontal, np.array([-horizontal[1], horizontal[0]])


def refine_confidence_mask(confidence: np.ndarray, landmarks: np.ndarray | None = None) -> tuple[np.ndarray, float, list[str]]:
    values = np.asarray(confidence, dtype=np.float32)
    if values.ndim != 2 or not values.size:
        raise ValueError("Hair confidence mask must be a nonempty 2D array")
    # Model output is often full-resolution. Bound cleanup cost in model space;
    # scale landmarks along with it, keeping rectangular images isotropic.
    if max(values.shape) > 1024:
        height, width = values.shape
        scale = 1024 / max(height, width)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        values = np.asarray(Image.fromarray(values).resize(size, Image.Resampling.BILINEAR))
        if landmarks is not None:
            landmarks = np.asarray(landmarks).copy() * np.array([size[0] / width, size[1] / height])
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    values = np.clip(values, 0.0, 1.0)
    strong = values >= 0.65
    weak = values >= 0.35
    near_head = np.ones_like(weak)
    minimum = max(16, int(values.size * 0.0005))
    if landmarks is not None:
        midpoint, distance, horizontal, vertical = _eye_geometry(landmarks)
        yy, xx = np.indices(values.shape, dtype=np.float32)
        x = ((xx - midpoint[0]) * horizontal[0] + (yy - midpoint[1]) * horizontal[1]) / distance
        y = ((xx - midpoint[0]) * vertical[0] + (yy - midpoint[1]) * vertical[1]) / distance
        # Components must originate at the scalp/temples. Connected long hair
        # remains intact; unrelated people, background and beard blobs do not.
        near_head = (np.abs(x) <= 1.9) & (y >= -4.0) & (y <= 0.55)
        weak &= (np.abs(x) <= 4.0) & (y >= -4.5) & (y <= 6.0)
        minimum = max(8, int(distance * distance * 0.015))
    components, count = label(weak, structure=np.ones((3, 3)))
    sizes = np.bincount(components.ravel(), minlength=count + 1)
    cores = np.bincount(components[strong & near_head], minlength=count + 1)
    accepted = (sizes >= minimum) & (cores >= max(3, minimum // 4))
    accepted[0] = False
    keep = accepted[components]
    # Fill only small internal pinholes, preserving real gaps and wispy edges.
    holes, hole_count = label(binary_fill_holes(keep) & ~keep)
    hole_sizes = np.bincount(holes.ravel(), minlength=hole_count + 1)
    small_holes = hole_sizes <= max(4, minimum // 3)
    small_holes[0] = False
    keep |= small_holes[holes]
    area_ratio = float(keep.mean())
    quality = float(values[keep].mean()) if keep.any() else 0.0
    reasons: list[str] = []
    relative_area = float(keep.sum()) / (distance * distance) if landmarks is not None else None
    if (relative_area is not None and not 0.015 <= relative_area <= 45) or (relative_area is None and (area_ratio < 0.002 or area_ratio > 0.65)):
        reasons.append("implausible_hair_area")
    if quality < 0.65:
        reasons.append("low_hair_confidence")
    if keep.any() and float(np.mean(values[keep] < 0.65)) > 0.45:
        reasons.append("uncertain_hair_boundary")
    border_pixels = keep[0].sum() + keep[-1].sum() + keep[:, 0].sum() + keep[:, -1].sum()
    if border_pixels >= max(3, int(min(keep.shape) * 0.005)):
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
        SELECT p.hash, p.path, p.landmarks_path
        FROM photos p JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND p.skipped = 0 AND p.landmarks_path IS NOT NULL
        ORDER BY p.captured_at, p.hash
        """,
        (project_id,),
    )
    segmenter = mp = None
    processed = failed = 0
    failures: list[dict[str, str]] = []
    def get_segmenter():
        nonlocal segmenter, mp
        if segmenter is None:
            segmenter, mp = create_hair_segmenter(config)
        return segmenter, mp

    try:
        for index, row in enumerate(rows):
            if cancel_check:
                cancel_check()
            if progress:
                progress("hair_analysis", index, len(rows), "Analyzing hair")
            try:
                analyze_photo_hair(
                    db,
                    config,
                    row["hash"],
                    segmenter_factory=get_segmenter,
                    force=force,
                )
                processed += 1
            except Exception as exc:
                failed += 1
                failures.append({"hash": row["hash"], "error": f"{exc.__class__.__name__}: {exc}"})
                _record_hair_failure(db, config, row)
    finally:
        if segmenter is not None:
            segmenter.close()
    suggestions = update_haircut_suggestions(db, config, project_id)
    if progress:
        progress("hair_analysis", len(rows), len(rows), "Hair analysis complete")
    return {"total": len(rows), "processed": processed, "failed": failed, "failures": failures, "suggestions": suggestions}


def refresh_project_hair_alignment(
    db: Database,
    config: AppConfig,
    project_id: int,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, int]:
    result = recompute_project_hair(db, config, project_id, progress, cancel_check)
    return {"total": result["total"], "refreshed": result["processed"], "failed": result["failed"]}


def _record_hair_failure(db: Database, config: AppConfig, row) -> None:
    now = datetime.now().isoformat(sep=" ")
    try:
        signature = source_signature(Path(row["path"]), Path(row["landmarks_path"]))
    except OSError:
        signature = "missing-source"
    db.execute(
        """INSERT INTO hair_measurements (photo_hash, algorithm_version, source_signature,
               quality_score, eligible, reasons_json, computed_at, updated_at)
           VALUES (?, ?, ?, 0, 0, '["hair_analysis_failed"]', ?, ?)
           ON CONFLICT(photo_hash) DO UPDATE SET algorithm_version = excluded.algorithm_version,
               source_signature = excluded.source_signature, aligned_mask_path = NULL,
               alignment_signature = NULL, metrics_json = '{}', quality_score = 0, eligible = 0,
               reasons_json = excluded.reasons_json, computed_at = excluded.computed_at, updated_at = excluded.updated_at""",
        (row["hash"], ALGORITHM_VERSION, signature, now, now),
    )
    _invalidate_composite(config, row["hash"])


def _ensure_hair_alignment(db: Database, config: AppConfig, photo) -> None:
    from selfietl.pipeline.align import align_photo, aligned_path
    from selfietl.pipeline.canonical import compute_canonical_face

    project = db.fetchone("""SELECT p.id, p.canonical_landmarks_path FROM projects p
                           JOIN project_photos pp ON pp.project_id = p.id WHERE pp.photo_hash = ?
                           ORDER BY p.id LIMIT 1""", (photo["hash"],))
    if project is None:
        return
    canonical = Path(project["canonical_landmarks_path"]) if project["canonical_landmarks_path"] else None
    if canonical is None or not canonical.exists():
        canonical = compute_canonical_face(db, config, int(project["id"]))
    landmarks = Path(photo["landmarks_path"])
    aligned = config.aligned_landmarks_dir / f"{photo['hash']}.npz"
    newest_input = max(canonical.stat().st_mtime_ns, landmarks.stat().st_mtime_ns, Path(photo["path"]).stat().st_mtime_ns)
    if aligned.exists() and aligned.stat().st_mtime_ns >= newest_input:
        return
    target_landmarks, target_size = canonical_pixels(canonical)
    align_photo(source_path=Path(photo["path"]), landmarks_path=landmarks, target_landmarks=target_landmarks,
                target_size=target_size, output_path=aligned_path(config, photo["hash"]),
                aligned_landmarks_path=aligned, mode=config.alignment.mode,
                quality=config.alignment.output_quality, preserve_exif=config.alignment.preserve_exif)


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
    if matrix.shape != (2, 3) or not np.isfinite(matrix).all() or len(target_size) != 2 or min(target_size) <= 0:
        raise ValueError("Invalid hair alignment transform")
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
    temporary = output.with_name(f".{photo_hash}.{uuid.uuid4().hex}.png")
    try:
        Image.fromarray(aligned).save(temporary, optimize=True)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    metrics = hair_metrics(aligned >= 128, aligned_landmarks)
    expected_area = source_mask.sum() * (source_size[0] / source_mask.shape[1]) * (source_size[1] / source_mask.shape[0]) * abs(float(np.linalg.det(matrix[:, :2])))
    if metrics and expected_area > 0:
        metrics["retained_area"] = round(min(1.0, float(np.count_nonzero(aligned >= 128)) / expected_area), 5)
        singular_values = np.linalg.svd(matrix[:, :2], compute_uv=False)
        metrics["alignment_anisotropy"] = round(float(singular_values.max() / max(singular_values.min(), 1e-6)), 5)
    return output, signature, metrics


def hair_metrics(mask: np.ndarray, landmarks: np.ndarray) -> dict[str, float]:
    binary = np.asarray(mask, dtype=bool)
    if binary.ndim != 2 or not binary.any():
        return {}
    try:
        eye_mid, interocular, horizontal, vertical = _eye_geometry(landmarks)
    except ValueError:
        return {}
    ys, xs = np.nonzero(binary)
    x = ((xs - eye_mid[0]) * horizontal[0] + (ys - eye_mid[1]) * horizontal[1]) / interocular
    y = ((xs - eye_mid[0]) * vertical[0] + (ys - eye_mid[1]) * vertical[1]) / interocular
    vertical_edges = np.logical_xor(binary[:, 1:], binary[:, :-1]).sum()
    horizontal_edges = np.logical_xor(binary[1:, :], binary[:-1, :]).sum()
    return {
        "area": round(float(binary.sum()) / (interocular * interocular), 5),
        "top_extent": round(max(0.0, -float(np.quantile(y, 0.01))), 5),
        "lower_extent": round(max(0.0, float(np.quantile(y, 0.99))), 5),
        "left_extent": round(max(0.0, -float(np.quantile(x, 0.01))), 5),
        "right_extent": round(max(0.0, float(np.quantile(x, 0.99))), 5),
        "crown_area": round(float(np.count_nonzero(y < -0.5)) / interocular ** 2, 5),
        "side_area": round(float(np.count_nonzero(np.abs(x) > 0.65)) / interocular ** 2, 5),
        "perimeter": round(float(vertical_edges + horizontal_edges) / interocular, 5),
    }


def project_hair_revision(db: Database, config: AppConfig, project_id: int) -> str:
    digest = hashlib.sha256(ALGORITHM_VERSION.encode())
    rows = db.fetchall(
        """
        SELECT p.hash, p.captured_at, p.skipped, p.path, p.landmarks_path,
               p.yaw, p.pitch, p.roll, p.quality_score,
               m.algorithm_version, m.source_signature, m.alignment_signature,
               m.eligible, m.user_excluded, m.updated_at
        FROM photos p LEFT JOIN hair_measurements m ON p.hash = m.photo_hash
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? ORDER BY p.hash
        """,
        (project_id,),
    )
    for row in rows:
        digest.update("|".join(str(value) for value in row).encode("utf-8"))
        for raw_path in (row["path"], row["landmarks_path"], config.aligned_landmarks_dir / f"{row['hash']}.npz"):
            path = Path(raw_path) if raw_path else None
            if path and path.exists():
                stat = path.stat()
                digest.update(f"{stat.st_size}|{stat.st_mtime_ns}".encode())
    events = db.fetchall(
        "SELECT id, event_date, source, status, COALESCE(score, 0), evidence_json, updated_at FROM haircut_events WHERE project_id = ? ORDER BY id",
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
        "SELECT COUNT(*) AS n FROM photos p JOIN project_photos pp ON pp.photo_hash = p.hash WHERE pp.project_id = ? AND p.skipped = 0 AND p.landmarks_path IS NOT NULL",
        (project_id,),
    )["n"])
    rows = _hair_rows(db, project_id, include_excluded=True)
    haircuts = [_haircut_payload(row) for row in db.fetchall(
        "SELECT * FROM haircut_events WHERE project_id = ? AND status IN ('provisional', 'suggested', 'confirmed') ORDER BY event_date DESC, id DESC",
        (project_id,),
    )]
    last_haircut = next((event for event in haircuts if event["status"] == "confirmed" and event["event_date"] <= date.today().isoformat()), None)
    last_haircut = {"id": last_haircut["id"], "event_date": last_haircut["event_date"], "days_since": (date.today() - date.fromisoformat(last_haircut["event_date"])).days} if last_haircut else None
    fresh = [row for row in rows if not _row_alignment_stale(config, row)]
    failed = sum("hair_analysis_failed" in _json_list(row["reasons_json"]) for row in rows)
    successful = [row for row in fresh if "hair_analysis_failed" not in _json_list(row["reasons_json"])]
    latest_photo = db.fetchone("SELECT MAX(p.captured_at) AS captured_at FROM photos p JOIN project_photos pp ON pp.photo_hash = p.hash WHERE pp.project_id = ? AND p.skipped = 0", (project_id,))
    analysis = {
        "latest_photo_date": str(latest_photo["captured_at"])[:10] if latest_photo["captured_at"] else None,
        "latest_analyzed_date": str(successful[-1]["captured_at"])[:10] if successful else None,
        "updated_at": max((str(row["computed_at"]) for row in successful), default=None),
        "pending_photos": total - len(fresh),
        "failed_photos": failed,
    }
    if not rows:
        return {
            "status": "not_ready" if total else "insufficient",
            "analysis_version": ALGORITHM_VERSION,
            "analysis_revision": None,
            "coverage": {"available": 0, "included": 0, "excluded": 0, "total_photos": total},
            "face_outline": [],
            "frames": [],
            "haircuts": haircuts,
            "last_haircut": last_haircut,
            "analysis": analysis,
            "change_since_haircut": None,
            "latest_export": None,
        }
    revision = project_hair_revision(db, config, project_id)
    stale = len(fresh) < total
    frames = []
    for row in _daily_hair_rows(rows):
        day = str(row["captured_at"])[:10]
        reasons = _json_list(row["reasons_json"])
        frames.append(
            {
                "hash": row["hash"],
                "date": day,
                "captured_at": str(row["captured_at"]),
                "quality": round(float(row["hair_quality"] or 0), 3),
                "eligible": bool(row["eligible"]) and not _row_alignment_stale(config, row),
                "excluded": bool(row["user_excluded"]),
                "reasons": reasons,
                "thumb_url": f"/api/photos/{row['hash']}/thumb",
                "source_url": f"/api/photos/{row['hash']}/image",
                "composite_url": f"/api/photos/{row['hash']}/hair-composite.png?v={revision[:10]}" if row["aligned_mask_path"] and Path(row["aligned_mask_path"]).exists() else None,
                "metrics": _json_dict(row["metrics_json"]),
            }
        )
    latest = db.fetchone("SELECT * FROM hair_exports WHERE project_id = ? AND status = 'done' ORDER BY id DESC LIMIT 1", (project_id,))
    latest_export = None
    if latest and latest["output_path"] and Path(latest["output_path"]).exists():
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
        "last_haircut": last_haircut,
        "analysis": analysis,
        "change_since_haircut": _change_since_haircut(_daily_hair_rows(successful, included_only=True), last_haircut),
        "latest_export": latest_export,
    }


def _daily_hair_rows(rows, included_only: bool = False):
    days: dict[str, Any] = {}
    for row in rows:
        if included_only and (not row["eligible"] or row["user_excluded"]):
            continue
        day = str(row["captured_at"])[:10]
        def rank(item):
            return (bool(item["eligible"]) and not item["user_excluded"], not item["user_excluded"], float(item["hair_quality"] or 0), str(item["captured_at"]), item["hash"])
        if day not in days or rank(row) > rank(days[day]):
            days[day] = row
    return [days[day] for day in sorted(days)]


def _haircut_payload(row) -> dict[str, Any]:
    result = {key: row[key] for key in ("id", "event_date", "first_after_photo_hash", "source", "status", "score")}
    result["evidence"] = _json_dict(row["evidence_json"])
    return result


def _change_since_haircut(rows, last_haircut) -> dict[str, Any] | None:
    if not last_haircut or not rows:
        return None
    cut = date.fromisoformat(last_haircut["event_date"])
    after = [row for row in rows if date.fromisoformat(str(row["captured_at"])[:10]) >= cut]
    if not after:
        return None
    latest = after[-1]
    comparable = [row for row in after if _comparable_photos(row, latest)]
    baseline = [row for row in comparable if (date.fromisoformat(str(row["captured_at"])[:10]) - cut).days <= 21][:3]
    latest_date = date.fromisoformat(str(latest["captured_at"])[:10])
    recent = [row for row in comparable if (latest_date - date.fromisoformat(str(row["captured_at"])[:10])).days <= 14][-3:]
    if len(baseline) < 2 or len(recent) < 2 or str(baseline[-1]["captured_at"]) >= str(recent[0]["captured_at"]):
        return None
    baseline_area = float(np.median([_json_dict(row["metrics_json"]).get("area", 0) for row in baseline]))
    recent_area = float(np.median([_json_dict(row["metrics_json"]).get("area", 0) for row in recent]))
    if baseline_area <= 0 or not math.isfinite(baseline_area + recent_area):
        return None
    return {"area_change_percent": round(100 * (recent_area / baseline_area - 1), 1), "baseline_date": str(baseline[0]["captured_at"])[:10], "latest_date": str(recent[-1]["captured_at"])[:10], "baseline_days": len(baseline), "recent_days": len(recent)}


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
    parsed = _validated_haircut_date(event_date)
    existing = db.fetchone("SELECT * FROM haircut_events WHERE project_id = ? AND event_date = ? AND status = 'confirmed'", (project_id, parsed))
    if existing:
        return _haircut_payload(existing)
    suggested = db.fetchone("SELECT id FROM haircut_events WHERE project_id = ? AND event_date = ? AND status IN ('provisional', 'suggested') ORDER BY id LIMIT 1", (project_id, parsed))
    if suggested:
        return update_haircut_event(db, int(suggested["id"]), status="confirmed")
    now = datetime.now().isoformat(sep=" ")
    event_id = db.execute(
        "INSERT INTO haircut_events (project_id, event_date, source, status, created_at, updated_at) VALUES (?, ?, 'manual', 'confirmed', ?, ?)",
        (project_id, parsed, now, now),
    )
    return _haircut_payload(db.fetchone("SELECT * FROM haircut_events WHERE id = ?", (event_id,)))


def _validated_haircut_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    if parsed > date.today():
        raise ValueError("Haircut date cannot be in the future")
    return parsed.isoformat()


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
    next_date = _validated_haircut_date(event_date) if event_date is not None else row["event_date"]
    next_status = status or row["status"]
    if next_status not in {"provisional", "suggested", "confirmed", "dismissed"}:
        raise ValueError("Invalid haircut status")
    db.execute(
        "UPDATE haircut_events SET event_date = ?, status = ?, updated_at = ? WHERE id = ?",
        (next_date, next_status, datetime.now().isoformat(sep=" "), event_id),
    )
    return _haircut_payload(db.fetchone("SELECT * FROM haircut_events WHERE id = ?", (event_id,)))


def _comparable_photos(a, b) -> bool:
    for key in ("camera_make", "camera_model"):
        if a[key] and b[key] and str(a[key]).strip().lower() != str(b[key]).strip().lower():
            return False
    for key in ("yaw", "pitch"):
        if a[key] is not None and b[key] is not None and abs(float(a[key]) - float(b[key])) > 8:
            return False
    return True


def _comparison_mask(config: AppConfig, row) -> np.ndarray | None:
    path = row["aligned_mask_path"]
    if not path or not Path(path).exists():
        return None
    try:
        with Image.open(path) as image:
            image = image.convert("L")
            original_size = image.size
            image.thumbnail((768, 768), Image.Resampling.BILINEAR)
            with np.load(config.aligned_landmarks_dir / f"{row['hash']}.npz", allow_pickle=False) as payload:
                landmarks = np.asarray(payload["landmarks"], dtype=np.float64)[:, :2]
            landmarks *= np.array([image.width / original_size[0], image.height / original_size[1]])
            midpoint, distance, horizontal, vertical = _eye_geometry(landmarks)
            axes = np.stack([horizontal, vertical]) * (32.0 / distance)
            matrix = np.column_stack([axes, np.array([128.0, 128.0]) - axes @ midpoint]).astype(np.float32)
            return _warp_array(np.asarray(image), matrix, (256, 352)) >= 128
    except (OSError, ValueError, KeyError):
        return None


def update_haircut_suggestions(db: Database, config: AppConfig, project_id: int, *, since: date | None = None) -> int:
    rows = _daily_hair_rows([
        row for row in _hair_rows(db, project_id)
        if row["eligible"] and not _row_alignment_stale(config, row)
        and float(row["hair_quality"]) >= 0.65
        and (since is None or date.fromisoformat(str(row["captured_at"])[:10]) >= since - timedelta(days=21))
    ], included_only=True)
    masks = {row["hash"]: _comparison_mask(config, row) for row in rows}
    rows = [row for row in rows if masks[row["hash"]] is not None]
    dates = [date.fromisoformat(str(row["captured_at"])[:10]) for row in rows]
    protected = db.fetchall("SELECT event_date FROM haircut_events WHERE project_id = ? AND status IN ('confirmed', 'dismissed')", (project_id,))
    protected_dates = [date.fromisoformat(row["event_date"]) for row in protected]
    changes: list[tuple[int, float]] = []
    candidates: list[dict[str, Any]] = []
    for index in range(1, len(rows)):
        previous, new = rows[index - 1], rows[index]
        if not 1 <= (dates[index] - dates[index - 1]).days <= 21 or not _comparable_photos(previous, new):
            continue
        new_mask = masks[new["hash"]]
        adjacent_change = 1.0 - mask_iou(masks[previous["hash"]], new_mask)
        history = [change for at, change in changes[-12:] if (dates[index] - dates[at]).days <= 60 and _comparable_photos(rows[at], new)]
        changes.append((index, adjacent_change))
        if since is not None and dates[index] < since:
            continue
        baseline = [row for at, row in enumerate(rows[max(0, index - 3):index], start=max(0, index - 3))
                    if (dates[index] - dates[at]).days <= 21 and _comparable_photos(row, new)]
        if len(baseline) < 2 or any(abs((dates[index] - day).days) <= 10 for day in protected_dates):
            continue
        before = np.mean([masks[row["hash"]] for row in baseline], axis=0) > 0.5
        old_area = int(before.sum())
        if old_area == 0:
            continue
        change = 1.0 - mask_iou(before, new_mask)
        area_drop = 1.0 - float(new_mask.sum()) / old_area
        extent_keys = ("top_extent", "lower_extent", "left_extent", "right_extent")
        old_extent = float(np.median([sum(float(_json_dict(row["metrics_json"]).get(key, 0)) for key in extent_keys) for row in baseline]))
        new_extent = sum(float(_json_dict(new["metrics_json"]).get(key, 0)) for key in extent_keys)
        extent_drop = 1.0 - new_extent / max(old_extent, 1e-6)
        removed = float(np.logical_and(before, ~new_mask).sum()) / old_area
        added = float(np.logical_and(~before, new_mask).sum()) / old_area
        center = float(np.median(history)) if len(history) >= 4 else 0.0
        scale = max(0.025, 1.4826 * float(np.median(np.abs(np.asarray(history) - center)))) if len(history) >= 4 else 0.05
        threshold = max(0.16, center + 3.0 * scale)
        # A contraction must beat recent styling noise, with more disappearing
        # hair than new pixels elsewhere. Shifted/reshaped hair alone is insufficient.
        if change < threshold or area_drop < 0.10 or (extent_drop < 0.045 and area_drop < 0.20) or removed < 0.14 or removed < 2.2 * added:
            continue
        future = [row for at, row in enumerate(rows[index + 1:index + 4], start=index + 1)
                  if (dates[at] - dates[index]).days <= 21 and _comparable_photos(row, new)]
        persistent = [row for row in future
                      if float(masks[row["hash"]].sum()) <= old_area * (1.0 - max(0.06, area_drop * 0.45))
                      and mask_iou(new_mask, masks[row["hash"]]) >= mask_iou(before, masks[row["hash"]]) + 0.035]
        if len(persistent) >= 2:
            status = "suggested"
        elif len(future) < 2 and len(persistent) == len(future):
            status = "provisional"
        else:
            continue
        score = round((change - center) / max(scale, 0.05), 3)
        evidence = {
            "algorithm_version": ALGORITHM_VERSION,
            "before_photo_hash": baseline[-1]["hash"],
            "after_photo_hash": new["hash"],
            "earliest_date": (dates[index - 1] + timedelta(days=1)).isoformat(),
            "latest_date": dates[index].isoformat(),
            "baseline_days": len(baseline),
            "following_days": len(persistent),
            "area_drop_percent": round(area_drop * 100, 1),
            "extent_drop_percent": round(extent_drop * 100, 1),
            "shape_change": round(change, 4),
            "noise_threshold": round(threshold, 4),
        }
        candidates.append({"hash": new["hash"], "date": dates[index], "score": score, "status": status, "evidence": evidence})
    # Adjacent drops can describe the same cut. Keep one supported boundary.
    selected: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (item["status"] == "suggested", item["score"]), reverse=True):
        if all(abs((candidate["date"] - other["date"]).days) > 10 for other in selected):
            selected.append(candidate)
    now = datetime.now().isoformat(sep=" ")
    with db.connect() as conn:
        existing = {row["first_after_photo_hash"]: row for row in conn.execute(
            "SELECT * FROM haircut_events WHERE project_id = ? AND source = 'automatic'", (project_id,)).fetchall()}
        for item in selected:
            prior = existing.get(item["hash"])
            if prior and prior["status"] in {"confirmed", "dismissed"}:
                continue
            evidence = json.dumps(item["evidence"], sort_keys=True, separators=(",", ":"))
            event_date = item["date"].isoformat()
            if prior:
                if (prior["event_date"], prior["status"], prior["score"], prior["evidence_json"]) != (event_date, item["status"], item["score"], evidence):
                    conn.execute("UPDATE haircut_events SET event_date = ?, status = ?, score = ?, evidence_json = ?, updated_at = ? WHERE id = ?",
                                 (event_date, item["status"], item["score"], evidence, now, prior["id"]))
            else:
                conn.execute("INSERT INTO haircut_events (project_id, event_date, first_after_photo_hash, source, status, score, evidence_json, created_at, updated_at) VALUES (?, ?, ?, 'automatic', ?, ?, ?, ?, ?)",
                             (project_id, event_date, item["hash"], item["status"], item["score"], evidence, now, now))
        active = {item["hash"] for item in selected}
        for photo_hash, prior in existing.items():
            if photo_hash not in active and prior["status"] in {"provisional", "suggested"} and (since is None or date.fromisoformat(prior["event_date"]) >= since):
                conn.execute("DELETE FROM haircut_events WHERE id = ?", (prior["id"],))
    return sum(item["status"] == "suggested" for item in selected)


def mask_iou(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 1.0


def normalized_hair_export(payload: dict[str, Any]) -> dict[str, Any]:
    return {"start_date": payload.get("start_date") or None, "end_date": payload.get("end_date") or None,
            "seconds_per_selfie": float(payload.get("seconds_per_selfie", 1)),
            "width": int(payload.get("width", DEFAULT_WIDTH)), "height": int(payload.get("height", DEFAULT_HEIGHT))}


def reusable_hair_export(db: Database, project_id: int, revision: str, payload: dict[str, Any]):
    for row in db.fetchall("SELECT * FROM hair_exports WHERE project_id = ? AND analysis_revision = ? AND status = 'done' ORDER BY id DESC", (project_id, revision)):
        if normalized_hair_export(_json_dict(row["config_json"])) != normalized_hair_export(payload) or not row["output_path"]:
            continue
        try:
            with Path(row["output_path"]).open("rb") as stream:
                if stream.read(8)[4:8] == b"ftyp":
                    return row
        except OSError:
            continue
    return None


def create_hair_export(db: Database, config: AppConfig, project_id: int, payload: dict[str, Any]) -> int:
    revision = project_hair_revision(db, config, project_id)
    existing = reusable_hair_export(db, project_id, revision, payload)
    if existing:
        return int(existing["id"])
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
    existing = reusable_hair_export(db, project_id, project_hair_revision(db, config, project_id), payload)
    if existing and int(existing["id"]) == export_id:
        if progress:
            progress("hair_export", 1, 1, "Video is already up to date")
        return {"export_id": export_id, "output_path": existing["output_path"], "reused": True}
    start = payload.get("start_date")
    end = payload.get("end_date")
    seconds = float(payload.get("seconds_per_selfie", 1.0))
    width = int(payload.get("width", DEFAULT_WIDTH))
    height = int(payload.get("height", DEFAULT_HEIGHT))
    rows = [
        row for row in _daily_hair_rows(_hair_rows(db, project_id), included_only=True)
        if not _row_alignment_stale(config, row)
        and (not start or str(row["captured_at"])[:10] >= start)
        and (not end or str(row["captured_at"])[:10] <= end)
    ]
    if len(rows) < 2:
        db.execute("UPDATE hair_exports SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                   ("At least two included days are required", datetime.now().isoformat(sep=" "), export_id))
        raise RuntimeError("At least two included hair frames are required")
    output = _hair_output_path(config, project_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.export-{export_id}.tmp.mp4")
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
        _replace_previous_hair_exports(db, config, project_id, export_id, output)
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


def _hair_output_path(config: AppConfig, project_id: int) -> Path:
    return config.exports_dir / f"hair-timeline-{project_id}.mp4"


def hair_playback_path(config: AppConfig, project_id: int) -> Path:
    return config.hair_playback_dir / f"hair-timeline-{project_id}.mp4"


def _replace_previous_hair_exports(
    db: Database,
    config: AppConfig,
    project_id: int,
    current_export_id: int,
    current_output_path: Path,
) -> None:
    rows = db.fetchall(
        "SELECT id, output_path FROM hair_exports WHERE project_id = ? AND status = 'done' AND id <> ?",
        (project_id, current_export_id),
    )
    current_path = current_output_path.expanduser().absolute()
    replaced_ids: list[int] = []
    for row in rows:
        export_id = int(row["id"])
        try:
            previous_text = row["output_path"]
            if previous_text:
                previous_path = Path(previous_text).expanduser().absolute()
                if previous_path != current_path:
                    previous_path.unlink(missing_ok=True)
            legacy_playback = config.hair_playback_dir / f"hair-export-{export_id}.mp4"
            legacy_playback.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove replaced hair export %s: %s", export_id, exc)
            continue
        replaced_ids.append(export_id)

    try:
        hair_playback_path(config, project_id).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove cached hair playback for project %s: %s", project_id, exc)

    if replaced_ids:
        with db.connect() as conn:
            conn.execute(
                "UPDATE hair_exports SET status = 'replaced', output_path = NULL WHERE id IN ({})".format(
                    ",".join("?" for _ in replaced_ids)
                ),
                tuple(replaced_ids),
            )


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
    eye_mid, interocular, horizontal, vertical = _eye_geometry(landmarks)
    scale = (width * 0.18) / interocular
    axes = np.stack([horizontal, vertical]) * scale
    return np.column_stack([axes, np.array([width * 0.5, height * 0.39]) - axes @ eye_mid]).astype(np.float32)


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
        SELECT p.hash, p.path, p.landmarks_path, p.captured_at, p.skipped, pr.canonical_landmarks_path,
               p.camera_make, p.camera_model, p.yaw, p.pitch, p.roll,
               m.algorithm_version, m.source_signature, m.computed_at, m.eligible, m.user_excluded,
               m.aligned_mask_path, m.alignment_signature, m.metrics_json,
               m.quality_score AS hair_quality, m.reasons_json, m.updated_at
        FROM hair_measurements m
        JOIN photos p ON p.hash = m.photo_hash
        JOIN project_photos pp ON pp.photo_hash = p.hash
        JOIN projects pr ON pr.id = pp.project_id
        WHERE pp.project_id = ? AND p.skipped = 0 AND p.landmarks_path IS NOT NULL {condition}
        ORDER BY p.captured_at, p.hash
        """,
        (project_id,),
    )


def _row_alignment_stale(config: AppConfig, row) -> bool:
    if row["algorithm_version"] != ALGORITHM_VERSION:
        return True
    try:
        if row["source_signature"] != source_signature(Path(row["path"]), Path(row["landmarks_path"]) if row["landmarks_path"] else None):
            return True
    except OSError:
        return "hair_analysis_failed" not in _json_list(row["reasons_json"])
    if "hair_analysis_failed" in _json_list(row["reasons_json"]):
        return False
    expected = alignment_signature(config.aligned_landmarks_dir / f"{row['hash']}.npz")
    if expected is None:
        return "alignment_not_ready" not in _json_list(row["reasons_json"])
    canonical = Path(row["canonical_landmarks_path"]) if row["canonical_landmarks_path"] else None
    aligned = config.aligned_landmarks_dir / f"{row['hash']}.npz"
    if canonical and canonical.exists() and canonical.stat().st_mtime_ns > aligned.stat().st_mtime_ns:
        return True
    return expected != row["alignment_signature"] or not row["aligned_mask_path"] or not Path(row["aligned_mask_path"]).exists()


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
