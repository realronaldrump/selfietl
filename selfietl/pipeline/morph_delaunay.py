from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image
from scipy.spatial import Delaunay


def load_aligned_landmarks(path: str | Path) -> np.ndarray:
    payload = np.load(path)
    return np.asarray(payload["landmarks"], dtype=np.float64)[:, :2]


def morph_pair(
    image_a_path: str | Path,
    image_b_path: str | Path,
    landmarks_a_path: str | Path,
    landmarks_b_path: str | Path,
    intermediate_frames: int,
) -> Iterable[Image.Image]:
    image_a = Image.open(image_a_path).convert("RGB")
    image_b = Image.open(image_b_path).convert("RGB")
    if image_a.size != image_b.size:
        image_b = image_b.resize(image_a.size, Image.Resampling.LANCZOS)
    landmarks_a = load_aligned_landmarks(landmarks_a_path)
    landmarks_b = load_aligned_landmarks(landmarks_b_path)
    yield from morph_images(image_a, image_b, landmarks_a, landmarks_b, intermediate_frames)


def morph_images(
    image_a: Image.Image,
    image_b: Image.Image,
    landmarks_a: np.ndarray,
    landmarks_b: np.ndarray,
    intermediate_frames: int,
) -> Iterable[Image.Image]:
    if intermediate_frames <= 0:
        return
    if landmarks_a.shape != landmarks_b.shape or len(landmarks_a) < 3:
        for idx in range(intermediate_frames):
            yield Image.blend(image_a, image_b, (idx + 1) / (intermediate_frames + 1))
        return

    try:
        import cv2
    except Exception:
        for idx in range(intermediate_frames):
            yield Image.blend(image_a, image_b, (idx + 1) / (intermediate_frames + 1))
        return

    width, height = image_a.size
    boundary = _boundary_points(width, height)
    points_a = np.vstack([landmarks_a, boundary])
    points_b = np.vstack([landmarks_b, boundary])
    average = (points_a + points_b) / 2
    triangles = Delaunay(average).simplices
    arr_a = np.asarray(image_a, dtype=np.float32)
    arr_b = np.asarray(image_b, dtype=np.float32)
    for idx in range(intermediate_frames):
        t = (idx + 1) / (intermediate_frames + 1)
        target = (1 - t) * points_a + t * points_b
        frame = _morph_frame_cv2(cv2, arr_a, arr_b, points_a, points_b, target, triangles, t)
        yield Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8))


def _boundary_points(width: int, height: int) -> np.ndarray:
    return np.array(
        [
            [0, 0],
            [width - 1, 0],
            [width - 1, height - 1],
            [0, height - 1],
            [width / 2, 0],
            [width - 1, height / 2],
            [width / 2, height - 1],
            [0, height / 2],
        ],
        dtype=np.float64,
    )


def _morph_frame_cv2(
    cv2,
    image_a: np.ndarray,
    image_b: np.ndarray,
    points_a: np.ndarray,
    points_b: np.ndarray,
    target: np.ndarray,
    triangles: np.ndarray,
    t: float,
) -> np.ndarray:
    height, width = image_a.shape[:2]
    output = np.zeros_like(image_a, dtype=np.float32)
    coverage = np.zeros((height, width, 1), dtype=np.float32)
    for tri in triangles:
        tri_a = points_a[tri].astype(np.float32)
        tri_b = points_b[tri].astype(np.float32)
        tri_t = target[tri].astype(np.float32)
        patch_a = _warp_triangle(cv2, image_a, tri_a, tri_t, width, height)
        patch_b = _warp_triangle(cv2, image_b, tri_b, tri_t, width, height)
        warped = (1 - t) * patch_a + t * patch_b
        mask = np.zeros((height, width, 1), dtype=np.float32)
        cv2.fillConvexPoly(mask, np.int32(np.round(tri_t)), (1.0,), lineType=cv2.LINE_AA)
        output = output * (1 - mask) + warped * mask
        coverage = np.maximum(coverage, mask)
    if np.any(coverage < 0.5):
        fallback = (1 - t) * image_a + t * image_b
        output = output * coverage + fallback * (1 - coverage)
    return output


def _warp_triangle(cv2, image: np.ndarray, src_tri: np.ndarray, dst_tri: np.ndarray, width: int, height: int) -> np.ndarray:
    matrix = cv2.getAffineTransform(src_tri.astype(np.float32), dst_tri.astype(np.float32))
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )
