from __future__ import annotations

import json
import math
from datetime import date, timedelta

import numpy as np
from fastapi.testclient import TestClient

from selfietl.config import load_config
from selfietl.db import Database
from selfietl.pipeline.face_shape import (
    FACE_OVAL,
    compare_periods,
    extract_features,
    get_project_trend,
    recompute_project,
    update_calibration,
)
from selfietl.server import create_app


def synthetic_landmarks(fullness: float = 1.0) -> np.ndarray:
    points = np.full((478, 3), 0.5, dtype=np.float64)
    angles = np.linspace(-math.pi / 2, 3 * math.pi / 2, len(FACE_OVAL), endpoint=False)
    for index, angle in zip(FACE_OVAL, angles):
        y = 0.53 + math.sin(angle) * 0.32
        lower_emphasis = 1.0 + max(0.0, (y - 0.45) / 0.4) * (fullness - 1.0)
        x = 0.5 + math.cos(angle) * 0.25 * fullness * lower_emphasis
        points[index, :2] = (x, y)
    points[33, :2] = points[133, :2] = (0.4, 0.4)
    points[263, :2] = points[362, :2] = (0.6, 0.4)
    return points


def transformed(points: np.ndarray, scale: float, angle: float, translation: tuple[float, float]) -> np.ndarray:
    rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    result = points.copy()
    result[:, :2] = points[:, :2] @ (scale * rotation).T + np.asarray(translation)
    return result


def create_project_with_landmarks(tmp_path, fullness_values: list[float]) -> tuple[Database, int]:
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("shape", str(config.inbox_dir), "2024-01-01 09:00:00"),
    )
    start = date(2024, 1, 1)
    for index, fullness in enumerate(fullness_values):
        photo_hash = f"shape-{index}"
        landmarks_path = config.landmarks_dir / f"{photo_hash}.npz"
        np.savez_compressed(landmarks_path, landmarks=synthetic_landmarks(fullness).astype(np.float32))
        captured = start + timedelta(days=index * 14)
        db.execute(
            """
            INSERT INTO photos (
                hash, path, captured_at, width, height, landmarks_path, quality_score,
                yaw, pitch, roll, mouth_open_ratio, skipped
            ) VALUES (?, ?, ?, 3024, 4032, ?, 0.9, 0, 6, 0, 0.01, 0)
            """,
            (photo_hash, str(config.inbox_dir / f"{photo_hash}.jpg"), f"{captured.isoformat()} 10:00:00", str(landmarks_path)),
        )
        db.execute("INSERT INTO project_photos (project_id, photo_hash) VALUES (?, ?)", (project_id, photo_hash))
    return db, project_id


def test_face_shape_features_are_similarity_invariant_and_fullness_sensitive():
    base = synthetic_landmarks(1.0)
    transformed_points = transformed(base, scale=2.3, angle=0.42, translation=(4.0, -2.0))
    base_features, _ = extract_features(base)
    transformed_features, _ = extract_features(transformed_points)

    assert transformed_features == pytest_approx_dict(base_features)

    fuller_features, _ = extract_features(synthetic_landmarks(1.15))
    assert fuller_features["lower_face_width"] > base_features["lower_face_width"]
    assert fuller_features["lower_face_area"] > base_features["lower_face_area"]
    assert {
        "jaw_cheek_ratio",
        "outline_roundness",
        "chin_cheek_ratio",
        "temple_cheek_ratio",
        "lower_face_height",
        "jaw_angle",
        "outline_asymmetry",
    }.issubset(base_features)


