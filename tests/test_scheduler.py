from datetime import datetime, timedelta

from selfietl.config import load_config
from selfietl.db import Database
from selfietl.scheduler import (
    AutoRenderSettings,
    DEFAULT_RENDER_TIME,
    auto_render_has_pending_changes,
    default_settings,
    load_settings,
    next_run_at,
    primary_project_id,
    render_config_from_settings,
    render_input_signature,
    save_settings,
    streak_summary,
)


def test_next_run_at_uses_today_when_time_has_not_passed():
    now = datetime(2026, 5, 8, 1, 0, 0)
    target = next_run_at(now, "03:00", last_run_date=None)
    assert target == datetime(2026, 5, 8, 3, 0, 0)


def test_next_run_at_catches_up_after_target_time_when_not_run_today():
    now = datetime(2026, 5, 8, 5, 30, 0)
    target = next_run_at(now, "03:00", last_run_date=None)
    assert target == now


def test_next_run_at_waits_before_retrying_recent_failed_attempt():
    now = datetime(2026, 5, 8, 5, 30, 0)
    target = next_run_at(
        now,
        "03:00",
        last_run_date=None,
        last_attempt_at="2026-05-08 05:00:00",
    )
    assert target == datetime(2026, 5, 8, 6, 0, 0)


def test_next_run_at_skips_to_tomorrow_after_run_today():
    now = datetime(2026, 5, 8, 1, 0, 0)
    target = next_run_at(now, "03:00", last_run_date="2026-05-08")
    assert target == datetime(2026, 5, 9, 3, 0, 0)


def test_next_run_at_skips_to_tomorrow_after_check_today():
    now = datetime(2026, 5, 8, 5, 30, 0)
    target = next_run_at(now, "03:00", last_run_date=None, last_checked_date="2026-05-08")
    assert target == datetime(2026, 5, 9, 3, 0, 0)


def test_settings_round_trip(tmp_path):
    config = load_config(tmp_path / "home")
    settings = default_settings()
    settings.enabled = False
    settings.time = "04:30"
    settings.render_config["resolution"] = "1080_square"
    settings.last_checked_date = "2026-05-08"
    settings.last_render_signature = "abc123"
    save_settings(config, settings)

    reloaded = load_settings(config)
    assert reloaded.enabled is False
    assert reloaded.time == "04:30"
    assert reloaded.render_config["resolution"] == "1080_square"
    assert reloaded.last_checked_date == "2026-05-08"
    assert reloaded.last_render_signature == "abc123"
    # Default keys still present after partial overwrite.
    assert "morph_mode" in reloaded.render_config


def test_settings_default_when_file_missing(tmp_path):
    config = load_config(tmp_path / "home")
    settings = load_settings(config)
    assert settings.enabled is True
    assert settings.time == DEFAULT_RENDER_TIME


def test_primary_project_id_prefers_inbox(tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    inbox = (config.data_dir / "inbox").resolve()
    inbox.mkdir(parents=True, exist_ok=True)
    db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("Other", "/tmp/other", datetime.now().isoformat(sep=" ")),
    )
    db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("Inbox", str(inbox), datetime.now().isoformat(sep=" ")),
    )
    assert primary_project_id(db, config) is not None
    project_id = primary_project_id(db, config)
    row = db.fetchone("SELECT name FROM projects WHERE id = ?", (project_id,))
    assert row["name"] == "Inbox"


def test_auto_render_signature_tracks_photos_and_settings(tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = _insert_project(db, config)
    settings = default_settings()
    render_config = render_config_from_settings(settings)

    empty_signature = render_input_signature(db, config, project_id, render_config)
    _insert_active_photo(db, project_id, "hash1", "2026-05-07 10:00:00")
    one_photo_signature = render_input_signature(db, config, project_id, render_config)
    assert one_photo_signature != empty_signature

    settings.last_render_signature = one_photo_signature
    assert auto_render_has_pending_changes(db, config, project_id, settings) is False

    settings.render_config["fps"] = 60
    assert auto_render_has_pending_changes(db, config, project_id, settings) is True

    settings.render_config["fps"] = 30
    _insert_active_photo(db, project_id, "hash2", "2026-05-08 10:00:00")
    assert auto_render_has_pending_changes(db, config, project_id, settings) is True


def test_streak_summary_counts_consecutive_days(tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = _insert_project(db, config)
    today = datetime(2026, 5, 8).date()
    days = [today, today - timedelta(days=1), today - timedelta(days=2), today - timedelta(days=5)]
    for index, day in enumerate(days):
        photo_hash = f"hash{index}"
        db.execute(
            """
            INSERT INTO photos (hash, path, captured_at, skipped, landmarks_path)
            VALUES (?, ?, ?, 0, ?)
            """,
            (photo_hash, f"/tmp/{photo_hash}.jpg", f"{day.isoformat()} 10:00:00", "/tmp/landmarks"),
        )
        db.execute(
            "INSERT INTO project_photos (project_id, photo_hash, added_at) VALUES (?, ?, ?)",
            (project_id, photo_hash, datetime.now().isoformat(sep=" ")),
        )

    summary = streak_summary(db, project_id, today=today)
    assert summary["has_today"] is True
    assert summary["streak"] == 3
    assert summary["total_days"] == 4
    assert summary["longest_streak"] == 3


def test_streak_counts_yesterday_when_today_missing():
    settings = AutoRenderSettings(
        enabled=True,
        time="03:00",
        last_run_date=None,
        last_render_id=None,
        last_attempt_at=None,
        last_error=None,
        render_config={},
    )
    assert settings.enabled is True


def _insert_project(db: Database, config) -> int:
    return db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("p", str(config.inbox_dir), datetime.now().isoformat(sep=" ")),
    )


def _insert_active_photo(
    db: Database,
    project_id: int,
    photo_hash: str,
    captured_at: str,
    *,
    quality_score: float = 0.8,
) -> None:
    db.execute(
        """
        INSERT INTO photos (
            hash, path, captured_at, width, height, detected_at, landmarks_path, quality_score, skipped
        )
        VALUES (?, ?, ?, 100, 120, ?, ?, ?, 0)
        """,
        (
            photo_hash,
            f"/tmp/{photo_hash}.jpg",
            captured_at,
            "2026-05-08 11:00:00",
            f"/tmp/{photo_hash}.npz",
            quality_score,
        ),
    )
    db.execute(
        "INSERT INTO project_photos (project_id, photo_hash, added_at) VALUES (?, ?, ?)",
        (project_id, photo_hash, datetime.now().isoformat(sep=" ")),
    )
