from PIL import Image

from selfietl.api.system import default_source_folder, reset_app_data
from selfietl.config import load_config
from selfietl.db import Database
from selfietl.pipeline.ingest import create_project, scan_project


def test_default_source_folder_is_created(tmp_path):
    config = load_config(tmp_path / "home")
    path = default_source_folder(config)

    assert path.exists()
    assert path.is_dir()
    assert path.name == "inbox"


def test_reset_app_data_keeps_inbox_originals(tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    inbox = default_source_folder(config)
    photo_path = inbox / "selfie_2026-05-08_100000.jpg"
    Image.new("RGB", (32, 32), (10, 40, 90)).save(photo_path, "JPEG")
    project_id = create_project(db, "Inbox", str(inbox))
    scan_project(db, config, project_id)
    assert db.fetchone("SELECT COUNT(*) AS count FROM photos")["count"] == 1

    reset_app_data(config, db)

    assert photo_path.exists()
    assert db.fetchone("SELECT COUNT(*) AS count FROM photos")["count"] == 0
    assert db.fetchone("SELECT COUNT(*) AS count FROM projects")["count"] == 0
