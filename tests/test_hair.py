from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from selfietl.config import load_config
from selfietl.db import Database
from selfietl.pipeline.face_shape import FACE_OVAL
from selfietl.pipeline.hair import (
    ALGORITHM_VERSION,
    _canvas_assets,
    _signed_distance,
    alignment_signature,
    create_hair_export,
    hair_playback_path,
    hair_metrics,
    mask_iou,
    project_hair_revision,
    source_signature,
    refine_confidence_mask,
    render_hair_export,
    update_haircut_suggestions,
)
from selfietl.server import create_app


def _landmarks() -> np.ndarray:
    points = np.full((478, 3), 0.5, dtype=np.float32)
    points[33, :2] = points[133, :2] = (0.4, 0.42)
    points[263, :2] = points[362, :2] = (0.6, 0.42)
    angles = np.linspace(-np.pi / 2, 3 * np.pi / 2, len(FACE_OVAL), endpoint=False)
    for index, angle in zip(FACE_OVAL, angles):
        points[index, :2] = (0.5 + np.cos(angle) * 0.24, 0.52 + np.sin(angle) * 0.32)
    return points


def _project(tmp_path: Path, masks: list[np.ndarray]):
    config = load_config(tmp_path / "home")
    db = Database(config.db_path)
    canonical = config.data_dir / "canonical.npz"
    np.savez_compressed(canonical, landmarks=_landmarks(), target_size=np.array([100, 120], dtype=np.int32))
    project_id = db.execute(
        "INSERT INTO projects (name, source_folder, created_at, canonical_landmarks_path) VALUES ('hair', ?, '2024-01-01', ?)",
        (str(config.inbox_dir), str(canonical)),
    )
    start = date(2024, 1, 1)
    for index, mask in enumerate(masks):
        photo_hash = f"hair-{index}"
        source = config.inbox_dir / f"{photo_hash}.jpg"
        Image.new("RGB", (100, 120), "white").save(source)
        landmark_path = config.landmarks_dir / f"{photo_hash}.npz"
        np.savez_compressed(landmark_path, landmarks=_landmarks())
        aligned_landmarks = _landmarks().copy()
        aligned_landmarks[:, 0] *= 100
        aligned_landmarks[:, 1] *= 120
        aligned_landmarks_path = config.aligned_landmarks_dir / f"{photo_hash}.npz"
        np.savez_compressed(
            aligned_landmarks_path,
            landmarks=aligned_landmarks,
            matrix=np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32),
            target_size=np.array([100, 120], dtype=np.int32),
        )
        mask_path = config.hair_aligned_masks_dir / f"{photo_hash}.png"
        Image.fromarray(mask.astype(np.uint8) * 255).save(mask_path)
        source_mask_path = config.hair_source_masks_dir / f"{photo_hash}.npz"
        np.savez_compressed(source_mask_path, confidence=mask.astype(np.float16))
        captured = start + timedelta(days=index * 3)
        db.execute(
            "INSERT INTO photos (hash, path, captured_at, width, height, landmarks_path, skipped) VALUES (?, ?, ?, 100, 120, ?, 0)",
            (photo_hash, str(source), f"{captured.isoformat()} 10:00:00", str(landmark_path)),
        )
        db.execute("INSERT INTO project_photos (project_id, photo_hash) VALUES (?, ?)", (project_id, photo_hash))
        metrics = hair_metrics(mask, aligned_landmarks)
        signature = alignment_signature(aligned_landmarks_path)
        db.execute(
            """
            INSERT INTO hair_measurements (
                photo_hash, algorithm_version, source_signature, alignment_signature,
                source_mask_path, aligned_mask_path, metrics_json, quality_score,
                eligible, reasons_json, computed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, .9, 1, '[]', '2024-01-01', '2024-01-01')
            """,
            (photo_hash, ALGORITHM_VERSION, source_signature(source, landmark_path), signature, str(source_mask_path), str(mask_path), json.dumps(metrics)),
        )
    return config, db, project_id


