import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from selfietl.config import load_config
from selfietl.server import create_app


def test_spa_fallback_does_not_serve_files_outside_web_dist(tmp_path: Path):
    app = create_app(load_config(tmp_path / "home"))

    with TestClient(app) as client:
        response = client.get("/%2e%2e/%2e%2e/pyproject.toml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<div id=\"root\"></div>" in response.text
    assert "[build-system]" not in response.text


def test_spa_fallback_does_not_mask_unknown_api_routes(tmp_path: Path):
    app = create_app(load_config(tmp_path / "home"))

    with TestClient(app) as client:
        response = client.get("/api/not-a-real-endpoint")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_importing_server_module_does_not_initialize_default_data(tmp_path: Path):
    data_dir = tmp_path / "must-not-be-created"
    env = os.environ.copy()
    env["SELFIE_TL_HOME"] = str(data_dir)

    completed = subprocess.run(
        [sys.executable, "-c", "import selfietl.server"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    assert not data_dir.exists()
