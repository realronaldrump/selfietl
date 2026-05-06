from selfietl.config import load_config
from selfietl.db import Database


def test_database_migration_creates_catalog_tables(tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)

    tables = {
        row["name"]
        for row in db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    }

    assert {"photos", "projects", "renders", "project_photos"}.issubset(tables)