def test_refine_confidence_mask_keeps_connected_wisps_and_removes_noise():
    confidence = np.zeros((64, 64), dtype=np.float32)
    confidence[12:36, 18:46] = 0.8
    confidence[8:13, 30:33] = 0.35
    confidence[55, 55] = 0.9

    mask, quality, reasons = refine_confidence_mask(confidence)

    assert mask[9, 31]
    assert not mask[55, 55]
    assert quality > 0.7
    assert "implausible_hair_area" not in reasons


def test_mask_metrics_and_iou_are_similarity_friendly():
    mask = np.zeros((120, 100), dtype=bool)
    mask[12:55, 24:76] = True
    landmarks = _landmarks()
    landmarks[:, 0] *= 100
    landmarks[:, 1] *= 120

    metrics = hair_metrics(mask, landmarks)

    assert metrics["area"] > 0
    assert metrics["top_extent"] > 0
    assert mask_iou(mask, mask) == 1


def test_signed_distance_interpolation_preserves_exact_endpoints():
    first = np.zeros((40, 40), dtype=bool)
    first[5:30, 5:20] = True
    second = np.zeros((40, 40), dtype=bool)
    second[8:26, 12:34] = True
    first_sdf = _signed_distance(first)
    second_sdf = _signed_distance(second)

    assert np.array_equal(first_sdf >= 0, first)
    assert np.array_equal(second_sdf >= 0, second)


def test_canvas_uses_one_canonical_face_outline_for_every_day(tmp_path):
    mask = np.zeros((120, 100), dtype=bool)
    mask[8:48, 22:78] = True
    config, db, _ = _project(tmp_path, [mask, mask])
    second = config.aligned_landmarks_dir / "hair-1.npz"
    with np.load(second) as payload:
        moved = np.asarray(payload["landmarks"]).copy()
        matrix = payload["matrix"]
        target = payload["target_size"]
    moved[:, 0] += 12
    np.savez_compressed(second, landmarks=moved, matrix=matrix, target_size=target)

    _, first_base = _canvas_assets(db, config, "hair-0", 360, 450)
    _, second_base = _canvas_assets(db, config, "hair-1", 360, 450)

    assert np.array_equal(np.asarray(first_base), np.asarray(second_base))


def test_persistent_shorter_shape_creates_confirmable_haircut(tmp_path):
    long = np.zeros((120, 100), dtype=bool)
    long[5:92, 12:88] = True
    short = np.zeros((120, 100), dtype=bool)
    short[12:58, 24:76] = True
    config, db, project_id = _project(tmp_path, [long, long, short, short, short])

    count = update_haircut_suggestions(db, config, project_id)
    event = db.fetchone("SELECT * FROM haircut_events WHERE project_id = ?", (project_id,))

    assert count == 1
    assert event["status"] == "suggested"
    assert event["first_after_photo_hash"] == "hair-2"
    evidence = json.loads(event["evidence_json"])
    assert evidence["following_days"] == 2
    assert evidence["earliest_date"] == "2024-01-05"
    assert evidence["latest_date"] == "2024-01-07"


def test_hair_api_manifest_exclusion_and_manual_haircuts(tmp_path):
    mask = np.zeros((120, 100), dtype=bool)
    mask[8:58, 20:80] = True
    config, db, project_id = _project(tmp_path, [mask, mask])
    revision_before = project_hair_revision(db, config, project_id)
    app = create_app(config)

    with TestClient(app) as client:
        manifest = client.get(f"/api/projects/{project_id}/hair")
        excluded = client.patch("/api/photos/hair-0/hair", json={"excluded": True})
        haircut = client.post(f"/api/projects/{project_id}/haircuts", json={"event_date": "2024-01-02"})
        confirmed = client.get(f"/api/projects/{project_id}/hair")

    assert manifest.status_code == 200
    assert manifest.json()["coverage"]["included"] == 2
    assert excluded.status_code == 200
    assert haircut.status_code == 200
    assert confirmed.json()["coverage"]["included"] == 1
    assert confirmed.json()["haircuts"][0]["status"] == "confirmed"
    assert project_hair_revision(db, config, project_id) != revision_before


