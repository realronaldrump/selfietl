import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest
import piexif
from fastapi.testclient import TestClient
from PIL import Image

from selfietl.api.capture import _parse_capture_datetime as parse_upload_datetime
from selfietl.api.photos import _parse_capture_datetime as parse_edit_datetime
from selfietl.config import RenderConfig, load_config
from selfietl.db import Database
from selfietl.jobs.runner import runner
from selfietl.pipeline.compose import _filter_rows_by_date
from selfietl.pipeline.detect import DetectionResult
from selfietl.pipeline.images import exif_metadata, parse_filename_datetime
from selfietl.pipeline.ingest import create_project, scan_project
from selfietl.pipeline.single import _mark_other_active_captures_for_day, import_to_inbox, process_single_photo
from selfietl.server import create_app


@pytest.fixture(params=["America/Chicago", "UTC", "Asia/Tokyo"])
def server_timezone(request, monkeypatch):
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize("parse", [parse_upload_datetime, parse_edit_datetime])
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-03T23:59:00-06:00", "2026-10-03 23:59:00"),
        ("2026-01-31T23:59:59-07:00", "2026-01-31 23:59:59"),
        ("2026-11-01T01:30:00-06:00", "2026-11-01 01:30:00"),
        ("2026-11-01T01:30:00-07:00", "2026-11-01 01:30:00"),
        ("2026-03-08T02:30:00-07:00", "2026-03-08 02:30:00"),
        ("2026-10-03T00:01:02+05:30", "2026-10-03 00:01:02"),
        ("2026-10-03T23:59:00Z", "2026-10-03 23:59:00"),
        ("2026-10-03T23:59:00.123456-06:00", "2026-10-03 23:59:00.123456"),
        ("2026-10-03 23:59:00", "2026-10-03 23:59:00"),
    ],
)
def test_capture_dates_preserve_source_wall_time(server_timezone, parse, value, expected):
    assert parse(value) == datetime.fromisoformat(expected)


@pytest.fixture
def capture_client(tmp_path, monkeypatch):
    config = load_config(tmp_path / "home")
    app = create_app(config)
    runner.jobs.clear()
    runner.resume_new_jobs()

    def no_face(*args, **kwargs):
        return DetectionResult(
            landmarks=None, bbox=None, confidence=0,
            yaw=None, pitch=None, roll=None,
            eye_open_ratio=None, mouth_open_ratio=None,
            warnings=["no_face_detected"], method="test",
        )

    monkeypatch.setattr("selfietl.pipeline.single.detect_landmarks", no_face)
    source = tmp_path / "photo.jpg"
    Image.new("RGB", (32, 32), (100, 120, 140)).save(source, "JPEG")
    with TestClient(app) as client:
        yield client, app.state.db, config, source.read_bytes()


def completed_capture(client, response):
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            break
        time.sleep(0.01)
    assert job["status"] == "done", job
    return job["result"]


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_midnight_import_matches_storage_calendar_preview_and_render(server_timezone, capture_client, batch):
    client, db, config, contents = capture_client
    timestamp = "2026-10-03T23:59:00-06:00"
    if batch:
        response = client.post(
            "/api/capture/batch",
            files=[("files", ("photo.jpg", contents, "image/jpeg"))],
            data={"metadata": json.dumps([{"captured_at": timestamp}])},
        )
    else:
        response = client.post(
            "/api/capture", params={"captured_at": timestamp},
            files={"file": ("photo.jpg", contents, "image/jpeg")},
        )
    result = completed_capture(client, response)
    photo = result["photos"][0] if batch else result
    assert photo["captured_at"] == "2026-10-03 23:59:00"
    row = db.fetchone("SELECT * FROM photos WHERE hash = ?", (photo["hash"],))
    assert row["captured_at"] == datetime(2026, 10, 3, 23, 59)
    assert Path(row["path"]).name == "selfie_2026-10-03_235900.jpg"
    assert client.get("/api/photos/by-date/2026-10-03").json()["photos"][0]["hash"] == photo["hash"]
    assert client.get("/api/photos/by-date/2026-10-04").json()["photos"] == []
    days = client.get("/api/calendar", params={"start": "2026-10-03", "end": "2026-10-04"}).json()["days"]
    assert [(day["date"], day["count"]) for day in days] == [("2026-10-03", 1)]
    assert _filter_rows_by_date([row], "2026-10-03", "2026-10-03") == [row]

    preview = client.post(
        "/api/capture/preview",
        files=[("files", (Path(row["path"]).name, contents, "image/jpeg"))],
    ).json()["items"][0]
    assert preview["captured_at"] == "2026-10-03 23:59:00"
    assert preview["captured_at_source"] == "filename"


