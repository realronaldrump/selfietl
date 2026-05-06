from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.pipeline.canonical import (
    affine_transform,
    apply_transform,
    canonical_pixels,
    similarity_transform,
    stable_alignment_points,
)
from selfietl.pipeline.images import copy_exif, open_oriented_image

Progress = Callable[[str, int, int, str], None]
CancelCheck = Callable[[], None]


def align_project(
    db: Database,
    config: AppConfig,
    project_id: int,
    mode: str = "similarity",
    progress: Progress | None = None,
    force: bool = False,
    cancel_check: CancelCheck | None = None,
) -> dict:
    project = db.fetchone("SELECT canonical_landmarks_path FROM projects WHERE id = ?", (project_id,))
    if project is None or not project["canonical_landmarks_path"]:
        raise RuntimeError("Canonical face has not been computed")
    canonical_path = Path(project["canonical_landmarks_path"])
    target_landmarks, target_size = canonical_pixels(canonical_path)
    rows = db.fetchall(
        """
        SELECT p.hash, p.path, p.landmarks_path
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND p.skipped = 0 AND p.landmarks_path IS NOT NULL
        ORDER BY p.captured_at
        """,
        (project_id,),
    )

    aligned = 0
    skipped = 0
    errors: list[dict] = []
    for idx, row in enumerate(rows):
        if cancel_check:
            cancel_check()
        output = aligned_path(config, row["hash"])
        if output.exists() and not force:
            skipped += 1
            continue
        if progress:
            progress("align", idx + 1, len(rows), f"Aligning {Path(row['path']).name}")
        try:
            align_photo(
                source_path=Path(row["path"]),
                landmarks_path=Path(row["landmarks_path"]),
                target_landmarks=target_landmarks,
                target_size=target_size,
                output_path=output,
                aligned_landmarks_path=config.aligned_landmarks_dir / f"{row['hash']}.npz",
                mode=mode,
                quality=config.alignment.output_quality,
                preserve_exif=config.alignment.preserve_exif,
            )
            aligned += 1
        except Exception as exc:
            errors.append({"hash": row["hash"], "error": f"{exc.__class__.__name__}: {exc}"})

    return {"total": len(rows), "aligned": aligned, "skipped_existing": skipped, "errors": errors}


def aligned_path(config: AppConfig, photo_hash: str) -> Path:
    suffix = config.alignment.output_format
    return config.aligned_dir / f"{photo_hash}.{suffix}"


def align_photo(
    *,
    source_path: Path,
    landmarks_path: Path,
    target_landmarks: np.ndarray,
    target_size: tuple[int, int],
    output_path: Path,
    aligned_landmarks_path: Path,
    mode: str = "similarity",
    quality: int = 95,
    preserve_exif: bool = True,
) -> Path:
    payload = np.load(landmarks_path)
    normalized = np.asarray(payload["landmarks"], dtype=np.float64)[:, :2]
    with open_oriented_image(source_path) as image:
        width, height = image.size
        source_landmarks = normalized * np.array([width, height], dtype=np.float64)
        if mode == "affine":
            matrix = affine_transform(stable_alignment_points(source_landmarks), stable_alignment_points(target_landmarks))
        else:
            matrix = similarity_transform(stable_alignment_points(source_landmarks), stable_alignment_points(target_landmarks))
        aligned = _warp_image(image, matrix, target_size)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs = {"quality": quality, "optimize": True} if output_path.suffix.lower() in (".jpg", ".jpeg") else {}
    aligned.save(output_path, **save_kwargs)
    if preserve_exif and output_path.suffix.lower() in (".jpg", ".jpeg"):
        copy_exif(source_path, output_path)

    aligned_landmarks_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        aligned_landmarks_path,
        landmarks=apply_transform(source_landmarks, matrix).astype(np.float32),
        matrix=matrix.astype(np.float32),
        target_size=np.array(target_size, dtype=np.int32),
    )
    return output_path


def _warp_image(image: Image.Image, matrix: np.ndarray, target_size: tuple[int, int]) -> Image.Image:
    try:
        import cv2

        rgb = np.asarray(image.convert("RGB"))
        warped = cv2.warpAffine(
            rgb,
            matrix.astype(np.float32),
            target_size,
            flags=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_REPLICATE,
        )
        return Image.fromarray(warped)
    except Exception:
        affine = np.vstack([matrix, [0, 0, 1]])
        inverse = np.linalg.inv(affine)
        coeff = (
            float(inverse[0, 0]),
            float(inverse[0, 1]),
            float(inverse[0, 2]),
            float(inverse[1, 0]),
            float(inverse[1, 1]),
            float(inverse[1, 2]),
        )
        return image.transform(target_size, Image.Transform.AFFINE, coeff, resample=Image.Resampling.BICUBIC)
