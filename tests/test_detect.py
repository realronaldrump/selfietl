from pathlib import Path

import numpy as np

from selfietl.config import load_config
from selfietl.pipeline import detect as detect_pipeline
from selfietl.pipeline.detect import DetectionResult


class FakeFaceMesh:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _landmark_result() -> DetectionResult:
    return DetectionResult(
        landmarks=np.ones((478, 3), dtype=np.float64),
        bbox=(0.2, 0.2, 0.5, 0.5),
        confidence=1.0,
        yaw=0.0,
        pitch=0.0,
        roll=0.0,
        eye_open_ratio=0.2,
        mouth_open_ratio=0.01,
        warnings=[],
        method="mediapipe",
    )


def test_detect_landmarks_creates_detector_for_one_off_calls(tmp_path, monkeypatch):
    config = load_config(tmp_path / "home")
    created_face_mesh = FakeFaceMesh()

    def fake_create(received_config):
        assert received_config is config
        return created_face_mesh

    def fake_mediapipe(path, received_config, *, face_mesh=None):
        assert path == Path("selfie.jpg")
        assert received_config is config
        assert face_mesh is created_face_mesh
        return _landmark_result()

    def fake_opencv(*args, **kwargs):
        raise AssertionError("OpenCV fallback should not run when MediaPipe succeeds")

    monkeypatch.setattr(detect_pipeline, "_create_mediapipe_face_mesh", fake_create)
    monkeypatch.setattr(detect_pipeline, "_detect_with_mediapipe", fake_mediapipe)
    monkeypatch.setattr(detect_pipeline, "_detect_with_opencv", fake_opencv)

    result = detect_pipeline.detect_landmarks(Path("selfie.jpg"), config)

    assert result.landmarks is not None
    assert result.method == "mediapipe"
    assert created_face_mesh.closed is True


def test_detect_landmarks_keeps_injected_detector_open(tmp_path, monkeypatch):
    config = load_config(tmp_path / "home")
    injected_face_mesh = FakeFaceMesh()

    def fake_create(*args, **kwargs):
        raise AssertionError("Batch callers already supplied a detector")

    def fake_mediapipe(path, received_config, *, face_mesh=None):
        assert face_mesh is injected_face_mesh
        return _landmark_result()

    monkeypatch.setattr(detect_pipeline, "_create_mediapipe_face_mesh", fake_create)
    monkeypatch.setattr(detect_pipeline, "_detect_with_mediapipe", fake_mediapipe)

    result = detect_pipeline.detect_landmarks(Path("selfie.jpg"), config, face_mesh=injected_face_mesh)

    assert result.landmarks is not None
    assert injected_face_mesh.closed is False


def test_detect_project_falls_back_when_mediapipe_cannot_initialize(tmp_path, monkeypatch):
    from selfietl.db import Database

    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("p", str(tmp_path), "2026-05-08 09:00:00"),
    )
    db.execute(
        "INSERT INTO photos (hash, path, captured_at) VALUES (?, ?, ?)",
        ("hash", str(tmp_path / "photo.jpg"), "2026-05-08 10:00:00"),
    )
    db.execute(
        "INSERT INTO project_photos (project_id, photo_hash) VALUES (?, ?)",
        (project_id, "hash"),
    )

    def unavailable(_config):
        raise RuntimeError("MediaPipe unavailable")

    monkeypatch.setattr(detect_pipeline, "_create_mediapipe_face_mesh", unavailable)
    monkeypatch.setattr(
        detect_pipeline,
        "detect_landmarks",
        lambda *args, **kwargs: DetectionResult(
            landmarks=None,
            bbox=None,
            confidence=0,
            yaw=None,
            pitch=None,
            roll=None,
            eye_open_ratio=None,
            mouth_open_ratio=None,
            warnings=["no_face_detected"],
            method="opencv",
        ),
    )

    result = detect_pipeline.detect_project(db, config, project_id)

    assert result["skipped"] == 1
    assert db.fetchone("SELECT skip_reason FROM photos WHERE hash = ?", ("hash",))["skip_reason"] == "no_face_detected"
