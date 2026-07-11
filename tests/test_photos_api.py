import json
from pathlib import Path

from fastapi.testclient import TestClient

from selfietl.config import load_config
from selfietl.db import Database
from selfietl.server import create_app


def test_patch_photo_updates_captured_at(tmp_path: Path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("p", str(config.inbox_dir), "2026-05-08 09:00:00"),
    )
    source = config.inbox_dir / "selfie.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"fake")
    db.execute(
        """
        INSERT INTO photos (hash, path, captured_at, skipped, user_override, warnings_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("photo-1", str(source), "2026-05-08 09:00:00", 0, 0, "[]"),
    )
    db.execute(
        "INSERT INTO project_photos (project_id, photo_hash, added_at) VALUES (?, ?, ?)",
        (project_id, "photo-1", "2026-05-08 09:00:00"),
    )

    app = create_app(config)
    with TestClient(app) as client:
        response = client.patch("/api/photos/photo-1", json={"captured_at": "2026-05-03T08:09:10"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["captured_at"] == "2026-05-03T08:09:10"
    assert payload["user_override"] is True
    row = db.fetchone("SELECT captured_at, user_override, warnings_json FROM photos WHERE hash = ?", ("photo-1",))
    assert str(row["captured_at"]) == "2026-05-03 08:09:10"
    assert row["user_override"] == 1
    assert "captured_at_user_override" in json.loads(row["warnings_json"])


def test_patch_photo_rejects_invalid_captured_at(tmp_path: Path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    source = config.inbox_dir / "selfie.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"fake")
    db.execute(
        """
        INSERT INTO photos (hash, path, captured_at, skipped, user_override, warnings_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("photo-1", str(source), "2026-05-08 09:00:00", 0, 0, "[]"),
    )

    app = create_app(config)
    with TestClient(app) as client:
        response = client.patch("/api/photos/photo-1", json={"captured_at": "not-a-date"})

    assert response.status_code == 400
    row = db.fetchone("SELECT captured_at FROM photos WHERE hash = ?", ("photo-1",))
    assert str(row["captured_at"]) == "2026-05-08 09:00:00"


def test_patch_photo_preserves_skip_reason_when_field_is_omitted(tmp_path: Path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    db.execute(
        """
        INSERT INTO photos (hash, path, captured_at, skipped, skip_reason)
        VALUES (?, ?, ?, 1, ?)
        """,
        ("preserve-reason", "/tmp/photo.jpg", "2026-05-08 12:00:00", "low_quality"),
    )
    app = create_app(config)

    with TestClient(app) as client:
        response = client.patch(
            "/api/photos/preserve-reason",
            json={"captured_at": "2026-05-09T13:00:00"},
        )

    assert response.status_code == 200
    assert response.json()["skipped"] is True
    assert response.json()["skip_reason"] == "low_quality"
