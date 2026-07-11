import math

import numpy as np

from selfietl.config import load_config
from selfietl.db import Database
from selfietl.pipeline import canonical
from selfietl.pipeline.canonical import apply_transform, similarity_transform


def test_similarity_transform_recovers_known_transform():
    rng = np.random.default_rng(42)
    source = rng.normal(size=(80, 2)) * 120
    angle = math.radians(18)
    scale = 1.37
    rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    translation = np.array([240.0, -91.0])
    target = source @ (scale * rotation).T + translation

    matrix = similarity_transform(source, target)
    recovered = apply_transform(source, matrix)

    assert np.max(np.abs(recovered - target)) < 1e-6


def test_similarity_transform_handles_synthetic_photo_set_within_one_pixel():
    rng = np.random.default_rng(7)
    canonical = rng.uniform([250, 180], [760, 900], size=(468, 2))
    for idx in range(50):
        angle = math.radians(-12 + idx * 0.5)
        scale = 0.88 + idx * 0.006
        rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        translation = np.array([idx * 3.0, -idx * 1.7])
        observed = canonical @ (scale * rotation).T + translation
        matrix = similarity_transform(observed, canonical)
        aligned = apply_transform(observed, matrix)
        rms = np.sqrt(np.mean(np.sum((aligned - canonical) ** 2, axis=1)))
        assert rms < 1.0


def test_compute_canonical_skips_invalid_and_trims_mixed_landmark_counts(tmp_path, monkeypatch):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("p", str(tmp_path), "2026-05-08 09:00:00"),
    )
    shapes = {
        "invalid": np.array([[0.1, 0.1], [0.2, 0.2]], dtype=np.float32),
        "three": np.array([[0.1, 0.1], [0.9, 0.1], [0.5, 0.8]], dtype=np.float32),
        "four": np.array([[0.1, 0.1], [0.9, 0.1], [0.5, 0.8], [0.5, 0.5]], dtype=np.float32),
    }
    for index, (photo_hash, points) in enumerate(shapes.items()):
        path = config.landmarks_dir / f"{photo_hash}.npz"
        np.savez_compressed(path, landmarks=points)
        db.execute(
            """
            INSERT INTO photos (
                hash, path, captured_at, width, height, landmarks_path,
                quality_score, skipped
            ) VALUES (?, ?, ?, 100, 100, ?, 0.25, 0)
            """,
            (photo_hash, str(tmp_path / f"{photo_hash}.jpg"), f"2026-05-0{index + 1} 10:00:00", str(path)),
        )
        db.execute(
            "INSERT INTO project_photos (project_id, photo_hash) VALUES (?, ?)",
            (project_id, photo_hash),
        )

    seen_confidences = []
    original_score = canonical.compute_quality_score

    def record_confidence(**kwargs):
        seen_confidences.append(kwargs["confidence"])
        return original_score(**kwargs)

    monkeypatch.setattr(canonical, "compute_quality_score", record_confidence)

    path = canonical.compute_canonical_face(db, config, project_id)

    with np.load(path) as payload:
        assert payload["landmarks"].shape == (3, 2)
        assert payload["hashes"].tolist() == ["three", "four"]
    assert seen_confidences == [1.0, 1.0]
    assert db.fetchone("SELECT quality_score FROM photos WHERE hash = ?", ("invalid",))["quality_score"] == 0.25
