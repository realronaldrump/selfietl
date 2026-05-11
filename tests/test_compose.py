import pytest

from selfietl.config import RenderConfig
from selfietl.pipeline.compose import _filter_rows_by_date, _latest_row_per_day, _run_ffmpeg


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