def test_hair_export_writes_browser_compatible_mp4(tmp_path):
    mask_a = np.zeros((120, 100), dtype=bool)
    mask_a[8:65, 18:82] = True
    mask_b = np.zeros((120, 100), dtype=bool)
    mask_b[12:56, 24:76] = True
    config, db, project_id = _project(tmp_path, [mask_a, mask_b])
    payload = {"start_date": None, "end_date": None, "seconds_per_selfie": 0.25, "width": 360, "height": 450}
    previous_id = create_hair_export(db, config, project_id, payload)
    previous_output = config.exports_dir / f"hair-timeline-{project_id}-{previous_id}.mp4"
    previous_output.write_bytes(b"previous-hair-video")
    db.execute(
        "UPDATE hair_exports SET status = 'done', output_path = ? WHERE id = ?",
        (str(previous_output), previous_id),
    )
    previous_playback = config.hair_playback_dir / f"hair-export-{previous_id}.mp4"
    previous_playback.parent.mkdir(parents=True, exist_ok=True)
    previous_playback.write_bytes(b"previous-playback")
    export_id = create_hair_export(db, config, project_id, payload)

    result = render_hair_export(db, config, project_id, export_id, payload)
    row = db.fetchone("SELECT * FROM hair_exports WHERE id = ?", (export_id,))
    previous_row = db.fetchone("SELECT status, output_path FROM hair_exports WHERE id = ?", (previous_id,))

    assert result["frames"] > 2
    assert row["status"] == "done"
    assert Path(row["output_path"]) == config.exports_dir / f"hair-timeline-{project_id}.mp4"
    assert Path(row["output_path"]).read_bytes()[4:8] == b"ftyp"
    assert not previous_output.exists()
    assert not previous_playback.exists()
    assert previous_row["status"] == "replaced"
    assert previous_row["output_path"] is None
    assert hair_playback_path(config, project_id) == config.hair_playback_dir / f"hair-timeline-{project_id}.mp4"


def test_hair_migration_is_idempotent(tmp_path):
    config = load_config(tmp_path / "home")
    Database(config.db_path)
    db = Database(config.db_path)
    tables = {row["name"] for row in db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"hair_measurements", "haircut_events", "hair_exports"}.issubset(tables)


def _shapes():
    long = np.zeros((120, 100), dtype=bool)
    long[5:92, 12:88] = True
    short = np.zeros_like(long)
    short[12:58, 24:76] = True
    return long, short


def test_head_anchor_rejects_background_and_beard_components():
    landmarks = _landmarks()[:, :2] * [100, 120]
    confidence = np.zeros((120, 100), dtype=np.float32)
    confidence[8:44, 28:72] = .9
    confidence[76:90, 38:60] = .95  # Disconnected beard.
    confidence[95:116, 78:99] = .95  # Unrelated lower-frame object.
    mask, quality, _ = refine_confidence_mask(confidence, landmarks)
    assert mask[20, 50]
    assert not mask[82, 50]
    assert not mask[100, 90]
    assert quality > .8


def test_invalid_probabilities_do_not_become_certain_hair():
    confidence = np.full((40, 40), np.inf, dtype=np.float32)
    mask, quality, reasons = refine_confidence_mask(confidence)
    assert not mask.any()
    assert quality == 0
    assert "low_hair_confidence" in reasons


def test_cropped_scalp_is_detected_even_with_a_small_border_contact():
    confidence = np.zeros((100, 100), dtype=np.float32)
    confidence[:35, 45:55] = .9
    _, _, reasons = refine_confidence_mask(confidence)
    assert "hair_touches_frame_edge" in reasons


def test_hair_metrics_reject_degenerate_and_nonfinite_landmarks():
    mask, _ = _shapes()
    assert hair_metrics(mask, np.ones((478, 3))) == {}
    points = _landmarks() * [100, 120, 1]
    points[33, 0] = np.nan
    assert hair_metrics(mask, points) == {}


def test_metric_extents_ignore_a_single_distant_pixel():
    _, mask = _shapes()
    points = _landmarks() * [100, 120, 1]
    original = hair_metrics(mask, points)
    noisy = mask.copy()
    noisy[0, 0] = True
    measured = hair_metrics(noisy, points)
    assert abs(measured["top_extent"] - original["top_extent"]) < .05
    assert abs(measured["left_extent"] - original["left_extent"]) < .05


