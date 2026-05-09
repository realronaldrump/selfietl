import json
from datetime import datetime
from pathlib import Path

import piexif
from PIL import Image
from fastapi.testclient import TestClient

from selfietl.config import load_config
from selfietl.pipeline.detect import DetectionResult
from selfietl.pipeline.single import (
    _mark_other_active_captures_for_day,
    discard_photo,
    import_to_inbox,
)
from selfietl.server import create_app


def _make_jpeg(tmp_path: Path) -> bytes:
    image = Image.new("RGB", (320, 320), (220, 200, 180))
    target = tmp_path / "src.jpg"
    image.save(target, "JPEG", quality=85)
    return target.read_bytes()


def _make_exif_jpeg(tmp_path: Path, when: str = "2026:05:03 08:09:10") -> bytes:
    image = Image.new("RGB", (320, 320), (220, 200, 180))
    target = tmp_path / "exif.jpg"
    exif = {
        "0th": {
            piexif.ImageIFD.Make: "SelfieTLTest",
            piexif.ImageIFD.Model: "BackfillCam",
        },
        "Exif": {piexif.ExifIFD.DateTimeOriginal: when},
    }
    image.save(target, "JPEG", quality=85, exif=piexif.dump(exif))
    return target.read_bytes()


def test_import_to_inbox_writes_dated_filename(tmp_path: Path):
    config = load_config(tmp_path / "home")
    contents = _make_jpeg(tmp_path)
    when = datetime(2026, 5, 8, 14, 30, 12)

    saved = import_to_inbox(config, contents=contents, filename="ios.heic", captured_at=when)

    assert saved.exists()
    assert saved.parent == config.inbox_dir
    assert saved.name == "selfie_2026-05-08_143012.heic"
    assert saved.read_bytes() == contents


def test_import_to_inbox_handles_collisions(tmp_path: Path):
    config = load_config(tmp_path / "home")
    contents = _make_jpeg(tmp_path)
    when = datetime(2026, 5, 8, 14, 30, 12)

    first = import_to_inbox(config, contents=contents, filename="a.jpg", captured_at=when)
    second = import_to_inbox(config, contents=contents, filename="a.jpg", captured_at=when)

    assert first != second
    assert first.exists() and second.exists()
    assert second.name.startswith("selfie_2026-05-08_143012_")


def test_import_to_inbox_uses_safe_extension_for_unknown(tmp_path: Path):
    config = load_config(tmp_path / "home")
    saved = import_to_inbox(
        config,
        contents=_make_jpeg(tmp_path),
        filename="weird.bin",
        captured_at=datetime(2026, 5, 8, 9, 0, 0),
    )
    assert saved.suffix == ".jpg"


def test_capture_preview_reads_photo_metadata(tmp_path: Path):
    config = load_config(tmp_path / "home")
    app = create_app(config)
    contents = _make_exif_jpeg(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/capture/preview",
            files=[("files", ("old-selfie.jpg", contents, "image/jpeg"))],
        )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["filename"] == "old-selfie.jpg"
    assert item["captured_at"] == "2026-05-03 08:09:10"
    assert item["captured_at_source"] == "exif_datetime_original"
    assert item["camera_make"] == "SelfieTLTest"
    assert item["camera_model"] == "BackfillCam"
    assert item["width"] == 320
    assert item["height"] == 320


def test_process_single_photo_uses_manual_captured_at_override(tmp_path: Path, monkeypatch):
    config = load_config(tmp_path / "home")
    from selfietl.db import Database
    from selfietl.pipeline import single as single_pipeline

    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("p", str(config.inbox_dir), "2026-05-08 09:00:00"),
    )
    source = config.inbox_dir / "manual-override.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(_make_exif_jpeg(tmp_path, when="2026:05:08 14:30:00"))

    def fake_detect(*args, **kwargs):
        return DetectionResult(
            landmarks=None,
            bbox=None,
            confidence=0,
            yaw=None,
            pitch=None,
            roll=None,
            eye_open_ratio=None,
            mouth_open_ratio=None,
            warnings=["no_face_detected"],
            method="test",
        )

    monkeypatch.setattr(single_pipeline, "detect_landmarks", fake_detect)

    override = datetime(2026, 5, 3, 8, 9, 10)
    result = single_pipeline.process_single_photo(db, config, project_id, source, captured_at=override)

    row = db.fetchone("SELECT captured_at, warnings_json FROM photos WHERE hash = ?", (result["hash"],))
    assert str(row["captured_at"]) == "2026-05-03 08:09:10"
    assert "captured_at_user_override" in json.loads(row["warnings_json"])