def test_date_edit_preserves_offset_clock_and_moves_calendar_day(server_timezone, capture_client):
    client, db, config, contents = capture_client
    result = completed_capture(client, client.post(
        "/api/capture", params={"captured_at": "2026-10-04T00:59:00"},
        files={"file": ("photo.jpg", contents, "image/jpeg")},
    ))
    composite = config.hair_composites_dir / f"{result['hash']}.jpg"
    composite.write_bytes(b"cached image labeled October 4")
    other_composite = config.hair_composites_dir / "other.jpg"
    other_composite.write_bytes(b"other image")
    response = client.patch(
        f"/api/photos/{result['hash']}",
        json={"captured_at": "2026-10-03T23:59:00-06:00"},
    )
    assert response.status_code == 200
    assert response.json()["captured_at"] == "2026-10-03T23:59:00"
    assert not composite.exists()
    assert other_composite.exists()
    assert client.get("/api/photos/by-date/2026-10-04").json()["photos"] == []
    assert client.get("/api/photos/by-date/2026-10-03").json()["photos"][0]["captured_at"] == "2026-10-03 23:59:00"

    # Reprocessing the original inbox file must not undo a catalog date edit.
    row = db.fetchone("SELECT * FROM photos WHERE hash = ?", (result["hash"],))
    project_id = db.fetchone("SELECT project_id FROM project_photos WHERE photo_hash = ?", (result["hash"],))["project_id"]
    retried = process_single_photo(db, config, project_id, Path(row["path"]))
    assert retried["captured_at"] == "2026-10-03 23:59:00"
    assert db.fetchone("SELECT captured_at FROM photos WHERE hash = ?", (result["hash"],))["captured_at"] == row["captured_at"]


