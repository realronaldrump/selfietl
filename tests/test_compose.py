import pytest

from selfietl.config import RenderConfig, load_config
from selfietl.db import Database
from selfietl.pipeline.compose import (
    _default_output_path,
    _filter_rows_by_date,
    _latest_row_per_day,
    _preview_output_path,
    _replace_previous_render_outputs,
    _run_ffmpeg,
)


def test_default_output_path_is_stable_per_project(tmp_path):
    config = load_config(tmp_path / "home")

    first = _default_output_path(config, 7)
    second = _default_output_path(config, 7)

    assert first == second == config.exports_dir / "timelapse_7.mp4"
    assert _default_output_path(config, 7, preview=True) == config.exports_dir / "timelapse_preview_7.mp4"
    requested = tmp_path / "Movies" / "custom.mp4"
    assert _preview_output_path(config, 7, str(requested)) == requested.with_name("custom.preview.mp4")


def test_replacing_completed_render_removes_old_video_and_cache(tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("p", str(config.inbox_dir), "2026-05-09 10:00:00"),
    )
    old_output = config.exports_dir / "old-timelapse.mp4"
    current_output = _default_output_path(config, project_id)
    old_output.write_bytes(b"old-video")
    current_output.write_bytes(b"current-video")
    preview_output = _default_output_path(config, project_id, preview=True)
    preview_output.write_bytes(b"preview-video")
    old_id = db.execute(
        "INSERT INTO renders (project_id, output_path, started_at, status) VALUES (?, ?, ?, 'done')",
        (project_id, str(old_output), "2026-05-09 10:00:00"),
    )
    current_id = db.execute(
        "INSERT INTO renders (project_id, output_path, config_json, started_at, status) VALUES (?, ?, ?, ?, 'done')",
        (project_id, str(current_output), RenderConfig().model_dump_json(), "2026-05-09 11:00:00"),
    )
    preview_id = db.execute(
        "INSERT INTO renders (project_id, output_path, config_json, started_at, status) VALUES (?, ?, ?, ?, 'done')",
        (project_id, str(preview_output), RenderConfig(preview=True).model_dump_json(), "2026-05-09 10:30:00"),
    )
    old_cache = config.render_cache_dir / f"render_{old_id}"
    old_cache.mkdir(parents=True)
    (old_cache / "playback.mp4").write_bytes(b"old-playback")

    _replace_previous_render_outputs(db, config, project_id, current_id, current_output, preview=False)

    old_row = db.fetchone("SELECT status, output_path FROM renders WHERE id = ?", (old_id,))
    preview_row = db.fetchone("SELECT status, output_path FROM renders WHERE id = ?", (preview_id,))
    assert not old_output.exists()
    assert current_output.read_bytes() == b"current-video"
    assert preview_output.read_bytes() == b"preview-video"
    assert not old_cache.exists()
    assert old_row["status"] == "replaced"
    assert old_row["output_path"] is None
    assert preview_row["status"] == "done"
    assert preview_row["output_path"] == str(preview_output)


def test_filter_rows_by_date_keeps_full_end_day():
    rows = [
        {"captured_at": "2020-01-01 23:59:59", "hash": "a"},
        {"captured_at": "2020-01-02 12:00:00", "hash": "b"},
        {"captured_at": "2020-01-03 00:00:00", "hash": "c"},
    ]

    filtered = _filter_rows_by_date(rows, "2020-01-01", "2020-01-02")

    assert [row["hash"] for row in filtered] == ["a", "b"]


def test_filter_rows_by_date_rejects_backwards_range():
    with pytest.raises(RuntimeError, match="Start date"):
        _filter_rows_by_date([], "2020-01-03", "2020-01-02")


def test_latest_row_per_day_keeps_latest_capture_for_each_date():
    rows = [
        {"captured_at": "2020-01-01 08:00:00", "hash": "old"},
        {"captured_at": "2020-01-01 18:00:00", "hash": "new"},
        {"captured_at": "2020-01-02 09:00:00", "hash": "next"},
    ]

    filtered = _latest_row_per_day(rows)

    assert [row["hash"] for row in filtered] == ["new", "next"]


def test_run_ffmpeg_does_not_pipe_child_output(tmp_path, monkeypatch):
    calls = {}

    class FakeProcess:
        returncode = 0

        def poll(self):
            return 0

    def fake_popen(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("selfietl.pipeline.compose.subprocess.Popen", fake_popen)

    _run_ffmpeg(tmp_path, tmp_path / "out.mp4", RenderConfig(), frame_count=1)

    assert calls["kwargs"]["stdout"] is calls["kwargs"]["stderr"]


def test_run_ffmpeg_never_adds_fade_filters(tmp_path, monkeypatch):
    calls = {}

    class FakeProcess:
        returncode = 0

        def poll(self):
            return 0

    def fake_popen(command, **kwargs):
        calls["command"] = command
        return FakeProcess()

    monkeypatch.setattr("selfietl.pipeline.compose.subprocess.Popen", fake_popen)

    _run_ffmpeg(
        tmp_path,
        tmp_path / "out.mp4",
        RenderConfig(fade_in_seconds=5, fade_out_seconds=5),
        frame_count=120,
    )

    assert "fade=t=" not in " ".join(calls["command"])
