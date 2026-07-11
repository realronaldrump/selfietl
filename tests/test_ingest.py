from pathlib import Path

from PIL import Image

from selfietl.config import load_config
from selfietl.db import Database
from selfietl.pipeline.ingest import create_project, scan_project
from selfietl.pipeline.images import sha1_file


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


def test_scan_keeps_distinct_files_that_share_a_perceptual_hash(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (64, 64), (20, 90, 130)).save(source / "2026-05-01_100000.jpg", "JPEG")
    Image.new("RGB", (64, 64), (80, 90, 130)).save(source / "2026-05-02_100000.jpg", "JPEG")
    monkeypatch.setattr("selfietl.pipeline.ingest.perceptual_hash", lambda _path: "same-phash")
    config = load_config(data_dir)
    db = Database(config.db_path)
    project_id = create_project(db, "Test", str(source))

    result = scan_project(db, config, project_id)

    rows = db.fetchall("SELECT hash, path FROM photos ORDER BY captured_at")
    assert result["perceptual_duplicates"] == 1
    assert len(rows) == 2
    assert all(row["hash"] == sha1_file(Path(row["path"])) for row in rows)


def test_rescan_unlinks_photos_removed_from_source_folder(tmp_path: Path):
    data_dir = tmp_path / "data"
    source = tmp_path / "source"
    source.mkdir()
    first_path = source / "2026-05-01_100000.jpg"
    second_path = source / "2026-05-02_100000.jpg"
    Image.new("RGB", (64, 64), (20, 90, 130)).save(first_path, "JPEG")
    Image.new("RGB", (64, 64), (130, 90, 20)).save(second_path, "JPEG")
    config = load_config(data_dir)
    db = Database(config.db_path)
    project_id = create_project(db, "Test", str(source))
    scan_project(db, config, project_id)
    removed_hash = sha1_file(first_path)
    first_path.unlink()

    result = scan_project(db, config, project_id)

    assert result["unlinked"] == 1
    assert db.fetchone(
        "SELECT 1 FROM project_photos WHERE project_id = ? AND photo_hash = ?",
        (project_id, removed_hash),
    ) is None
    assert db.fetchone("SELECT 1 FROM photos WHERE hash = ?", (removed_hash,)) is None