def test_discard_photo_removes_files_and_row(tmp_path: Path):
    config = load_config(tmp_path / "home")
    from selfietl.db import Database

    db = Database(config.db_path)
    photo_hash = "abc123"
    photo_path = config.inbox_dir / "selfie.jpg"
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    photo_path.write_bytes(b"fake")
    thumb = config.thumbs_dir / f"{photo_hash}.jpg"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"fake-thumb")
    db.execute(
        "INSERT INTO photos (hash, path, captured_at) VALUES (?, ?, ?)",
        (photo_hash, str(photo_path), "2026-05-08 12:00:00"),
    )

    deleted = discard_photo(db, config, photo_hash)
    assert deleted is True
    assert not photo_path.exists()
    assert not thumb.exists()
    assert db.fetchone("SELECT * FROM photos WHERE hash = ?", (photo_hash,)) is None


def test_discard_photo_preserves_original_outside_inbox(tmp_path: Path):
    config = load_config(tmp_path / "home")
    from selfietl.db import Database

    db = Database(config.db_path)
    photo_hash = "external123"
    photo_path = tmp_path / "camera-roll" / "selfie.jpg"
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    photo_path.write_bytes(b"original")
    thumb = config.thumbs_dir / f"{photo_hash}.jpg"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"thumb")
    db.execute(
        "INSERT INTO photos (hash, path, captured_at) VALUES (?, ?, ?)",
        (photo_hash, str(photo_path), "2026-05-08 12:00:00"),
    )

    deleted = discard_photo(db, config, photo_hash)

    assert deleted is True
    assert photo_path.exists()
    assert not thumb.exists()
    assert db.fetchone("SELECT * FROM photos WHERE hash = ?", (photo_hash,)) is None


def test_new_active_capture_replaces_older_active_capture_for_same_day(tmp_path: Path):
    config = load_config(tmp_path / "home")
    from selfietl.db import Database

    db = Database(config.db_path)
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at) VALUES (?, ?, ?)",
        ("p", str(config.inbox_dir), "2026-05-08 09:00:00"),
    )
    for photo_hash, captured_at in [
        ("old", "2026-05-08 08:00:00"),
        ("keep", "2026-05-08 18:00:00"),
        ("other-day", "2026-05-07 08:00:00"),
    ]:
        db.execute(
            "INSERT INTO photos (hash, path, captured_at, skipped) VALUES (?, ?, ?, 0)",
            (photo_hash, str(config.inbox_dir / f"{photo_hash}.jpg"), captured_at),
        )
        db.execute(
            "INSERT INTO project_photos (project_id, photo_hash, added_at) VALUES (?, ?, ?)",
            (project_id, photo_hash, "2026-05-08 18:00:00"),
        )

    with db.connect() as conn:
        replaced = _mark_other_active_captures_for_day(
            conn,
            project_id=project_id,
            keep_hash="keep",
            captured_at=datetime(2026, 5, 8, 18, 0, 0),
        )

    assert replaced == 1
    assert db.fetchone("SELECT skipped, skip_reason FROM photos WHERE hash = ?", ("old",))["skipped"] == 1
    assert db.fetchone("SELECT skipped FROM photos WHERE hash = ?", ("keep",))["skipped"] == 0
    assert db.fetchone("SELECT skipped FROM photos WHERE hash = ?", ("other-day",))["skipped"] == 0


def test_discard_photo_returns_false_when_missing(tmp_path: Path):
    config = load_config(tmp_path / "home")
    from selfietl.db import Database

    db = Database(config.db_path)
    assert discard_photo(db, config, "missing-hash") is False
