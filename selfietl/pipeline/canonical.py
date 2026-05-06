from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw

from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.pipeline.score import compute_quality_score

Progress = Callable[[str, int, int, str], None]
CancelCheck = Callable[[], None]

STABLE_ALIGNMENT_INDICES = (
    33,
    133,
    263,
    362,
    6,
    8,
    9,
    168,
    195,
    197,
    468,
    469,
    470,
    471,
    472,
    473,
    474,
    475,
    476,
    477,
)


def similarity_transform(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Return a 2x3 matrix mapping source points onto target points."""
    src = np.asarray(source, dtype=np.float64)
    dst = np.asarray(target, dtype=np.float64)
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 2:
        raise ValueError("source and target must be Nx2 arrays")
    if len(src) < 2:
        raise ValueError("at least two points are required")

    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_centered = src - src_mean
    dst_centered = dst - dst_mean
    src_var = np.mean(np.sum(src_centered**2, axis=1))
    if src_var <= 1e-12:
        raise ValueError("source points are degenerate")

    covariance = (src_centered.T @ dst_centered) / len(src)
    u, singular_values, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1, :] *= -1
        singular_values[-1] *= -1
        rotation = vt.T @ u.T
    scale = float(np.sum(singular_values) / src_var)
    translation = dst_mean - scale * (rotation @ src_mean)
    matrix = np.empty((2, 3), dtype=np.float64)
    matrix[:, :2] = scale * rotation
    matrix[:, 2] = translation
    return matrix


def affine_transform(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    src = np.asarray(source, dtype=np.float64)
    dst = np.asarray(target, dtype=np.float64)
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 2:
        raise ValueError("source and target must be Nx2 arrays")
    if len(src) < 3:
        raise ValueError("at least three points are required")
    design = np.column_stack([src, np.ones(len(src))])
    coeff, *_ = np.linalg.lstsq(design, dst, rcond=None)
    return coeff.T


def apply_transform(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    return pts @ matrix[:, :2].T + matrix[:, 2]


def stable_alignment_points(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    indices = [idx for idx in STABLE_ALIGNMENT_INDICES if idx < len(pts)]
    if len(indices) >= 3:
        return pts[indices, :2]
    return pts[:, :2]


def compute_canonical_face(
    db: Database,
    config: AppConfig,
    project_id: int,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
) -> Path:
    rows = db.fetchall(
        """
        SELECT p.hash, p.landmarks_path, p.width, p.height, p.quality_score,
               p.yaw, p.pitch, p.roll, p.eye_open_ratio, p.user_override
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND p.landmarks_path IS NOT NULL AND p.skipped = 0
        ORDER BY p.captured_at
        """,
        (project_id,),
    )
    if not rows:
        raise RuntimeError("No detected, active photos are available for canonical face computation")

    shapes: list[np.ndarray] = []
    hashes: list[str] = []
    sizes: list[tuple[int, int]] = []
    for row in rows:
        if cancel_check:
            cancel_check()
        payload = np.load(row["landmarks_path"])
        landmarks = np.asarray(payload["landmarks"], dtype=np.float64)[:, :2]
        if landmarks.shape[0] < 3:
            continue
        shapes.append(landmarks)
        hashes.append(row["hash"])
        sizes.append((int(row["width"]), int(row["height"])))

    if not shapes:
        raise RuntimeError("Detected photos did not contain enough landmarks")

    reference = shapes[0]
    for _ in range(4):
        aligned = []
        for shape in shapes:
            if cancel_check:
                cancel_check()
            matrix = similarity_transform(stable_alignment_points(shape), stable_alignment_points(reference))
            aligned.append(apply_transform(shape, matrix))
        reference = np.mean(np.stack(aligned), axis=0)

    widths = np.array([size[0] for size in sizes], dtype=np.float64)
    heights = np.array([size[1] for size in sizes], dtype=np.float64)
    target_size = (int(np.median(widths)), int(np.median(heights)))

    canonical_dir = config.data_dir / "cache"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = canonical_dir / f"canonical_project_{project_id}.npz"
    np.savez_compressed(
        canonical_path,
        landmarks=reference.astype(np.float32),
        target_size=np.array(target_size, dtype=np.int32),
        hashes=np.array(hashes),
    )

    residuals = []
    aligned_shapes = []
    for shape in shapes:
        matrix = similarity_transform(stable_alignment_points(shape), stable_alignment_points(reference))
        aligned = apply_transform(shape, matrix)
        aligned_shapes.append(aligned)
        residuals.append(float(np.sqrt(np.mean(np.sum((aligned - reference) ** 2, axis=1)))))

    residual_arr = np.asarray(residuals, dtype=np.float64)
    mean = float(residual_arr.mean())
    std = float(residual_arr.std() or 1.0)
    zscores = (residual_arr - mean) / std

    with db.connect() as conn:
        for idx, row in enumerate(rows):
            if cancel_check:
                cancel_check()
            zscore = max(0.0, float(zscores[idx]))
            quality = compute_quality_score(
                confidence=row["quality_score"] if row["quality_score"] is not None else 1.0,
                yaw=row["yaw"],
                pitch=row["pitch"],
                roll=row["roll"],
                eye_open_ratio=row["eye_open_ratio"],
                landmark_zscore=zscore,
                config=config.quality,
            ).score
            should_skip = quality < config.quality.threshold
            reason = "low_quality"
            if zscore > config.quality.landmark_zscore_threshold:
                should_skip = True
                reason = "landmark_outlier"
            if row["user_override"]:
                should_skip = False
                reason = None
            conn.execute(
                """
                UPDATE photos
                SET quality_score = ?,
                    skipped = CASE WHEN ? THEN 1 ELSE skipped END,
                    skip_reason = CASE WHEN ? THEN ? ELSE skip_reason END
                WHERE hash = ?
                """,
                (quality, should_skip, should_skip, reason, row["hash"]),
            )
            if progress:
                progress("canonical", idx + 1, len(rows), "Updated landmark drift scores")

    render_average_face(canonical_path, config.data_dir / "cache" / f"avg_face_project_{project_id}.png")
    render_heatmap(canonical_path, aligned_shapes, config.data_dir / "cache" / f"heatmap_project_{project_id}.png")
    db.execute(
        "UPDATE projects SET canonical_landmarks_path = ? WHERE id = ?",
        (str(canonical_path), project_id),
    )
    return canonical_path


def load_canonical(path: str | Path) -> tuple[np.ndarray, tuple[int, int]]:
    payload = np.load(path)
    landmarks = np.asarray(payload["landmarks"], dtype=np.float64)
    target_size_raw = np.asarray(payload["target_size"], dtype=np.int32)
    return landmarks, (int(target_size_raw[0]), int(target_size_raw[1]))


def canonical_pixels(canonical_path: str | Path) -> tuple[np.ndarray, tuple[int, int]]:
    landmarks, size = load_canonical(canonical_path)
    width, height = size
    pixels = landmarks[:, :2] * np.array([width, height], dtype=np.float64)
    return pixels, size


def render_average_face(canonical_path: str | Path, output_path: str | Path, side: int = 900) -> Path:
    landmarks, _ = load_canonical(canonical_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (side, side), (242, 239, 231))
    draw = ImageDraw.Draw(image, "RGBA")
    pts = landmarks[:, :2].copy()
    mins = pts.min(axis=0)
    maxs = pts.max(axis=0)
    span = np.maximum(maxs - mins, 1e-6)
    pts = (pts - mins) / span
    pts = pts * (side * 0.72) + side * 0.14
    for x, y in pts:
        draw.ellipse((x - 2.4, y - 2.4, x + 2.4, y + 2.4), fill=(28, 75, 86, 185))
    for idx in range(0, len(pts), 7):
        x, y = pts[idx]
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(196, 79, 49, 145))
    image.save(output_path, "PNG")
    return output_path


def render_heatmap(
    canonical_path: str | Path,
    aligned_shapes: list[np.ndarray],
    output_path: str | Path,
    side: int = 900,
) -> Path:
    canonical, _ = load_canonical(canonical_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (side, side), (22, 24, 27))
    draw = ImageDraw.Draw(image, "RGBA")
    if not aligned_shapes:
        image.save(output_path, "PNG")
        return output_path

    stack = np.stack(aligned_shapes)
    drift = np.sqrt(np.mean(np.sum((stack - canonical[None, :, :2]) ** 2, axis=2), axis=0))
    max_drift = float(drift.max() or 1.0)
    pts = canonical[:, :2].copy()
    pts = pts * np.array([side, side])
    for (x, y), value in zip(pts, drift):
        intensity = min(1.0, float(value / max_drift))
        color = _heat_color(intensity)
        radius = 3 + 9 * intensity
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    image.save(output_path, "PNG")
    return output_path


def _heat_color(value: float) -> tuple[int, int, int, int]:
    value = max(0.0, min(1.0, value))
    red = int(220 * value + 40 * (1 - value))
    green = int(78 * value + 175 * (1 - value))
    blue = int(46 * value + 170 * (1 - value))
    alpha = int(105 + 130 * math.sqrt(value))
    return red, green, blue, alpha


def project_stats(db: Database, project_id: int) -> dict:
    rows = db.fetchall(
        """
        SELECT p.captured_at, p.quality_score, p.yaw, p.pitch, p.roll,
               p.eye_open_ratio, p.mouth_open_ratio, p.skipped
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ?
        ORDER BY p.captured_at
        """,
        (project_id,),
    )
    by_month: dict[str, int] = {}
    timeline = []
    yaw_pitch_roll = []
    eye_open = []
    for row in rows:
        captured = str(row["captured_at"])
        month = captured[:7]
        by_month[month] = by_month.get(month, 0) + 1
        timeline.append(
            {
                "date": captured,
                "quality": row["quality_score"],
                "skipped": bool(row["skipped"]),
            }
        )
        yaw_pitch_roll.append(
            {
                "date": captured,
                "yaw": row["yaw"],
                "pitch": row["pitch"],
                "roll": row["roll"],
            }
        )
        if row["eye_open_ratio"] is not None:
            eye_open.append(float(row["eye_open_ratio"]))

    return {
        "timeline": timeline,
        "pose": yaw_pitch_roll,
        "eye_open": eye_open,
        "photos_by_month": [{"month": key, "count": value} for key, value in sorted(by_month.items())],
        "total": len(rows),
        "skipped": sum(1 for row in rows if row["skipped"]),
    }