def test_wet_or_shifted_hair_that_recovers_is_not_a_haircut(tmp_path):
    long, short = _shapes()
    config, db, project_id = _project(tmp_path, [long, long, short, long, long])
    assert update_haircut_suggestions(db, config, project_id) == 0
    assert not db.fetchall("SELECT * FROM haircut_events")


def test_equal_area_styling_change_is_not_a_haircut(tmp_path):
    long, _ = _shapes()
    shifted = np.roll(long, 8, axis=1)
    config, db, project_id = _project(tmp_path, [long, long, shifted, shifted, shifted])
    assert update_haircut_suggestions(db, config, project_id) == 0
    assert not db.fetchall("SELECT * FROM haircut_events")


def test_same_day_bursts_do_not_confirm_a_haircut(tmp_path):
    long, short = _shapes()
    config, db, project_id = _project(tmp_path, [long, long, short, short, short])
    db.execute("UPDATE photos SET captured_at = '2024-01-07 12:00:00' WHERE hash IN ('hair-3', 'hair-4')")
    assert update_haircut_suggestions(db, config, project_id) == 0
    event = db.fetchone("SELECT * FROM haircut_events")
    assert event["status"] == "provisional"


def test_camera_switch_and_long_gaps_do_not_generate_a_cut(tmp_path):
    long, short = _shapes()
    config, db, project_id = _project(tmp_path, [long, long, short, short, short])
    db.execute("UPDATE photos SET camera_model = 'A' WHERE hash IN ('hair-0', 'hair-1')")
    db.execute("UPDATE photos SET camera_model = 'B' WHERE hash NOT IN ('hair-0', 'hair-1')")
    assert update_haircut_suggestions(db, config, project_id) == 0
    db.execute("UPDATE photos SET camera_model = NULL")
    db.execute("UPDATE photos SET captured_at = '2024-07-01 10:00:00' WHERE hash = 'hair-2'")
    db.execute("UPDATE photos SET captured_at = '2024-07-04 10:00:00' WHERE hash = 'hair-3'")
    db.execute("UPDATE photos SET captured_at = '2024-07-07 10:00:00' WHERE hash = 'hair-4'")
    assert update_haircut_suggestions(db, config, project_id) == 0
    assert not db.fetchall("SELECT * FROM haircut_events")


def test_stale_suggestions_clear_but_confirmed_haircuts_survive(tmp_path):
    from selfietl.pipeline.hair import create_haircut_event
    long, short = _shapes()
    config, db, project_id = _project(tmp_path, [long, long, short, short, short])
    update_haircut_suggestions(db, config, project_id)
    confirmed = create_haircut_event(db, project_id, "2023-12-01")
    db.execute("UPDATE hair_measurements SET user_excluded = 1")
    assert update_haircut_suggestions(db, config, project_id) == 0
    events = db.fetchall("SELECT * FROM haircut_events")
    assert len(events) == 1 and events[0]["id"] == confirmed["id"]


def test_manifest_and_prediction_use_one_best_eligible_photo_per_day(tmp_path):
    from selfietl.pipeline.hair import get_project_hair
    _, short = _shapes()
    config, db, project_id = _project(tmp_path, [short, short, short])
    db.execute("UPDATE photos SET captured_at = '2024-01-01 11:00:00' WHERE hash = 'hair-1'")
    db.execute("UPDATE hair_measurements SET quality_score = .99, eligible = 0 WHERE photo_hash = 'hair-1'")
    manifest = get_project_hair(db, config, project_id)
    assert [frame["hash"] for frame in manifest["frames"]] == ["hair-0", "hair-2"]
    assert manifest["coverage"]["included"] == 2


def test_new_unmeasured_photo_changes_revision_and_reports_pending(tmp_path):
    from selfietl.pipeline.hair import get_project_hair
    _, mask = _shapes()
    config, db, project_id = _project(tmp_path, [mask, mask])
    before = project_hair_revision(db, config, project_id)
    db.execute("DELETE FROM hair_measurements WHERE photo_hash = 'hair-1'")
    assert project_hair_revision(db, config, project_id) != before
    manifest = get_project_hair(db, config, project_id)
    assert manifest["status"] == "stale"
    assert manifest["analysis"]["pending_photos"] == 1