def test_recompute_builds_frozen_baseline_trend_and_comparison(tmp_path):
    values = [0.9] * 6 + [1.1] * 6
    db, project_id = create_project_with_landmarks(tmp_path, values)

    result = recompute_project(db, project_id)
    assert result["status"] == "ready"
    profile_before = db.fetchone("SELECT baseline_json FROM face_shape_profiles WHERE project_id = ?", (project_id,))["baseline_json"]

    trend = get_project_trend(db, project_id)
    assert trend["status"] == "ready"
    assert trend["coverage"]["eligible_photos"] == 12
    assert any(point.get("trend_index") is not None for point in trend["points"])
    assert trend["statistics"]["status"] == "ready"
    assert trend["statistics"]["direction"] == "increasing"
    assert trend["insights"]
    assert all("components" in point for point in trend["points"] if not point.get("is_break"))

    comparison = compare_periods(
        db,
        project_id,
        {"start": "2024-01-01", "end": "2024-03-20"},
        {"start": "2024-03-21", "end": "2024-06-30"},
    )
    assert comparison["delta"] > 0
    assert comparison["conclusion"] == "fuller"

    recompute_project(db, project_id, rebuild_baseline=False)
    profile_after = db.fetchone("SELECT baseline_json FROM face_shape_profiles WHERE project_id = ?", (project_id,))["baseline_json"]
    assert profile_after == profile_before


def test_calibration_validates_ranges_and_api_returns_trend(tmp_path):
    db, project_id = create_project_with_landmarks(tmp_path, [0.9] * 6 + [1.1] * 6)
    recompute_project(db, project_id)

    calibration = update_calibration(
        db,
        project_id,
        {"start": "2024-01-01", "end": "2024-03-20"},
        {"start": "2024-03-21", "end": "2024-06-30"},
    )
    assert calibration["status"] == "calibrated"
    assert calibration["lighter"]["used"] >= 5
    assert calibration["fuller"]["used"] >= 5

    app = create_app(load_config(tmp_path / "home"))
    with TestClient(app) as client:
        response = client.get(f"/api/projects/{project_id}/face-shape")
        compare = client.post(
            f"/api/projects/{project_id}/face-shape/compare",
            json={
                "a": {"start": "2024-01-01", "end": "2024-03-20"},
                "b": {"start": "2024-03-21", "end": "2024-06-30"},
            },
        )
        csv_export = client.get(f"/api/projects/{project_id}/face-shape/export?format=csv")
        json_export = client.get(f"/api/projects/{project_id}/face-shape/export?format=json")

    assert response.status_code == 200
    assert response.json()["metric"]["baseline_value"] == 0
    assert compare.status_code == 200
    assert compare.json()["disclaimer"].startswith("Face Shape Index")
    assert csv_export.status_code == 200
    assert "attachment;" in csv_export.headers["content-disposition"]
    assert "outline_asymmetry" in csv_export.text.splitlines()[0]
    assert json_export.status_code == 200
    assert json_export.json()["analysis"]["statistics"]["status"] == "ready"


def test_trends_do_not_bridge_capture_profile_changes(tmp_path):
    db, project_id = create_project_with_landmarks(tmp_path, [0.9] * 6 + [1.1] * 6)
    db.execute(
        "UPDATE photos SET camera_model = 'new-camera' WHERE captured_at >= ?",
        ("2024-03-21 00:00:00",),
    )

    recompute_project(db, project_id)
    trend = get_project_trend(db, project_id)

    points = [point for point in trend["points"] if not point.get("is_break")]
    assert {point["segment"] for point in points} == {0, 1}
    assert any(event["type"] == "capture_profile_change" for event in trend["events"])
    assert trend["summary"]["direction_90d"] == "steady"
    assert trend["statistics"]["observation_days"] == 6


def pytest_approx_dict(values: dict[str, float]):
    import pytest

    return {key: pytest.approx(value, rel=1e-7, abs=1e-7) for key, value in values.items()}


def test_face_shape_migration_is_idempotent(tmp_path):
    config = load_config(tmp_path / "home")
    Database(config.db_path)
    db = Database(config.db_path)
    tables = {row["name"] for row in db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"face_shape_measurements", "face_shape_profiles"}.issubset(tables)