def test_late_night_capture_only_replaces_its_original_day(server_timezone, tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    project_id = create_project(db, "Test", str(config.inbox_dir))
    for photo_hash, timestamp in [("earlier", "2026-10-03 12:00:00"), ("new", "2026-10-03 23:59:00"), ("next-day", "2026-10-04 00:30:00")]:
        db.execute("INSERT INTO photos (hash,path,captured_at) VALUES (?,?,?)", (photo_hash, f"/tmp/{photo_hash}.jpg", timestamp))
        db.execute("INSERT INTO project_photos (project_id,photo_hash) VALUES (?,?)", (project_id, photo_hash))
    with db.connect() as conn:
        replaced = _mark_other_active_captures_for_day(
            conn, project_id=project_id, keep_hash="new",
            captured_at=parse_upload_datetime("2026-10-03T23:59:00-06:00"),
        )
    assert replaced == 1
    assert db.fetchone("SELECT skipped FROM photos WHERE hash = 'earlier'")["skipped"] == 1
    assert db.fetchone("SELECT skipped FROM photos WHERE hash = 'next-day'")["skipped"] == 0


def test_direct_pipeline_override_uses_wall_time(server_timezone, capture_client):
    client, db, config, contents = capture_client
    project_id = create_project(db, "Test", str(config.inbox_dir))
    captured_at = datetime.fromisoformat("2026-10-03T23:59:00-06:00")
    path = import_to_inbox(config, contents=contents, filename="photo.jpg", captured_at=captured_at)
    result = process_single_photo(db, config, project_id, path, captured_at=captured_at)
    assert result["captured_at"] == "2026-10-03 23:59:00"
    row = db.fetchone("SELECT captured_at FROM photos WHERE hash = ?", (result["hash"],))
    assert row["captured_at"] == datetime(2026, 10, 3, 23, 59)
    assert client.get("/api/photos/by-date/2026-10-03").json()["photos"][0]["hash"] == result["hash"]


@pytest.mark.parametrize(
    "filename",
    [
        "selfie_2026-10-03_235900.jpeg",
        "selfie_2026-10-03_235900_2.jpeg",
        "2026-10-03_23-59-00.jpg",
        "20261003_235900.jpg",
        "imported-selfie-2026-10-03T23:59:00-06:00.heic",
    ],
)
def test_date_stamped_filenames_preserve_capture_day(filename):
    assert parse_filename_datetime(filename) == datetime(2026, 10, 3, 23, 59)


def test_inbox_rescan_preserves_capture_time_without_exif(tmp_path):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    source = tmp_path / "photo.jpg"
    Image.new("RGB", (32, 32), (100, 120, 140)).save(source, "JPEG")
    path = import_to_inbox(config, contents=source.read_bytes(), filename=source.name, captured_at=datetime(2026, 10, 3, 23, 59))
    assert exif_metadata(path)["captured_at"] == datetime(2026, 10, 3, 23, 59)
    project_id = create_project(db, "Test", str(config.inbox_dir))
    scan_project(db, config, project_id)
    assert db.fetchone("SELECT captured_at FROM photos")["captured_at"] == datetime(2026, 10, 3, 23, 59)


def test_render_bounds_use_capture_wall_time():
    rows = [{"captured_at": "2026-10-03 23:59:00", "hash": "photo"}]
    assert _filter_rows_by_date(rows, "2026-10-03T23:58:00-06:00", "2026-10-03T23:59:59-06:00") == rows
    # Ordering also follows the selected local dates, even with mixed offsets.
    RenderConfig(start_date="2026-10-03T23:00:00-07:00", end_date="2026-10-04T00:00:00+05:30")


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_invalid_capture_offset_is_rejected_before_saving(capture_client, batch):
    client, db, config, contents = capture_client
    timestamp = "2026-10-03T23:59:00+25:00"
    if batch:
        response = client.post(
            "/api/capture/batch",
            files=[("files", ("photo.jpg", contents, "image/jpeg"))],
            data={"metadata": json.dumps([{"captured_at": timestamp}])},
        )
    else:
        response = client.post(
            "/api/capture", params={"captured_at": timestamp},
            files={"file": ("photo.jpg", contents, "image/jpeg")},
        )
    assert response.status_code == 400
    assert db.fetchone("SELECT COUNT(*) AS count FROM photos")["count"] == 0
    assert list(config.inbox_dir.iterdir()) == []


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_import_without_a_photo_timestamp_is_rejected(capture_client, batch):
    client, db, config, contents = capture_client
    if batch:
        response = client.post("/api/capture/batch", files=[("files", ("photo.jpg", contents, "image/jpeg"))])
    else:
        response = client.post("/api/capture", files={"file": ("photo.jpg", contents, "image/jpeg")})
    assert response.status_code == 400
    assert "original" in response.json()["detail"].lower()
    assert db.fetchone("SELECT COUNT(*) AS count FROM photos")["count"] == 0
    assert list(config.inbox_dir.iterdir()) == []


def test_preview_never_assigns_upload_time_to_a_photo(capture_client):
    client, db, config, contents = capture_client
    response = client.post("/api/capture/preview", files=[("files", ("photo.jpg", contents, "image/jpeg"))])
    assert response.status_code == 200
    photo = response.json()["items"][0]
    assert photo["supported"] is True
    assert photo["captured_at"] is None
    assert photo["captured_at_source"] is None
    assert "missing_capture_timestamp" in photo["warnings"]


def test_missing_timestamp_in_batch_cleans_up_preceding_upload(capture_client):
    client, db, config, contents = capture_client
    response = client.post(
        "/api/capture/batch",
        files=[("files", ("dated.jpg", contents, "image/jpeg")), ("files", ("undated.jpg", contents, "image/jpeg"))],
        data={"metadata": json.dumps([{"captured_at": "2026-10-03T23:59:00-06:00"}, None])},
    )
    assert response.status_code == 400
    assert "undated.jpg" in response.json()["detail"]
    assert list(config.inbox_dir.iterdir()) == []
    assert db.fetchone("SELECT COUNT(*) AS count FROM photos")["count"] == 0


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_import_uses_original_exif_instead_of_transfer_date(server_timezone, capture_client, tmp_path, batch):
    client, db, config, _ = capture_client
    source = tmp_path / "original.jpg"
    Image.new("RGB", (32, 32), (100, 120, 140)).save(source, "JPEG", exif=piexif.dump({
        "0th": {piexif.ImageIFD.DateTime: "2026:10:04 01:00:00"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: "2026:10:03 23:59:00"},
    }))
    contents = source.read_bytes()
    if batch:
        response = client.post("/api/capture/batch", files=[("files", ("original.jpg", contents, "image/jpeg"))])
    else:
        response = client.post("/api/capture", files={"file": ("original.jpg", contents, "image/jpeg")})
    result = completed_capture(client, response)
    photo = result["photos"][0] if batch else result
    assert photo["captured_at"] == "2026-10-03 23:59:00"
    assert db.fetchone("SELECT captured_at FROM photos")["captured_at"] == datetime(2026, 10, 3, 23, 59)


@pytest.mark.parametrize("modification_exif", [False, True], ids=["no-exif", "modification-exif"])
def test_metadata_never_infers_original_date_from_modification_time(tmp_path, modification_exif):
    source = tmp_path / "undated.jpg"
    exif = {"0th": {piexif.ImageIFD.DateTime: "2026:10:04 01:00:00"}} if modification_exif else {}
    Image.new("RGB", (32, 32), (100, 120, 140)).save(source, "JPEG", exif=piexif.dump(exif))
    os.utime(source, (1_790_000_000, 1_790_000_000))
    metadata = exif_metadata(source)
    assert metadata["captured_at"] is None
    assert metadata["captured_at_source"] is None
    assert "missing_capture_timestamp" in metadata["warnings"]


def test_folder_scan_skips_undated_photos_without_inventing_a_date(tmp_path):
    config = load_config(tmp_path / "home")
    source = tmp_path / "source"
    source.mkdir()
    path = source / "undated.jpg"
    Image.new("RGB", (32, 32), (100, 120, 140)).save(path, "JPEG")
    db = Database(config.db_path)
    project_id = create_project(db, "Test", str(source))
    result = scan_project(db, config, project_id)
    assert result["inserted"] == 0
    assert {"path": str(path), "warning": "missing_capture_timestamp"} in result["warnings"]
    assert db.fetchone("SELECT COUNT(*) AS count FROM photos")["count"] == 0
    assert path.exists()


def test_inbox_write_requires_a_known_capture_timestamp(capture_client):
    _, _, config, contents = capture_client
    with pytest.raises(ValueError, match="original capture timestamp"):
        import_to_inbox(config, contents=contents, filename="photo.jpg")
    assert list(config.inbox_dir.iterdir()) == []


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_photos_library_timestamp_takes_precedence_over_embedded_date(server_timezone, capture_client, tmp_path, batch):
    client, db, config, _ = capture_client
    source = tmp_path / "original.jpg"
    Image.new("RGB", (32, 32), (100, 120, 140)).save(source, "JPEG", exif=piexif.dump({
        "Exif": {piexif.ExifIFD.DateTimeOriginal: "2026:10:04 12:00:00"},
    }))
    timestamp = "2026-10-03T23:59:00-06:00"
    if batch:
        response = client.post(
            "/api/capture/batch",
            files=[("files", ("original.jpg", source.read_bytes(), "image/jpeg"))],
            data={"metadata": json.dumps([{"captured_at": timestamp}])},
        )
    else:
        response = client.post(
            "/api/capture", params={"captured_at": timestamp},
            files={"file": ("original.jpg", source.read_bytes(), "image/jpeg")},
        )
    result = completed_capture(client, response)
    photo = result["photos"][0] if batch else result
    assert photo["captured_at"] == "2026-10-03 23:59:00"
    assert db.fetchone("SELECT captured_at FROM photos")["captured_at"] == datetime(2026, 10, 3, 23, 59)
