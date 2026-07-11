from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from selfietl.api import auto_render as auto_render_api
from selfietl.config import load_config
from selfietl.db import Database
from selfietl.jobs.runner import runner
from selfietl.scheduler import default_settings, load_settings, save_settings
from selfietl.server import create_app


def test_run_auto_render_now_starts_job_from_async_route(tmp_path, monkeypatch):
    config = load_config(tmp_path / "home")
    settings = default_settings()
    settings.enabled = False
    save_settings(config, settings)

    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("Inbox", str(config.inbox_dir), "2026-05-09 12:00:00"),
    )
    runner.jobs.clear()
    runner.resume_new_jobs()

    def fake_kick_off_auto_render(db_arg, config_arg, project_id_arg, settings_arg, *, job_name=None):
        asyncio.get_running_loop()
        assert db_arg is not None
        assert config_arg == config
        assert project_id_arg == project_id
        assert job_name == f"manual_auto_render:{project_id}"
        return "job-123", 456

    monkeypatch.setattr(auto_render_api, "kick_off_auto_render", fake_kick_off_auto_render)
    monkeypatch.setattr(auto_render_api, "project_has_active_photos", lambda *_args: True)

    app = create_app(config)
    with TestClient(app) as client:
        response = client.post("/api/auto-render/run")

    assert response.status_code == 200
    assert response.json()["job_id"] == "job-123"


def test_update_auto_render_rejects_out_of_range_time(tmp_path):
    config = load_config(tmp_path / "home")
    app = create_app(config)

    with TestClient(app) as client:
        response = client.patch("/api/auto-render", json={"time": "99:99"})

    assert response.status_code == 400
    assert load_settings(config).time == "03:00"


def test_update_auto_render_rejects_invalid_render_config(tmp_path):
    config = load_config(tmp_path / "home")
    app = create_app(config)

    with TestClient(app) as client:
        response = client.patch(
            "/api/auto-render",
            json={"render_config": {"fps": 0, "resolution": "poster"}},
        )

    assert response.status_code == 400
    settings = load_settings(config)
    assert settings.render_config["fps"] == 30
    assert settings.render_config["resolution"] == "1080_vertical"
