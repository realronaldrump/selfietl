from __future__ import annotations

import math
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.pipeline.images import open_oriented_image
from selfietl.pipeline.score import compute_quality_score

Progress = Callable[[str, int, int, str], None]
CancelCheck = Callable[[], None]

FACE_LANDMARKER_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)
FACE_LANDMARKER_MODEL_NAME = "face_landmarker.task"


@dataclass
class DetectionResult:
    landmarks: np.ndarray | None
    bbox: tuple[float, float, float, float] | None
    confidence: float
    yaw: float | None
    pitch: float | None
    roll: float | None
    eye_open_ratio: float | None
    mouth_open_ratio: float | None
    warnings: list[str]
    method: str


def detect_project(
    db: Database,
    config: AppConfig,
    project_id: int,
    progress: Progress | None = None,
    force: bool = False,
    cancel_check: CancelCheck | None = None,
) -> dict:
    where_detected = "" if force else "AND p.detected_at IS NULL"
    rows = db.fetchall(
        f"""
        SELECT p.hash, p.path, p.user_override
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? {where_detected}
        ORDER BY p.captured_at
        """,
        (project_id,),
    )
    detected = 0
    skipped = 0
    failed = 0
    warnings: list[dict] = []
    face_mesh = _create_mediapipe_face_mesh(config)

    try:
        for idx, row in enumerate(rows):
            if cancel_check:
                cancel_check()
            photo_hash = row["hash"]
            if progress:
                progress("detect", idx + 1, len(rows), f"Detecting landmarks for {Path(row['path']).name}")
            try:
                result = detect_landmarks(Path(row["path"]), config, face_mesh=face_mesh)
                if result.landmarks is None:
                    skipped += 1
                    skip_reason = "landmarks_unavailable" if "opencv_face_detected_without_landmarks" in result.warnings else "no_face_detected"
                    db.execute(
                        """
                        UPDATE photos
                        SET detected_at = ?, skipped = 1, skip_reason = ?, quality_score = 0
                        WHERE hash = ?
                        """,
                        (datetime.now().isoformat(sep=" "), skip_reason, photo_hash),
                    )
                    continue

                landmarks_path = config.landmarks_dir / f"{photo_hash}.npz"
                landmarks_path.parent.mkdir(parents=True, exist_ok=True)
                with open_oriented_image(row["path"]) as image:
                    image_size = np.array([image.width, image.height], dtype=np.int32)
                np.savez_compressed(
                    landmarks_path,
                    landmarks=result.landmarks.astype(np.float32),
                    bbox=np.array(result.bbox or (0, 0, 0, 0), dtype=np.float32),
                    image_size=image_size,
                    confidence=np.array([result.confidence], dtype=np.float32),
                    method=np.array([result.method]),
                )
                quality = compute_quality_score(
                    confidence=result.confidence,
                    yaw=result.yaw,
                    pitch=result.pitch,
                    roll=result.roll,
                    eye_open_ratio=result.eye_open_ratio,
                    landmark_zscore=None,
                    config=config.quality,
                ).score
                should_skip = quality < config.quality.threshold and not bool(row["user_override"])
                db.execute(
                    """
                    UPDATE photos
                    SET detected_at = ?, landmarks_path = ?, quality_score = ?,
                        yaw = ?, pitch = ?, roll = ?, eye_open_ratio = ?, mouth_open_ratio = ?,
                        skipped = ?, skip_reason = ?
                    WHERE hash = ?
                    """,
                    (
                        datetime.now().isoformat(sep=" "),
                        str(landmarks_path),
                        quality,
                        result.yaw,
                        result.pitch,
                        result.roll,
                        result.eye_open_ratio,
                        result.mouth_open_ratio,
                        1 if should_skip else 0,
                        "low_quality" if should_skip else None,
                        photo_hash,
                    ),
                )
                detected += 1
                if result.warnings:
                    warnings.append({"hash": photo_hash, "warnings": result.warnings})
            except Exception as exc:
                failed += 1
                warnings.append({"hash": photo_hash, "warnings": [f"detect_failed:{exc.__class__.__name__}"]})
    finally:
        if face_mesh is not None:
            face_mesh.close()

    return {
        "total": len(rows),
        "detected": detected,
        "skipped": skipped,
        "failed": failed,
        "warnings": warnings,
    }


