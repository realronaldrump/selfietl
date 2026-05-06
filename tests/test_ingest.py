from pathlib import Path

from PIL import Image

from selfietl.config import load_config
from selfietl.db import Database
from selfietl.pipeline.ingest import create_project, scan_project


def test_scan_is_idempotent_for_exact_duplicate_bytes(tmp_path: Path):
    data_dir = tmp_path / "data"
    source = tmp_path / "source"
    source.mkdir()
    image = Image.new("RGB", (64, 64), (20, 90, 130))
    image.save(source / "a.jpg", "JPEG")
    image.save(source / "b.jpg", "JPEG")
    config = load_config(data_dir)
    db = Database(config.db_path)
    project_id = create_project(db, "Test", str(source))

    first = scan_project(db, config, project_id)
    second = scan_project(db, config, project_id)
    photo_count = db.fetchone("SELECT COUNT(*) AS count FROM photos")["count"]
    project_count = db.fetchone("SELECT COUNT(*) AS count FROM project_photos WHERE project_id = ?", (project_id,))["count"]

    assert first["total_files"] == 2
    assert second["inserted"] == 0
    assert photo_count == 1
    assert project_count == 1