def test_haircut_timer_works_without_hair_masks_and_dates_are_validated(tmp_path):
    from selfietl.pipeline.hair import create_haircut_event, get_project_hair
    _, mask = _shapes()
    config, db, project_id = _project(tmp_path, [mask])
    db.execute("DELETE FROM hair_measurements")
    first = create_haircut_event(db, project_id, "2024-01-02")
    assert create_haircut_event(db, project_id, "2024-01-02")["id"] == first["id"]
    manifest = get_project_hair(db, config, project_id)
    assert manifest["last_haircut"]["days_since"] == (date.today() - date(2024, 1, 2)).days
    assert len(manifest["haircuts"]) == 1
    with TestClient(create_app(config)) as client:
        future = (date.today() + timedelta(days=1)).isoformat()
        assert client.post(f"/api/projects/{project_id}/haircuts", json={"event_date": future}).status_code == 400
        assert client.patch(f"/api/haircuts/{first['id']}", json={"event_date": ""}).status_code == 400


def test_export_rejects_bad_ranges_or_one_day_without_queuing(tmp_path):
    _, mask = _shapes()
    config, db, project_id = _project(tmp_path, [mask])
    with TestClient(create_app(config)) as client:
        url = f"/api/projects/{project_id}/hair/export"
        assert client.post(url, json={"start_date": "2024-01-05", "end_date": "2024-01-01"}).status_code == 422
        assert client.post(url, json={"width": 361}).status_code == 422
        assert client.post(url, json={}).status_code == 400
    assert not db.fetchall("SELECT * FROM hair_exports")


def test_upgrade_reuses_valid_raw_masks_and_preserves_exclusions(tmp_path, monkeypatch):
    import hashlib
    from selfietl.pipeline import hair
    _, mask = _shapes()
    config, db, _ = _project(tmp_path, [mask])
    path = config.inbox_dir / "hair-0.jpg"
    stat = path.stat()
    legacy = hashlib.sha256(f"hair-v1|{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
    db.execute("UPDATE hair_measurements SET algorithm_version = 'hair-v1', source_signature = ?, user_excluded = 1", (legacy,))
    monkeypatch.setattr(hair, "create_hair_segmenter", lambda _: (_ for _ in ()).throw(AssertionError("Raw cache should be reused")))
    hair.analyze_photo_hair(db, config, "hair-0")
    row = db.fetchone("SELECT * FROM hair_measurements")
    assert row["algorithm_version"] == ALGORITHM_VERSION
    assert row["user_excluded"] == 1
    assert row["eligible"] == 1


def test_failed_recompute_does_not_leave_old_results_eligible(tmp_path, monkeypatch):
    from selfietl.pipeline import hair
    _, mask = _shapes()
    config, db, project_id = _project(tmp_path, [mask])
    monkeypatch.setattr(hair, "analyze_photo_hair", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("broken model")))
    result = hair.recompute_project_hair(db, config, project_id)
    manifest = hair.get_project_hair(db, config, project_id)
    assert result["failed"] == 1
    assert manifest["status"] == "ready"
    assert manifest["analysis"]["failed_photos"] == 1
    assert manifest["coverage"]["included"] == 0
    assert manifest["frames"][0]["composite_url"] is None


def test_each_saved_selfie_rechecks_and_confirms_persistent_changes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from datetime import datetime
    from selfietl.pipeline import hair, single
    from selfietl.pipeline.detect import DetectionResult
    long, short = _shapes()
    config, db, project_id = _project(tmp_path, [long, long, short, short])
    hair.update_haircut_suggestions(db, config, project_id)
    assert db.fetchone("SELECT status FROM haircut_events")["status"] == "provisional"
    monkeypatch.setattr(single, "detect_landmarks", lambda *a: DetectionResult(_landmarks(), (.1, .1, .8, .8), 1, 0, 0, 0, .3, .05, [], "test"))
    probability = short.astype(np.float32)[..., None] * .95
    segmenter = SimpleNamespace(segment=lambda _: SimpleNamespace(confidence_masks=[SimpleNamespace(numpy_view=lambda: 1 - probability), SimpleNamespace(numpy_view=lambda: probability)]), close=lambda: None)
    mp = SimpleNamespace(Image=lambda **kw: None, ImageFormat=SimpleNamespace(SRGB=1))
    monkeypatch.setattr(hair, "create_hair_segmenter", lambda _: (segmenter, mp))
    source = config.inbox_dir / "new-selfie.jpg"
    Image.new("RGB", (100, 120), "blue").save(source)
    result = single.process_single_photo(db, config, project_id, source, captured_at=datetime(2024, 1, 13, 10))
    event = db.fetchone("SELECT * FROM haircut_events")
    manifest = hair.get_project_hair(db, config, project_id)
    assert not result["skipped"]
    assert not any("hair" in warning for warning in result["warnings"])
    assert event["status"] == "suggested"
    assert json.loads(event["evidence_json"])["following_days"] == 2
    assert manifest["analysis"]["latest_analyzed_date"] == "2024-01-13"
    assert manifest["frames"][-1]["hash"] == result["hash"]