def detect_landmarks(path: Path, config: AppConfig, face_mesh=None) -> DetectionResult:
    mediapipe_result = _detect_with_mediapipe(path, config, face_mesh=face_mesh)
    if mediapipe_result.landmarks is not None:
        return mediapipe_result
    opencv_result = _detect_with_opencv(path, config)
    if opencv_result.landmarks is not None or "opencv_face_detected_without_landmarks" in opencv_result.warnings:
        opencv_result.warnings = [*mediapipe_result.warnings, *opencv_result.warnings]
        return opencv_result
    return mediapipe_result


def _create_mediapipe_face_mesh(config: AppConfig):
    try:
        import mediapipe as mp

        face_mesh = getattr(getattr(mp, "solutions", None), "face_mesh", None)
        if face_mesh is not None:
            return face_mesh.FaceMesh(
                static_image_mode=True,
                max_num_faces=4,
                refine_landmarks=config.detection.refine_landmarks,
                min_detection_confidence=config.detection.min_detection_confidence,
            )
    except Exception as exc:
        legacy_error = exc
    else:
        legacy_error = None

    try:
        from mediapipe.tasks.python import BaseOptions, vision
        from mediapipe.tasks.python.vision.core import vision_task_running_mode

        model_path = _ensure_face_landmarker_model(config)
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision_task_running_mode.VisionTaskRunningMode.IMAGE,
            num_faces=4,
            min_face_detection_confidence=config.detection.min_detection_confidence,
            min_face_presence_confidence=config.detection.min_detection_confidence,
        )
        return vision.FaceLandmarker.create_from_options(options)
    except Exception as exc:
        detail = f"{exc.__class__.__name__}: {exc}"
        if legacy_error is not None:
            detail = f"{detail}; legacy FaceMesh failed with {legacy_error.__class__.__name__}: {legacy_error}"
        raise RuntimeError(f"MediaPipe Face Mesh could not be initialized. {detail}") from exc


def _ensure_face_landmarker_model(config: AppConfig) -> Path:
    model_path = config.data_dir / "cache" / "models" / FACE_LANDMARKER_MODEL_NAME
    if model_path.exists() and model_path.stat().st_size > 0:
        return model_path
    model_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(FACE_LANDMARKER_MODEL_URL, model_path)
    except Exception as exc:
        if model_path.exists():
            model_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"MediaPipe Tasks needs a one-time face-landmarker model download. "
            f"Could not download it; place {FACE_LANDMARKER_MODEL_NAME} at {model_path} and retry."
        ) from exc
    return model_path


def _detect_with_mediapipe(path: Path, config: AppConfig, face_mesh=None) -> DetectionResult:
    if face_mesh is None:
        return _empty_result(["mediapipe_unavailable"], "none")

    try:
        with open_oriented_image(path) as image:
            resized, _ = _resize_for_detection(np.asarray(image.convert("RGB")), config.detection.max_detection_side)

        faces = _process_mediapipe_faces(face_mesh, resized)
        if not faces:
            return _empty_result(["no_face_detected"], "mediapipe")

        ranked_faces = []
        for face in faces:
            points = face.landmark if hasattr(face, "landmark") else face
            arr = np.array([[lm.x, lm.y, lm.z] for lm in points], dtype=np.float64)
            bbox = _bbox(arr)
            ranked_faces.append((bbox[2] * bbox[3], arr, bbox))
        ranked_faces.sort(key=lambda item: item[0], reverse=True)
        _, landmarks, bbox = ranked_faces[0]
        warnings = ["multiple_faces_largest_selected"] if len(ranked_faces) > 1 else []
        metrics = landmark_metrics(landmarks)
        return DetectionResult(
            landmarks=landmarks,
            bbox=bbox,
            confidence=1.0,
            warnings=warnings,
            method="mediapipe",
            **metrics,
        )
    except Exception as exc:
        return _empty_result([f"mediapipe_failed:{exc.__class__.__name__}"], "mediapipe")


def _process_mediapipe_faces(face_mesh, image: np.ndarray):
    if hasattr(face_mesh, "process"):
        result = face_mesh.process(image)
        return result.multi_face_landmarks or []

    if hasattr(face_mesh, "detect"):
        import mediapipe as mp

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(image.astype(np.uint8)),
        )
        result = face_mesh.detect(mp_image)
        return result.face_landmarks or []

    raise RuntimeError(f"Unsupported MediaPipe detector: {type(face_mesh).__name__}")


