from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from selfietl.config import load_config
from selfietl.db import Database
from selfietl.pipeline.face_shape import FACE_OVAL
from selfietl.pipeline.hair import (
    _canvas_assets,
    _signed_distance,
    create_hair_export,
    hair_metrics,
    mask_iou,
    project_hair_revision,
    refine_confidence_mask,
    render_hair_export,
    update_haircut_suggestions,
)
from selfietl.server import create_app


def _landmarks() -> np.ndarray:
    points = np.full((478, 3), 0.5, dtype=np.float32)
    points[33, :2] = points[133, :2] = (0.4, 0.42)
    points[263, :2] = points[362, :2] = (0.6, 0.42)
    angles = np.linspace(-np.pi / 2, 3 * np.pi / 2, len(FACE_OVAL), endpoint=False)
    for index, angle in zip(FACE_OVAL, angles):
        points[index, :2] = (0.5 + np.cos(angle) * 0.24, 0.52 + np.sin(angle) * 0.32)
    return points


def _project(tmp_path: Path, masks: list[np.ndarray]):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    canonical = config.data_dir / "canonical.npz"
    np.savez_compressed(canonical, landmarks=_landmarks(), target_size=np.array([100, 120], dtype=np.int32))
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at, canonical_landmarks_path) VALUES ('hair', ?, '2024-01-01', ?)",
        (str(config.inbox_dir), str(canonical)),
    )
    start = date(2024, 1, 1)
    for index, mask in enumerate(masks):
        photo_hash = f"hair-{index}"
        source = config.inbox_dir / f"{photo_hash}.jpg"
        Image.new("RGB", (100, 120), "white").save(source)
        landmark_path = config.landmarks_dir / f"{photo_hash}.npz"
        np.savez_compressed(landmark_path, landmarks=_landmarks())
        aligned_landmarks = _landmarks().copy()
        aligned_landmarks[:, 0] *= 100
        aligned_landmarks[:, 1] *= 120
        aligned_landmarks_path = config.aligned_landmarks_dir / f"{photo_hash}.npz"
        np.savez_compressed(
            aligned_landmarks_path,
            landmarks=aligned_landmarks,
            matrix=np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32),
            target_size=np.array([100, 120], dtype=np.int32),
        )
        mask_path = config.hair_aligned_masks_dir / f"{photo_hash}.png"
        Image.fromarray(mask.astype(np.uint8) * 255).save(mask_path)
        source_mask_path = config.hair_source_masks_dir / f"{photo_hash}.npz"
        np.savez_compressed(source_mask_path, confidence=mask.astype(np.float16))
        captured = start + timedelta(days=index * 3)
        db.execute(
            "INSERT INTO photos (hash, path, captured_at, width, height, landmarks_path, skipped) VALUES (?, ?, ?, 100, 120, ?, 0)",
            (photo_hash, str(source), f"{captured.isoformat()} 10:00:00", str(landmark_path)),
        )
        db.execute("INSERT INTO project_photos (project_id, photo_hash) VALUES (?, ?)", (project_id, photo_hash))
        metrics = hair_metrics(mask, aligned_landmarks)
        stat = aligned_landmarks_path.stat()
        signature = __import__("hashlib").sha256(f"hair-v1|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
        db.execute(
            """
            INSERT INTO hair_measurements (
                photo_hash, algorithm_version, source_signature, alignment_signature,
                source_mask_path, aligned_mask_path, metrics_json, quality_score,
                eligible, reasons_json, computed_at, updated_at
            ) VALUES (?, 'hair-v1', ?, ?, ?, ?, ?, .9, 1, '[]', '2024-01-01', '2024-01-01')
            """,
            (photo_hash, f"source-{index}", signature, str(source_mask_path), str(mask_path), json.dumps(metrics)),
        )
    return config, db, project_id


def test_refine_confidence_mask_keeps_connected_wisps_and_removes_noise():
    confidence = np.zeros((64, 64), dtype=np.float32)
    confidence[12:36, 18:46] = 0.8
    confidence[8:13, 30:33] = 0.35
    confidence[55, 55] = 0.9

    mask, quality, reasons = refine_confidence_mask(confidence)

    assert mask[9, 31]
    assert not mask[55, 55]
    assert quality > 0.7
    assert "implausible_hair_area" not in reasons


def test_mask_metrics_and_iou_are_similarity_friendly():
    mask = np.zeros((120, 100), dtype=bool)
    mask[12:55, 24:76] = True
    landmarks = _landmarks()
    landmarks[:, 0] *= 100
    landmarks[:, 1] *= 120

    metrics = hair_metrics(mask, landmarks)

    assert metrics["area"] > 0
    assert metrics["top_extent"] > 0
    assert mask_iou(mask, mask) == 1


def test_signed_distance_interpolation_preserves_exact_endpoints():
    first = np.zeros((40, 40), dtype=bool)
    first[5:30, 5:20] = True
    second = np.zeros((40, 40), dtype=bool)
    second[8:26, 12:34] = True
    first_sdf = _signed_distance(first)
    second_sdf = _signed_distance(second)

    assert np.array_equal(first_sdf >= 0, first)
    assert np.array_equal(second_sdf >= 0, second)


def test_canvas_uses_one_canonical_face_outline_for_every_day(tmp_path):
    mask = np.zeros((120, 100), dtype=bool)
    mask[8:48, 22:78] = True
    config, db, _ = _project(tmp_path, [mask, mask])
    second = config.aligned_landmarks_dir / "hair-1.npz"
    with np.load(second) as payload:
        moved = np.asarray(payload["landmarks"]).copy()
        matrix = payload["matrix"]
        target = payload["target_size"]
    moved[:, 0] += 12
    np.savez_compressed(second, landmarks=moved, matrix=matrix, target_size=target)

    _, first_base = _canvas_assets(db, config, "hair-0", 360, 450)
    _, second_base = _canvas_assets(db, config, "hair-1", 360, 450)

    assert np.array_equal(np.asarray(first_base), np.asarray(second_base))


def test_persistent_shorter_shape_creates_confirmable_haircut(tmp_path):
    long = np.zeros((120, 100), dtype=bool)
    long[5:92, 12:88] = True
    short = np.zeros((120, 100), dtype=bool)
    short[12:58, 24:76] = True
    config, db, project_id = _project(tmp_path, [long, short, short])

    count = update_haircut_suggestions(db, config, project_id)
    event = db.fetchone("SELECT * FROM haircut_events WHERE project_id = ?", (project_id,))

    assert count == 1
    assert event["status"] == "suggested"
    assert event["first_after_photo_hash"] == "hair-1"


def test_hair_api_manifest_exclusion_and_manual_haircuts(tmp_path):
    mask = np.zeros((120, 100), dtype=bool)
    mask[8:58, 20:80] = True
    config, db, project_id = _project(tmp_path, [mask, mask])
    revision_before = project_hair_revision(db, config, project_id)
    app = create_app(config)

    with TestClient(app) as client:
        manifest = client.get(f"/api/projects/{project_id}/hair")
        excluded = client.patch("/api/photos/hair-0/hair", json={"excluded": True})
        haircut = client.post(f"/api/projects/{project_id}/haircuts", json={"event_date": "2024-01-02"})
        confirmed = client.get(f"/api/projects/{project_id}/hair")

    assert manifest.status_code == 200
    assert manifest.json()["coverage"]["included"] == 2
    assert excluded.status_code == 200
    assert haircut.status_code == 200
    assert confirmed.json()["coverage"]["included"] == 1
    assert confirmed.json()["haircuts"][0]["status"] == "confirmed"
    assert project_hair_revision(db, config, project_id) != revision_before


def test_hair_export_writes_browser_compatible_mp4(tmp_path):
    mask_a = np.zeros((120, 100), dtype=bool)
    mask_a[8:65, 18:82] = True
    mask_b = np.zeros((120, 100), dtype=bool)
    mask_b[12:56, 24:76] = True
    config, db, project_id = _project(tmp_path, [mask_a, mask_b])
    payload = {"start_date": None, "end_date": None, "seconds_per_selfie": 0.25, "width": 360, "height": 450}
    export_id = create_hair_export(db, config, project_id, payload)

    result = render_hair_export(db, config, project_id, export_id, payload)
    row = db.fetchone("SELECT * FROM hair_exports WHERE id = ?", (export_id,))

    assert result["frames"] > 2
    assert row["status"] == "done"
    assert Path(row["output_path"]).read_bytes()[4:8] == b"ftyp"


def test_hair_migration_is_idempotent(tmp_path):
    config = load_config(tmp_path / "home")
    Database(config.db_path)
    db = Database(config.db_path)
    tables = {row["name"] for row in db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"hair_measurements", "haircut_events", "hair_exports"}.issubset(tables)