def test_normalized_metrics_are_invariant_to_scale_and_roll():
    _, mask = _shapes()
    landmarks = _landmarks() * [100, 120, 1]
    original = hair_metrics(mask, landmarks)
    scaled = np.asarray(Image.fromarray(mask).resize((200, 240), Image.Resampling.NEAREST))
    measured = hair_metrics(scaled, landmarks * [2, 2, 1])
    assert abs(original["area"] - measured["area"]) < .001
    assert abs(original["top_extent"] - measured["top_extent"]) < .05
    rotated = np.rot90(mask)
    rotated_points = landmarks.copy()
    rotated_points[:, 0] = landmarks[:, 1]
    rotated_points[:, 1] = 99 - landmarks[:, 0]
    rolled = hair_metrics(rotated, rotated_points)
    assert abs(original["area"] - rolled["area"]) < .001
    assert abs(original["top_extent"] - rolled["top_extent"]) < .05


def test_growth_summary_uses_comparable_recent_days_after_confirmed_cut(tmp_path):
    from selfietl.pipeline.hair import create_haircut_event, get_project_hair
    long, short = _shapes()
    config, db, project_id = _project(tmp_path, [short, short, short, long, long, long])
    create_haircut_event(db, project_id, "2024-01-01")
    for index, day in enumerate((1, 4, 7), start=3):
        db.execute("UPDATE photos SET captured_at = ? WHERE hash = ?", (f"2024-07-{day:02d} 10:00:00", f"hair-{index}"))
    change = get_project_hair(db, config, project_id)["change_since_haircut"]
    assert change["area_change_percent"] > 100
    assert change["baseline_date"] == "2024-01-01"
    assert change["latest_date"] == "2024-07-07"
    db.execute("UPDATE photos SET camera_model = 'different' WHERE hash IN ('hair-3', 'hair-4', 'hair-5')")
    db.execute("UPDATE photos SET camera_model = 'original' WHERE hash IN ('hair-0', 'hair-1', 'hair-2')")
    assert get_project_hair(db, config, project_id)["change_since_haircut"] is None


def test_new_canonical_is_realigned_before_hair_measurement(tmp_path):
    import os
    from selfietl.pipeline.hair import analyze_photo_hair, get_project_hair
    _, mask = _shapes()
    config, db, project_id = _project(tmp_path, [mask])
    aligned = config.aligned_landmarks_dir / "hair-0.npz"
    original_timestamp = aligned.stat().st_mtime_ns
    os.utime(aligned, ns=(original_timestamp - 10000000000, original_timestamp - 10000000000))
    assert get_project_hair(db, config, project_id)["status"] == "stale"
    analyze_photo_hair(db, config, "hair-0")
    assert aligned.stat().st_mtime_ns > original_timestamp
    assert get_project_hair(db, config, project_id)["status"] == "ready"


def test_adding_known_cut_confirms_existing_same_date_suggestion(tmp_path):
    from selfietl.pipeline.hair import create_haircut_event
    long, short = _shapes()
    config, db, project_id = _project(tmp_path, [long, long, short, short, short])
    update_haircut_suggestions(db, config, project_id)
    event = create_haircut_event(db, project_id, "2024-01-07")
    assert event["status"] == "confirmed"
    assert len(db.fetchall("SELECT * FROM haircut_events")) == 1