def _detect_with_opencv(path: Path, config: AppConfig) -> DetectionResult:
    try:
        import cv2
    except Exception:
        return _empty_result(["opencv_unavailable"], "none")

    try:
        image = open_oriented_image(path)
        arr = np.asarray(image)
        resized, scale = _resize_for_detection(arr, config.detection.max_detection_side)
        gray = cv2.cvtColor(resized, cv2.COLOR_RGB2GRAY)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        classifier = cv2.CascadeClassifier(cascade_path)
        faces = classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(64, 64))
        if len(faces) == 0:
            return _empty_result(["no_face_detected"], "opencv")
        faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
        x, y, w, h = [float(v) / scale for v in faces[0]]
        bbox = (x / image.width, y / image.height, w / image.width, h / image.height)
        warnings = ["opencv_face_detected_without_landmarks"]
        if len(faces) > 1:
            warnings.append("multiple_faces_largest_selected")
        return DetectionResult(
            landmarks=None,
            bbox=bbox,
            confidence=0.5,
            warnings=warnings,
            method="opencv_fallback",
            yaw=None,
            pitch=None,
            roll=None,
            eye_open_ratio=None,
            mouth_open_ratio=None,
        )
    except Exception as exc:
        return _empty_result([f"opencv_failed:{exc.__class__.__name__}"], "opencv")


def landmark_metrics(landmarks: np.ndarray) -> dict:
    pts = np.asarray(landmarks, dtype=np.float64)
    left_outer = pts[33, :2]
    left_inner = pts[133, :2]
    right_outer = pts[263, :2]
    right_inner = pts[362, :2]
    left_eye = (left_outer + left_inner) / 2
    right_eye = (right_outer + right_inner) / 2
    eye_vector = right_eye - left_eye
    interocular = float(np.linalg.norm(eye_vector) or 1e-6)
    roll = math.degrees(math.atan2(float(eye_vector[1]), float(eye_vector[0])))

    nose = pts[1, :2]
    eye_mid = (left_eye + right_eye) / 2
    mouth_mid = (pts[13, :2] + pts[14, :2]) / 2
    yaw = float(np.clip((nose[0] - eye_mid[0]) / interocular * 55, -60, 60))
    pitch = float(np.clip((nose[1] - (eye_mid[1] + mouth_mid[1]) / 2) / interocular * 70, -60, 60))

    left_eye_open = _distance(pts[159, :2], pts[145, :2]) / max(_distance(left_outer, left_inner), 1e-6)
    right_eye_open = _distance(pts[386, :2], pts[374, :2]) / max(_distance(right_outer, right_inner), 1e-6)
    mouth_open = _distance(pts[13, :2], pts[14, :2]) / max(_distance(pts[61, :2], pts[291, :2]), 1e-6)

    return {
        "yaw": round(yaw, 4),
        "pitch": round(pitch, 4),
        "roll": round(float(roll), 4),
        "eye_open_ratio": round(float((left_eye_open + right_eye_open) / 2), 4),
        "mouth_open_ratio": round(float(mouth_open), 4),
    }


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def _bbox(landmarks: np.ndarray) -> tuple[float, float, float, float]:
    mins = landmarks[:, :2].min(axis=0)
    maxs = landmarks[:, :2].max(axis=0)
    return (float(mins[0]), float(mins[1]), float(maxs[0] - mins[0]), float(maxs[1] - mins[1]))


def _resize_for_detection(image: np.ndarray, max_side: int) -> tuple[np.ndarray, float]:
    height, width = image.shape[:2]
    side = max(height, width)
    if side <= max_side:
        return image, 1.0
    scale = max_side / side
    try:
        import cv2

        resized = cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    except Exception:
        from PIL import Image

        resized = np.asarray(Image.fromarray(image).resize((int(width * scale), int(height * scale))))
    return resized, scale


def _empty_result(warnings: list[str], method: str) -> DetectionResult:
    return DetectionResult(
        landmarks=None,
        bbox=None,
        confidence=0.0,
        yaw=None,
        pitch=None,
        roll=None,
        eye_open_ratio=None,
        mouth_open_ratio=None,
        warnings=warnings,
        method=method,
    )
