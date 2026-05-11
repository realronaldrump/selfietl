from __future__ import annotations

from fastapi.testclient import TestClient

from selfietl.config import load_config
from selfietl.db import Database
from selfietl.server import create_app


def test_delete_failed_render_history_removes_rows_files_and_cache(tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("p", str(config.inbox_dir), "2026-05-09 10:00:00"),
    )
    failed_output = config.exports_dir / "failed.mp4"
    done_output = config.exports_dir / "done.mp4"
    failed_output.parent.mkdir(parents=True, exist_ok=True)
    failed_output.write_bytes(b"failed-video")
    done_output.write_bytes(b"done-video")
    failed_id = db.execute(
        """
        INSERT INTO renders (project_id, output_path, started_at, finished_at, status, error)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (project_id, str(failed_output), "2026-05-09 10:00:00", "2026-05-09 10:01:00", "failed", "boom"),
    )
    done_id = db.execute(
        """
        INSERT INTO renders (project_id, output_path, started_at, finished_at, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, str(done_output), "2026-05-09 11:00:00", "2026-05-09 11:01:00", "done"),
    )
    cache_dir = config.render_cache_dir / f"render_{failed_id}"
    cache_dir.mkdir(parents=True)
    (cache_dir / "frame.jpg").write_bytes(b"cache")

    app = create_app(config)
    with TestClient(app) as client:
        response = client.delete(f"/api/projects/{project_id}/renders")

    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted_render_ids"] == [failed_id]
    assert not failed_output.exists()
    assert done_output.exists()
    assert not cache_dir.exists()
    assert db.fetchone("SELECT id FROM renders WHERE id = ?", (failed_id,)) is None
    assert db.fetchone("SELECT id FROM renders WHERE id = ?", (done_id,)) is not None


def test_delete_done_render_requires_explicit_render_delete(tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("p", str(config.inbox_dir), "2026-05-09 10:00:00"),
    )
    output = config.exports_dir / "done.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"done-video")
    render_id = db.execute(
        """
        INSERT INTO renders (project_id, output_path, started_at, finished_at, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, str(output), "2026-05-09 11:00:00", "2026-05-09 11:01:00", "done"),
    )

    app = create_app(config)
    with TestClient(app) as client:
        response = client.delete(f"/api/renders/{render_id}")

    assert response.status_code == 200
    assert response.json()["deleted_render_ids"] == [render_id]
    assert not output.exists()
    assert db.fetchone("SELECT id FROM renders WHERE id = ?", (render_id,)) is None


def test_render_file_supports_head_requests(tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("p", str(config.inbox_dir), "2026-05-09 10:00:00"),
    )
    output = config.exports_dir / "done.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"done-video")
    render_id = db.execute(
        """
        INSERT INTO renders (project_id, output_path, started_at, finished_at, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (project_id, str(output), "2026-05-09 11:00:00", "2026-05-09 11:01:00", "done"),
    )

    app = create_app(config)
    with TestClient(app) as client:
        response = client.head(f"/api/renders/{render_id}/file")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["content-length"] == str(output.stat().st_size)
