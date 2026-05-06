from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image
from scipy.spatial import Delaunay

CancelCheck = Callable[[], None]


def load_aligned_landmarks(path: str | Path) -> np.ndarray:
    with np.load(path) as payload:
        return np.asarray(payload["landmarks"], dtype=np.float64)[:, :2]


def morph_pair(
    image_a_path: str | Path,
    image_b_path: str | Path,
    landmarks_a_path: str | Path,
    landmarks_b_path: str | Path,
    intermediate_frames: int,
    cancel_check: CancelCheck | None = None,
) -> Iterable[Image.Image]:
    image_a = Image.open(image_a_path).convert("RGB")
    image_b = Image.open(image_b_path).convert("RGB")
    if image_a.size != image_b.size:
        image_b = image_b.resize(image_a.size, Image.Resampling.LANCZOS)
    landmarks_a = load_aligned_landmarks(landmarks_a_path)
    landmarks_b = load_aligned_landmarks(landmarks_b_path)
    yield from morph_images(image_a, image_b, landmarks_a, landmarks_b, intermediate_frames, cancel_check=cancel_check)


def morph_images(
    image_a: Image.Image,
    image_b: Image.Image,
    landmarks_a: np.ndarray,
    landmarks_b: np.ndarray,
    intermediate_frames: int,
    cancel_check: CancelCheck | None = None,
) -> Iterable[Image.Image]:
    def check_cancel() -> None:
        if cancel_check:
            cancel_check()

    if intermediate_frames <= 0:
        return
    if landmarks_a.shape != landmarks_b.shape or len(landmarks_a) < 3:
        for idx in range(intermediate_frames):
            check_cancel()
            yield Image.blend(image_a, image_b, (idx + 1) / (intermediate_frames + 1))
        return

    try:
        import cv2
    except Exception:
        for idx in range(intermediate_frames):
            check_cancel()
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
        check_cancel()
        t = (idx + 1) / (intermediate_frames + 1)
        target = (1 - t) * points_a + t * points_b
        frame = _morph_frame_cv2(cv2, arr_a, arr_b, points_a, points_b, target, triangles, t, cancel_check=cancel_check)
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
    cancel_check: CancelCheck | None = None,
) -> np.ndarray:
    height, width = image_a.shape[:2]
    output = np.zeros_like(image_a, dtype=np.float32)
    coverage = np.zeros((height, width, 1), dtype=np.float32)
    for index, tri in enumerate(triangles):
        if cancel_check and index % 16 == 0:
            cancel_check()
        tri_a = points_a[tri].astype(np.float32)
        tri_b = points_b[tri].astype(np.float32)
        tri_t = target[tri].astype(np.float32)
        _blend_triangle_patch(cv2, output, coverage, image_a, image_b, tri_a, tri_b, tri_t, t, width, height)
    if np.any(coverage < 0.5):
        fallback = (1 - t) * image_a + t * image_b
        output = output * coverage + fallback * (1 - coverage)
    return output


def _blend_triangle_patch(
    cv2,
    output: np.ndarray,
    coverage: np.ndarray,
    image_a: np.ndarray,
    image_b: np.ndarray,
    tri_a: np.ndarray,
    tri_b: np.ndarray,
    tri_t: np.ndarray,
    t: float,
    width: int,
    height: int,
) -> None:
    target_rect = _clipped_rect(cv2, tri_t, width, height)
    source_rect_a = _clipped_rect(cv2, tri_a, width, height)
    source_rect_b = _clipped_rect(cv2, tri_b, width, height)
    if target_rect is None or source_rect_a is None or source_rect_b is None:
        return

    tx0, ty0, tx1, ty1 = target_rect
    ax0, ay0, ax1, ay1 = source_rect_a
    bx0, by0, bx1, by1 = source_rect_b
    target_size = (tx1 - tx0, ty1 - ty0)

    target_local = tri_t - np.array([tx0, ty0], dtype=np.float32)
    patch_a = image_a[ay0:ay1, ax0:ax1]
    patch_b = image_b[by0:by1, bx0:bx1]
    tri_a_local = tri_a - np.array([ax0, ay0], dtype=np.float32)
    tri_b_local = tri_b - np.array([bx0, by0], dtype=np.float32)

    warp_a = _warp_triangle_patch(cv2, patch_a, tri_a_local, target_local, target_size)
    warp_b = _warp_triangle_patch(cv2, patch_b, tri_b_local, target_local, target_size)
    warped = (1 - t) * warp_a + t * warp_b

    mask = np.zeros((target_size[1], target_size[0], 1), dtype=np.float32)
    cv2.fillConvexPoly(mask, np.int32(np.round(target_local)), (1.0,), lineType=cv2.LINE_AA)

    output_roi = output[ty0:ty1, tx0:tx1]
    coverage_roi = coverage[ty0:ty1, tx0:tx1]
    output_roi[:] = output_roi * (1 - mask) + warped * mask
    coverage_roi[:] = np.maximum(coverage_roi, mask)


def _clipped_rect(cv2, points: np.ndarray, width: int, height: int) -> tuple[int, int, int, int] | None:
    x, y, w, h = cv2.boundingRect(points.astype(np.float32))
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(width, x + w)
    y1 = min(height, y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _warp_triangle_patch(
    cv2,
    image: np.ndarray,
    src_tri: np.ndarray,
    dst_tri: np.ndarray,
    target_size: tuple[int, int],
) -> np.ndarray:
    matrix = cv2.getAffineTransform(src_tri.astype(np.float32), dst_tri.astype(np.float32))
    return cv2.warpAffine(
        image,
        matrix,
        target_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )


def _warp_triangle(cv2, image: np.ndarray, src_tri: np.ndarray, dst_tri: np.ndarray, width: int, height: int) -> np.ndarray:
    matrix = cv2.getAffineTransform(src_tri.astype(np.float32), dst_tri.astype(np.float32))
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )
