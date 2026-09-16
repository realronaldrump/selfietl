from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy.stats import t as student_t

from selfietl.db import Database


ALGORITHM_VERSION = "face-shape-v3"
FULLNESS_FEATURE_NAMES = (
    "face_width_height",
    "jaw_cheek_ratio",
    "lower_face_width",
    "lower_face_area",
    "outline_roundness",
    "chin_cheek_ratio",
)
INSIGHT_FEATURE_NAMES = (
    "temple_cheek_ratio",
    "lower_face_height",
    "jaw_angle",
    "outline_asymmetry",
)
FEATURE_NAMES = FULLNESS_FEATURE_NAMES + INSIGHT_FEATURE_NAMES
FULLNESS_INDICES = np.array([FEATURE_NAMES.index(name) for name in FULLNESS_FEATURE_NAMES])
FEATURE_LABELS = {
    "face_width_height": "cheek width relative to eye-to-chin length",
    "jaw_cheek_ratio": "jaw breadth",
    "lower_face_width": "lower-cheek width",
    "lower_face_area": "lower-face area",
    "outline_roundness": "lower-face roundness",
    "chin_cheek_ratio": "chin breadth",
    "temple_cheek_ratio": "temple-to-cheek balance",
    "lower_face_height": "lower-face length",
    "jaw_angle": "jaw angle",
    "outline_asymmetry": "outline asymmetry",
}
FACE_OVAL = (
    10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365,
    379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93,
    234, 127, 162, 21, 54, 103, 67, 109,
)
LOWER_FACE = (
    454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148, 176, 149,
    150, 136, 172, 58, 132, 93, 234,
)
NUISANCE_NAMES = ("yaw", "pitch", "roll", "mouth_open_ratio")

Progress = Callable[[str, int, int, str], None]
CancelCheck = Callable[[], None]


@dataclass
class ScoredObservation:
    hash: str
    captured_at: str
    day: date
    index: float
    components: np.ndarray
    contour: np.ndarray
    capture_profile: str
    quality: float
    yaw: float
    pitch: float
    roll: float
    mouth_open_ratio: float
    observation_weight: float


def measure_photo(db: Database, photo_hash: str) -> dict[str, Any]:
    row = db.fetchone("SELECT * FROM photos WHERE hash = ?", (photo_hash,))
    if row is None:
        raise ValueError(f"Photo not found: {photo_hash}")

    landmark_path = Path(row["landmarks_path"]) if row["landmarks_path"] else None
    signature = _landmark_signature(landmark_path)
    reasons: list[str] = []
    metrics: dict[str, float] = {}
    contour: list[list[float]] | None = None

    if landmark_path is None or not landmark_path.exists():
        reasons.append("missing_landmarks")
    else:
        try:
            with np.load(landmark_path) as payload:
                landmarks = np.asarray(payload["landmarks"], dtype=np.float64)
                image_size = np.asarray(payload["image_size"] if "image_size" in payload else [row["width"], row["height"]], dtype=float)
            metrics, contour_array = extract_features(landmarks, image_size=image_size)
            contour = np.round(contour_array, 6).tolist()
        except Exception as exc:
            reasons.append(f"invalid_landmarks:{exc.__class__.__name__}")

    for name in (*NUISANCE_NAMES, "quality_score"):
        if row[name] is None or not math.isfinite(float(row[name])):
            reasons.append(f"invalid_{name}")
    yaw = _number(row["yaw"])
    pitch = _number(row["pitch"])
    roll = _number(row["roll"])
    mouth = _number(row["mouth_open_ratio"])
    quality = _number(row["quality_score"], default=0.0)
    if abs(yaw) > 10:
        reasons.append("yaw_too_large")
    if abs(pitch) > 12:
        reasons.append("pitch_too_large")
    if abs(roll) > 8:
        reasons.append("roll_too_large")
    if mouth > 0.12:
        reasons.append("mouth_too_open")
    if quality < 0.45:
        reasons.append("low_landmark_quality")

    capture_profile = _capture_profile(row)
    now = datetime.now().isoformat(sep=" ")
    db.execute(
        """
        INSERT INTO face_shape_measurements (
            photo_hash, algorithm_version, source_signature, metrics_json, contour_json,
            eligible, reasons_json, capture_profile, computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(photo_hash) DO UPDATE SET
            algorithm_version = excluded.algorithm_version,
            source_signature = excluded.source_signature,
            metrics_json = excluded.metrics_json,
            contour_json = excluded.contour_json,
            eligible = excluded.eligible,
            reasons_json = excluded.reasons_json,
            capture_profile = excluded.capture_profile,
            computed_at = excluded.computed_at
        """,
        (
            photo_hash,
            ALGORITHM_VERSION,
            signature,
            json.dumps(metrics, separators=(",", ":")),
            json.dumps(contour, separators=(",", ":")) if contour is not None else None,
            0 if reasons else 1,
            json.dumps(reasons, separators=(",", ":")),
            capture_profile,
            now,
        ),
    )
    return {"hash": photo_hash, "eligible": not reasons, "reasons": reasons, "metrics": metrics}


def extract_features(
    landmarks: np.ndarray, image_size: Any = None,
) -> tuple[dict[str, float], np.ndarray]:
    """Measure in isotropic image coordinates, then normalize by eye distance.

    image_size is mandatory for normalized detector output; omitted only for
    callers already supplying isotropic coordinates (e.g. geometry tests).
    """
    pts = np.asarray(landmarks, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] < 2 or len(pts) <= max(FACE_OVAL):
        raise ValueError("MediaPipe face landmarks are incomplete")
    pts = pts[:, :2].copy()
    if not np.isfinite(pts).all():
        raise ValueError("Face landmarks must be finite")
    if image_size is not None:
        size = np.asarray(image_size, dtype=float)
        if size.shape != (2,) or not np.isfinite(size).all() or np.any(size <= 0):
            raise ValueError("Image dimensions are missing or invalid")
        pts *= size / size[0]

    left_eye = (pts[33] + pts[133]) / 2
    right_eye = (pts[263] + pts[362]) / 2
    eye_vector = right_eye - left_eye
    interocular = float(np.linalg.norm(eye_vector))
    if interocular <= 1e-8:
        raise ValueError("Eye landmarks are degenerate")

    angle = math.atan2(float(eye_vector[1]), float(eye_vector[0]))
    rotation = np.array(
        [[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]],
        dtype=np.float64,
    )
    eye_mid = (left_eye + right_eye) / 2
    aligned = (pts - eye_mid) @ rotation / interocular
    oval = aligned[list(FACE_OVAL)]
    lower = aligned[list(LOWER_FACE)]

    # The forehead is particularly vulnerable to hair occlusion. It is retained
    # in the display contour, but never contributes to the fullness index.
    face_height = abs(float(aligned[152, 1]))
    temple_width = _distance(aligned[127], aligned[356])
    cheek_width = _distance(aligned[234], aligned[454])
    jaw_width = _distance(aligned[172], aligned[397])
    lower_width = _distance(aligned[58], aligned[288])
    chin_width = _distance(aligned[176], aligned[400])
    oval_area = _polygon_area(oval)
    lower_area = _polygon_area(lower)
    perimeter = float(np.sum(np.linalg.norm(lower - np.roll(lower, 1, axis=0), axis=1)))
    lower_face_height = abs(float(aligned[152, 1] - (aligned[234, 1] + aligned[454, 1]) / 2))
    jaw_angle = (_vertex_angle(aligned[136], aligned[172], aligned[132]) + _vertex_angle(aligned[365], aligned[397], aligned[361])) / (2 * math.pi)
    outline_asymmetry = _outline_asymmetry(aligned, cheek_width)
    if min(face_height, temple_width, cheek_width, jaw_width, lower_width, chin_width, oval_area, lower_area, perimeter) <= 1e-8:
        raise ValueError("Face contour is degenerate")

    metrics = {
        "face_width_height": cheek_width / face_height,
        "jaw_cheek_ratio": jaw_width / cheek_width,
        "lower_face_width": lower_width,
        "lower_face_area": lower_area,
        "outline_roundness": 4 * math.pi * lower_area / (perimeter * perimeter),
        "chin_cheek_ratio": chin_width / cheek_width,
        "temple_cheek_ratio": temple_width / cheek_width,
        "lower_face_height": lower_face_height / face_height,
        "jaw_angle": jaw_angle,
        "outline_asymmetry": outline_asymmetry,
    }
    if not all(np.isfinite(value) for value in metrics.values()):
        raise ValueError("Face features are not finite")
    return metrics, oval


def recompute_project(
    db: Database,
    project_id: int,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
    rebuild_baseline: bool = False,
) -> dict[str, Any]:
    rows = db.fetchall(
        """
        SELECT p.hash
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ?
        ORDER BY p.captured_at
        """,
        (project_id,),
    )
    eligible = 0
    for index, row in enumerate(rows):
        if cancel_check:
            cancel_check()
        result = measure_photo(db, row["hash"])
        eligible += int(result["eligible"])
        if progress:
            progress("face_shape", index + 1, len(rows), "Measuring face shape")

    active = _measurement_rows(db, project_id)
    current_profile = _load_profile(db, project_id)
    if len({str(row["captured_at"])[:10] for row in active}) < 6:
        return {"status": "insufficient", "measured": len(rows), "eligible": len(active), "required": 6, "required_unit": "distinct_days"}

    if current_profile is None or rebuild_baseline:
        baseline, correction = _build_profile(active)
        calibration = None
        computed_at = datetime.now().isoformat(sep=" ")
    else:
        baseline = current_profile["baseline"]
        correction = current_profile["correction"]
        calibration = current_profile["calibration"]
        computed_at = current_profile["computed_at"]

    source_revision = project_source_revision(db, project_id)
    updated_at = datetime.now().isoformat(sep=" ")
    db.execute(
        """
        INSERT INTO face_shape_profiles (
            project_id, algorithm_version, baseline_json, correction_json,
            calibration_json, source_revision, computed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            algorithm_version = excluded.algorithm_version,
            baseline_json = excluded.baseline_json,
            correction_json = excluded.correction_json,
            calibration_json = excluded.calibration_json,
            source_revision = excluded.source_revision,
            computed_at = excluded.computed_at,
            updated_at = excluded.updated_at,
            trend_json = NULL,
            trend_cache_key = NULL
        """,
        (
            project_id,
            ALGORITHM_VERSION,
            json.dumps(baseline, separators=(",", ":")),
            json.dumps(correction, separators=(",", ":")),
            json.dumps(calibration, separators=(",", ":")) if calibration else None,
            source_revision,
            computed_at,
            updated_at,
        ),
    )
    # Build the public report once as part of the explicit analysis job. Reads
    # can then serve the persisted result without repeating the trend math.
    get_project_trend(db, project_id)
    return {
        "status": "ready",
        "measured": len(rows),
        "eligible": len(active),
        "baseline_rebuilt": current_profile is None or rebuild_baseline,
        "source_revision": source_revision,
    }


def get_project_trend(db: Database, project_id: int) -> dict[str, Any]:
    profile = _load_profile(db, project_id)
    landmark_count = int(
        db.fetchone(
            """
            SELECT COUNT(*) AS total
            FROM photos p JOIN project_photos pp ON pp.photo_hash = p.hash
            WHERE pp.project_id = ? AND p.landmarks_path IS NOT NULL
            """,
            (project_id,),
        )["total"]
    )
    measurement_count = int(
        db.fetchone(
            """
            SELECT COUNT(*) AS total
            FROM face_shape_measurements m
            JOIN project_photos pp ON pp.photo_hash = m.photo_hash
            WHERE pp.project_id = ? AND m.algorithm_version = ?
            """,
            (project_id, ALGORITHM_VERSION),
        )["total"]
    )
    if profile is None:
        eligible_days = len({str(row["captured_at"])[:10] for row in _measurement_rows(db, project_id)})
        status = "insufficient" if measurement_count >= landmark_count and eligible_days < 6 else "not_ready"
        return _empty_trend(status, landmark_count, measurement_count, eligible_days=eligible_days)

    current_revision = project_source_revision(db, project_id)
    if (
        profile["cached_trend"] is not None
        and profile["trend_cache_key"] == _trend_cache_key(profile)
    ):
        if current_revision != profile["source_revision"]:
            return _stale_cached_trend(profile["cached_trend"], current_revision)
        return profile["cached_trend"]

    return _build_project_trend(
        db,
        project_id,
        profile,
        current_revision,
        landmark_count,
        measurement_count,
    )


def _build_project_trend(
    db: Database,
    project_id: int,
    profile: dict[str, Any],
    current_revision: str,
    landmark_count: int,
    measurement_count: int,
) -> dict[str, Any]:
    stale = profile["source_revision"] != current_revision or measurement_count < landmark_count
    observations = _score_rows(_measurement_rows(db, project_id), profile)
    if len(observations) < 3:
        trend = _empty_trend("insufficient", landmark_count, measurement_count)
        _store_trend_cache(db, project_id, profile, trend)
        return trend

    daily = _daily_observations(observations)
    points, events = _trend_points(daily)
    usable_points = [point for point in points if point.get("trend_index") is not None]
    latest = usable_points[-1] if usable_points else None
    prior = _point_near_days_before(usable_points, latest, 90) if latest else None
    change = round(float(latest["trend_index"] - prior["trend_index"]), 2) if latest and prior else None
    uncertainty = float((latest or {}).get("uncertainty") or 0.0) + float((prior or {}).get("uncertainty") or 0.0)
    if change is None:
        direction = "unknown"
    elif abs(change) <= max(0.2, uncertainty):
        direction = "steady"
    else:
        direction = "fuller" if change > 0 else "leaner"
    statistics = _trend_statistics(daily, points)
    possible_shift = _possible_change_point(daily)
    insights = _shape_insights(latest, prior, statistics, possible_shift)

    excluded = int(
        db.fetchone(
            """
            SELECT COUNT(*) AS total
            FROM photos p JOIN project_photos pp ON pp.photo_hash = p.hash
            LEFT JOIN face_shape_measurements m ON m.photo_hash = p.hash
            WHERE pp.project_id = ? AND (p.skipped = 1 OR COALESCE(m.eligible, 0) = 0)
            """,
            (project_id,),
        )["total"]
    )
    quality_checks = _quality_diagnostics(db, project_id, profile)
    baseline = profile["baseline"]
    trend = {
        "status": "stale" if stale else "ready",
        "analysis_version": ALGORITHM_VERSION,
        "analysis_revision": current_revision,
        "generated_at": profile["updated_at"],
        "metric": {
            "unit": "personal_robust_sd",
            "baseline_value": 0,
            "interval_method": "approximate_robust_with_dependence_adjustment",
            "feature_scope": "lower_face_excluding_forehead",
            "higher_means": "fuller_like",
            "disclaimer": "A personal visual trend, not a weight measurement or medical assessment.",
        },
        "baseline": {
            "start": baseline["start"],
            "end": baseline["end"],
            "observation_count": baseline["observation_count"],
            "distinct_days": baseline["distinct_days"],
            "frozen": True,
        },
        "calibration": profile["calibration"] or {"status": "automatic"},
        "summary": {
            "latest_date": latest["date"] if latest else None,
            "latest_index": latest["trend_index"] if latest else None,
            "change_90d": change,
            "direction_90d": direction,
            "confidence": latest["confidence"] if latest else "unavailable",
        },
        "insights": insights,
        "statistics": statistics,
        "possible_change_point": possible_shift,
        "coverage": {
            "eligible_photos": len(observations),
            "eligible_days": len(daily),
            "excluded_photos": excluded,
            "first_date": daily[0]["date"] if daily else None,
            "last_date": daily[-1]["date"] if daily else None,
        },
        "quality_checks": quality_checks,
        "points": points,
        "events": events,
    }
    _store_trend_cache(db, project_id, profile, trend)
    return trend


def _store_trend_cache(
    db: Database,
    project_id: int,
    profile: dict[str, Any],
    trend: dict[str, Any],
) -> None:
    db.execute(
        """
        UPDATE face_shape_profiles
        SET trend_json = ?, trend_cache_key = ?
        WHERE project_id = ?
        """,
        (
            json.dumps(trend, separators=(",", ":")),
            _trend_cache_key(profile),
            project_id,
        ),
    )


def _trend_cache_key(profile: dict[str, Any]) -> str:
    return f"{profile['source_revision']}:{profile['updated_at']}"


def _stale_cached_trend(cached_trend: dict[str, Any], current_revision: str) -> dict[str, Any]:
    stale_trend = json.loads(json.dumps(cached_trend))
    stale_trend["status"] = "stale"
    stale_trend["source_revision"] = current_revision
    return stale_trend


def export_project_analysis(db: Database, project_id: int, format: str = "csv") -> tuple[str, str, str]:
    trend = get_project_trend(db, project_id)
    if trend["status"] in {"not_ready", "insufficient"}:
        raise ValueError("Face-shape analysis is not ready to export")
    project = db.fetchone("SELECT name FROM projects WHERE id = ?", (project_id,))
    project_name = project["name"] if project else None
    safe_name = "".join(
        character if character.isascii() and (character.isalnum() or character in "-_") else "-"
        for character in str(project_name or f"project-{project_id}")
    )
    stamp = datetime.now().date().isoformat()
    if format == "json":
        payload = {
            "schema_version": 1,
            "exported_at": datetime.now().isoformat(sep=" "),
            "project": {"id": project_id, "name": project_name},
            "analysis": trend,
        }
        return json.dumps(payload, indent=2), "application/json", f"{safe_name}-face-change-{stamp}.json"
    if format != "csv":
        raise ValueError("Export format must be csv or json")

    output = io.StringIO(newline="")
    fieldnames = [
        "date", "raw_index", "trend_index", "lower_95", "upper_95", "confidence",
        "sample_count", "capture_profile", "representative_hash", *FEATURE_NAMES,
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for point in trend["points"]:
        if point.get("is_break"):
            continue
        components = point.get("components", {})
        writer.writerow(
            {
                "date": point["date"],
                "raw_index": point.get("raw_index"),
                "trend_index": point.get("trend_index"),
                "lower_95": point.get("lower"),
                "upper_95": point.get("upper"),
                "confidence": point.get("confidence"),
                "sample_count": point.get("sample_count"),
                "capture_profile": point.get("capture_profile"),
                "representative_hash": (point.get("representative") or {}).get("hash"),
                **{name: components.get(name) for name in FEATURE_NAMES},
            }
        )
    return output.getvalue(), "text/csv; charset=utf-8", f"{safe_name}-face-change-{stamp}.csv"


def compare_periods(
    db: Database,
    project_id: int,
    a: dict[str, str],
    b: dict[str, str],
) -> dict[str, Any]:
    profile = _load_profile(db, project_id)
    if profile is None:
        raise ValueError("Face-shape analysis is not ready")
    if project_source_revision(db, project_id) != profile["source_revision"]:
        raise ValueError("Recompute face-shape analysis before comparing changed inputs")
    observations = _score_rows(_measurement_rows(db, project_id), profile)
    period_a = _period_summary(observations, a)
    period_b = _period_summary(observations, b)
    delta = float(period_b["index"] - period_a["index"])
    threshold = max(0.2, period_a["uncertainty"] + period_b["uncertainty"])
    same_profile = (
        len(period_a["capture_profiles"]) == 1
        and period_a["capture_profiles"] == period_b["capture_profiles"]
    )
    limitations = [] if same_profile else ["incompatible_capture_profiles"]
    selected_a = [item for item in observations if _parse_date(a["start"]) <= item.day <= _parse_date(a["end"])]
    selected_b = [item for item in observations if _parse_date(b["start"]) <= item.day <= _parse_date(b["end"])]
    try:
        _validate_anchor_pose(selected_a, selected_b)
    except ValueError:
        limitations.append("pose_or_expression_mismatch")
    if min(period_a["distinct_days"], period_b["distinct_days"]) < 3:
        limitations.append("insufficient_distinct_days")
    if limitations or abs(delta) <= threshold:
        conclusion = "no_clear_change"
    else:
        conclusion = "fuller" if delta > 0 else "leaner"
    confidence = _combined_confidence(period_a["confidence"], period_b["confidence"], not limitations)
    contributions = []
    for index, name in enumerate(FEATURE_NAMES):
        contributions.append(
            {
                "region": _region_label(name),
                "feature": name,
                "delta": round(float(period_b["components"][index] - period_a["components"][index]), 2),
                "kind": "fullness" if name in FULLNESS_FEATURE_NAMES else "proportion",
                "observation": _comparison_observation(
                    name,
                    float(period_b["components"][index] - period_a["components"][index]),
                ),
            }
        )
    return {
        "a": _public_period(period_a),
        "b": _public_period(period_b),
        "delta": round(delta, 2),
        "uncertainty": round(threshold, 2),
        "conclusion": conclusion,
        "confidence": confidence,
        "same_capture_profile": same_profile,
        "limitations": limitations,
        "contributions": sorted(contributions, key=lambda item: abs(item["delta"]), reverse=True),
        "disclaimer": "Face Shape Index is a personal visual trend, not a weight measurement.",
    }


def update_calibration(
    db: Database,
    project_id: int,
    lighter: dict[str, str] | None,
    fuller: dict[str, str] | None,
) -> dict[str, Any]:
    profile = _load_profile(db, project_id)
    if profile is None:
        raise ValueError("Run face-shape analysis before calibration")
    if project_source_revision(db, project_id) != profile["source_revision"]:
        raise ValueError("Recompute face-shape analysis before calibrating changed inputs")
    if lighter is None and fuller is None:
        calibration = None
    elif lighter is None or fuller is None:
        raise ValueError("Both lighter and fuller periods are required")
    else:
        lighter_range = _validated_period(lighter)
        fuller_range = _validated_period(fuller)
        if not (lighter_range[1] < fuller_range[0] or fuller_range[1] < lighter_range[0]):
            raise ValueError("Lighter and fuller periods must not overlap")
        observations = _score_rows(_measurement_rows(db, project_id), profile, ignore_calibration=True)
        light = [item for item in observations if lighter_range[0] <= item.day <= lighter_range[1]]
        full = [item for item in observations if fuller_range[0] <= item.day <= fuller_range[1]]
        _validate_anchor_samples(light, "lighter")
        _validate_anchor_samples(full, "fuller")
        shared_profiles = set(item.capture_profile for item in light) & set(item.capture_profile for item in full)
        if not shared_profiles:
            raise ValueError("Anchor periods use incompatible capture profiles")
        _validate_anchor_pose(light, full)
        if {item.capture_profile for item in light} != {item.capture_profile for item in full} or len(shared_profiles) != 1:
            raise ValueError("Anchor periods must use one matching capture profile")
        light_components = np.median(np.stack([item["components"][FULLNESS_INDICES] for item in _daily_observations(light)]), axis=0)
        full_components = np.median(np.stack([item["components"][FULLNESS_INDICES] for item in _daily_observations(full)]), axis=0)
        difference = full_components - light_components
        orientation = 1.0 if float(np.median(difference)) >= 0 else -1.0
        strengths = np.maximum(orientation * difference, 0.0)
        if float(np.median(strengths)) < 0.2:
            raise ValueError("The selected periods are not separated enough for reliable calibration")
        personalized = strengths / max(float(strengths.sum()), 1e-9)
        automatic = np.asarray(
            profile["baseline"].get("default_weights", np.ones(len(FULLNESS_FEATURE_NAMES)) / len(FULLNESS_FEATURE_NAMES)),
            dtype=np.float64,
        )
        weights = (0.5 * personalized + 0.5 * automatic) * (strengths > 0)
        weights /= weights.sum()
        calibration = {
            "status": "calibrated",
            "lighter": {**lighter, "used": len(light)},
            "fuller": {**fuller, "used": len(full)},
            "weights": np.round(weights, 8).tolist(),
            "orientation": orientation,
            "separation": round(float(abs(np.dot(difference, weights))), 3),
            "updated_at": datetime.now().isoformat(sep=" "),
        }

    now = datetime.now().isoformat(sep=" ")
    db.execute(
        """
        UPDATE face_shape_profiles
        SET calibration_json = ?, updated_at = ?, trend_json = NULL, trend_cache_key = NULL
        WHERE project_id = ?
        """,
        (json.dumps(calibration, separators=(",", ":")) if calibration else None, now, project_id),
    )
    return calibration or {"status": "automatic"}


def project_source_revision(db: Database, project_id: int) -> str:
    rows = db.fetchall(
        """
        SELECT p.hash, p.captured_at, p.skipped, p.landmarks_path,
               COALESCE(m.algorithm_version, ''), COALESCE(m.source_signature, ''),
               p.quality_score, p.yaw, p.pitch, p.roll, p.mouth_open_ratio,
               p.camera_make, p.camera_model, p.width, p.height, m.eligible,
               m.metrics_json, m.contour_json, m.capture_profile
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        LEFT JOIN face_shape_measurements m ON m.photo_hash = p.hash
        WHERE pp.project_id = ?
        ORDER BY p.hash
        """,
        (project_id,),
    )
    digest = hashlib.sha256()
    for row in rows:
        digest.update("|".join(str(value) for value in row).encode("utf-8"))
        digest.update(_landmark_signature(Path(row["landmarks_path"]) if row["landmarks_path"] else None).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _build_profile(rows: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    # Learn nuisance effects ONLY from within-day, same-camera variation.
    # Archive-wide regression confounds actual time trends with camera habits.
    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["captured_at"])[:10], row["capture_profile"])].append(row)
    daily_rows, matrix, nuisance = [], [], []
    x_residual, y_residual = [], []
    for key in sorted(grouped):
        # Identical repeats must not create additional correction evidence.
        unique = {tuple(_row_features(row) + _row_nuisance(row)): row for row in grouped[key]}
        items = list(unique.values())
        features = np.asarray([_row_features(row) for row in items])
        poses = np.asarray([_row_nuisance(row) for row in items])
        matrix.append(np.median(features, axis=0))
        nuisance.append(np.median(poses, axis=0))
        daily_rows.append(items[0])
        if len(items) >= 2:
            # One contrast per date/profile keeps large bursts from dominating.
            first, last = sorted(range(len(items)), key=lambda i: tuple(poses[i]))[::len(items)-1]
            x_residual.append(poses[last] - poses[first])
            y_residual.append(features[last] - features[first])
    matrix = np.asarray(matrix)
    nuisance = np.asarray(nuisance)
    nuisance_center = np.median(nuisance, axis=0)
    slopes = np.zeros((len(FEATURE_NAMES), len(NUISANCE_NAMES)))
    correction_days = len({str(row["captured_at"])[:10] for row in daily_rows
                           if len(grouped[(str(row["captured_at"])[:10], row["capture_profile"])]) > 1})
    if len(x_residual) >= 8 and correction_days >= 8:
        x = np.asarray(x_residual)
        y = np.asarray(y_residual)
        for feature_index in range(len(FEATURE_NAMES)):
            slopes[feature_index] = _robust_slopes(np.vstack([x, -x]), np.concatenate([y[:, feature_index], -y[:, feature_index]]))
    corrected = matrix - (nuisance - nuisance_center) @ slopes.T
    capture_profiles = [str(row["capture_profile"]) for row in daily_rows]
    capture_offsets = _overlapping_capture_offsets(daily_rows, corrected, capture_profiles)
    for row_index, capture_profile in enumerate(capture_profiles):
        corrected[row_index] -= np.asarray(capture_offsets.get(capture_profile, np.zeros(len(FEATURE_NAMES))))
    # Each calendar day gets one baseline vote, even with multiple cameras.
    by_day: dict[str, list[np.ndarray]] = defaultdict(list)
    for row, values in zip(daily_rows, corrected):
        by_day[str(row["captured_at"])[:10]].append(values)
    balanced = np.stack([np.median(by_day[day], axis=0) for day in sorted(by_day)])
    center = np.median(balanced, axis=0)
    scale = 1.4826 * np.median(np.abs(balanced - center), axis=0)
    # Numerical/landmark noise must not become enormous standardized changes
    # when a baseline feature is effectively constant. These are conservative
    # engineering floors, not empirically measured clinical precision.
    scale_floor = np.maximum(0.01, np.abs(center) * 0.01)
    scale = np.maximum(scale, scale_floor)
    default_weights = _default_fullness_weights(balanced, center, scale)
    dates = sorted(by_day)
    baseline = {
        "feature_names": list(FEATURE_NAMES),
        "center": np.round(center, 10).tolist(),
        "scale": np.round(scale, 10).tolist(),
        "scale_floor": np.round(scale_floor, 10).tolist(),
        "start": dates[0], "end": dates[-1],
        "observation_count": len(rows), "distinct_days": len(dates),
        "default_weights": np.round(default_weights, 8).tolist(),
    }
    correction = {
        "nuisance_names": list(NUISANCE_NAMES),
        "nuisance_center": np.round(nuisance_center, 10).tolist(),
        "slopes": np.round(slopes, 10).tolist(),
        "capture_offsets": capture_offsets,
        "method": "within_day" if np.any(slopes) else "eligibility_filters_only",
        "paired_days": correction_days,
        "nuisance_min": np.min([_row_nuisance(row) for row in rows], axis=0).tolist(),
        "nuisance_max": np.max([_row_nuisance(row) for row in rows], axis=0).tolist(),
    }
    return baseline, correction


def _robust_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    if len(x) < 8:
        return np.zeros(x.shape[1], dtype=np.float64)
    predictor_scale = 1.4826 * np.median(np.abs(x - np.median(x, axis=0)), axis=0)
    fallback = np.std(x, axis=0)
    predictor_scale = np.where(predictor_scale > 1e-8, predictor_scale, np.where(fallback > 1e-8, fallback, 1.0))
    standardized = x / predictor_scale
    design = np.column_stack([np.ones(len(x)), standardized])
    weights = np.ones(len(y), dtype=np.float64)
    coefficients = np.zeros(design.shape[1], dtype=np.float64)
    ridge = np.diag([0.0, *([max(0.5, 8.0 / len(x))] * x.shape[1])])
    for _ in range(8):
        weighted_design = design * weights[:, None]
        coefficients = np.linalg.solve(design.T @ weighted_design + ridge, design.T @ (weights * y))
        residual = y - design @ coefficients
        scale = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
        scale = max(scale, 1e-6)
        normalized = np.abs(residual) / (1.345 * scale)
        weights = np.where(normalized <= 1, 1.0, 1.0 / np.maximum(normalized, 1e-9))
    return coefficients[1:] / predictor_scale


def _default_fullness_weights(corrected: np.ndarray, center: np.ndarray, scale: np.ndarray) -> np.ndarray:
    count = len(FULLNESS_FEATURE_NAMES)
    if len(corrected) < 8:
        return np.ones(count, dtype=np.float64) / count
    standardized = (corrected[:, FULLNESS_INDICES] - center[FULLNESS_INDICES]) / scale[FULLNESS_INDICES]
    centered = standardized - standardized.mean(axis=0)
    norms = np.linalg.norm(centered, axis=0)
    unit = centered / np.maximum(norms, 1e-12)
    correlation = unit.T @ unit
    redundancy = np.sum(np.abs(correlation), axis=1) - np.abs(np.diag(correlation))
    weights = 1.0 / (1.0 + np.maximum(redundancy, 0.0))
    weights = np.clip(weights, 0.5 / count, 2.0 / count)
    return weights / weights.sum()


def _overlapping_capture_offsets(rows: list[Any], corrected: np.ndarray, profiles: list[str]) -> dict[str, list[float]]:
    counts = {profile: profiles.count(profile) for profile in sorted(set(profiles))}
    reference = max(counts, key=counts.get)
    by_profile: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    for row, values, profile in zip(rows, corrected, profiles):
        by_profile[profile][str(row["captured_at"])[:10]] = values
    offsets = {reference: [0.0] * len(FEATURE_NAMES)}
    for profile in counts:
        if profile == reference:
            continue
        # Date-range overlap alone is not matched evidence: different sampling
        # within that range can mistake real change for a camera offset.
        shared = sorted(set(by_profile[reference]) & set(by_profile[profile]))
        if len(shared) >= 3:
            differences = [by_profile[profile][day] - by_profile[reference][day] for day in shared]
            offsets[profile] = np.round(np.median(differences, axis=0), 10).tolist()
    return offsets


def _score_rows(rows: list[Any], profile: dict[str, Any], ignore_calibration: bool = False) -> list[ScoredObservation]:
    baseline = profile["baseline"]
    correction = profile["correction"]
    center = np.asarray(baseline["center"], dtype=np.float64)
    scale = np.asarray(baseline["scale"], dtype=np.float64)
    nuisance_center = np.asarray(correction["nuisance_center"], dtype=np.float64)
    slopes = np.asarray(correction["slopes"], dtype=np.float64)
    capture_offsets = correction.get("capture_offsets", {})
    calibration = None if ignore_calibration else profile.get("calibration")
    weights = np.asarray(
        (calibration or {}).get("weights", baseline.get("default_weights", np.ones(len(FULLNESS_FEATURE_NAMES)) / len(FULLNESS_FEATURE_NAMES))),
        dtype=np.float64,
    )
    orientation = float((calibration or {}).get("orientation", 1.0))
    result: list[ScoredObservation] = []
    for row in rows:
        raw = np.asarray(_row_features(row), dtype=np.float64)
        nuisance = np.asarray(_row_nuisance(row), dtype=np.float64)
        supported_nuisance = np.clip(nuisance, correction.get("nuisance_min", nuisance), correction.get("nuisance_max", nuisance))
        corrected = raw - slopes @ (supported_nuisance - nuisance_center)
        corrected -= np.asarray(capture_offsets.get(row["capture_profile"], [0.0] * len(FEATURE_NAMES)), dtype=np.float64)
        components = (corrected - center) / scale
        index = orientation * float(np.dot(components[FULLNESS_INDICES], weights))
        contour = np.asarray(json.loads(row["contour_json"]), dtype=np.float64)
        result.append(
            ScoredObservation(
                hash=row["hash"],
                captured_at=str(row["captured_at"]),
                day=_parse_date(str(row["captured_at"])[:10]),
                index=index,
                components=components,
                contour=contour,
                capture_profile=row["capture_profile"],
                quality=_number(row["quality_score"], default=0.0),
                yaw=_number(row["yaw"]),
                pitch=_number(row["pitch"]),
                roll=_number(row["roll"]),
                mouth_open_ratio=_number(row["mouth_open_ratio"]),
                observation_weight=_observation_weight(row),
            )
        )
    return result


def _daily_observations(observations: list[ScoredObservation]) -> list[dict[str, Any]]:
    grouped: dict[date, list[ScoredObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.day].append(observation)
    result = []
    for day in sorted(grouped):
        items = grouped[day]
        # Never blend unidentifiable camera differences into one daily reading.
        profiles = sorted({item.capture_profile for item in items})
        best_profile = max(profiles, key=lambda name: np.median([item.observation_weight for item in items if item.capture_profile == name]))
        items = [item for item in items if item.capture_profile == best_profile]
        weights = np.asarray([item.observation_weight for item in items], dtype=np.float64)
        index = _weighted_median(np.asarray([item.index for item in items]), weights)
        representative = min(items, key=lambda item: (abs(item.index - index), -item.quality))
        component_matrix = np.stack([item.components for item in items])
        components = np.asarray([_weighted_median(component_matrix[:, index], weights) for index in range(len(FEATURE_NAMES))])
        agreement = float(np.median(np.std(component_matrix, axis=0))) if len(items) > 1 else 0.0
        confidence_score = max(0.15, min(1.0, representative.observation_weight * (1.0 - min(agreement, 2.5) / 4)))
        result.append(
            {
                "date": day.isoformat(),
                "day": day,
                "index": index,
                "hash": representative.hash,
                "quality": representative.quality,
                "confidence_score": confidence_score,
                "components": components,
                "capture_profile": representative.capture_profile,
            }
        )
    return result


def _trend_points(daily: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    points: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    segment = 0
    segment_previous: dict[str, Any] | None = None
    for candidate in daily:
        if segment_previous is not None:
            gap = (candidate["day"] - segment_previous["day"]).days
            if gap > 120 or candidate["capture_profile"] != segment_previous["capture_profile"]:
                segment += 1
        candidate["segment"] = segment
        segment_previous = candidate
    previous: dict[str, Any] | None = None
    for item in daily:
        if previous is not None:
            gap_days = (item["day"] - previous["day"]).days
            profile_changed = item["capture_profile"] != previous["capture_profile"]
            if gap_days > 120 or profile_changed:
                midpoint = previous["day"] + timedelta(days=max(1, gap_days // 2))
                if gap_days > 120:
                    events.append({"date": item["date"], "type": "archive_gap", "label": f"No reliable observations for {gap_days} days"})
                if profile_changed:
                    events.append({"date": item["date"], "type": "capture_profile_change", "label": "Capture source changed"})
                points.append({"date": midpoint.isoformat(), "is_break": True, "trend_index": None, "lower": None, "upper": None})
        segment_daily = [candidate for candidate in daily if candidate["segment"] == item["segment"]]
        local = [candidate for candidate in segment_daily if abs((candidate["day"] - item["day"]).days) <= 45]
        if len(local) < 4:
            nearby = sorted(segment_daily, key=lambda candidate: abs((candidate["day"] - item["day"]).days))
            local = sorted(
                [candidate for candidate in nearby[: min(8, len(nearby))] if abs((candidate["day"] - item["day"]).days) <= 90],
                key=lambda candidate: candidate["day"],
            )
        trend = uncertainty = lower = upper = None
        component_trends: dict[str, float] = {}
        confidence = "low"
        window_start = window_end = item["date"]
        if len(local) >= 3:
            trend, uncertainty = _local_robust_estimate(local, item["day"])
            lower = trend - uncertainty
            upper = trend + uncertainty
            local_weights = _temporal_weights(local, item["day"])
            component_matrix = np.stack([candidate["components"] for candidate in local])
            component_trends = {
                name: round(_weighted_median(component_matrix[:, index], local_weights), 3)
                for index, name in enumerate(FEATURE_NAMES)
            }
            profile_count = len({candidate["capture_profile"] for candidate in local})
            if _effective_sample_size(local_weights) >= 6 and uncertainty <= 0.45 and profile_count == 1 and np.median([c["confidence_score"] for c in local]) >= 0.65:
                confidence = "high"
            elif _effective_sample_size(local_weights) >= 3 and uncertainty <= 0.9 and np.median([c["confidence_score"] for c in local]) >= 0.35:
                confidence = "medium"
            window_start = local[0]["date"]
            window_end = local[-1]["date"]
        points.append(
            {
                "date": item["date"],
                "raw_index": round(float(item["index"]), 3),
                "trend_index": round(trend, 3) if trend is not None else None,
                "lower": round(lower, 3) if lower is not None else None,
                "upper": round(upper, 3) if upper is not None else None,
                "uncertainty": round(uncertainty, 3) if uncertainty is not None else None,
                "interval_level": 0.95 if uncertainty is not None else None,
                "confidence": confidence,
                "sample_count": len(local),
                "window_start": window_start,
                "window_end": window_end,
                "representative": {
                    "hash": item["hash"],
                    "thumb_url": f"/api/photos/{item['hash']}/thumb",
                    "image_url": f"/api/photos/{item['hash']}/image",
                    "aligned_url": f"/api/photos/{item['hash']}/aligned",
                },
                "capture_profile": item["capture_profile"],
                "segment": item["segment"],
                "components": component_trends,
            }
        )
        previous = item
    return points, events


def _local_robust_estimate(local: list[dict[str, Any]], target: date) -> tuple[float, float]:
    x = np.asarray([(candidate["day"] - target).days for candidate in local], dtype=np.float64)
    y = np.asarray([candidate["index"] for candidate in local], dtype=np.float64)
    base_weights = _temporal_weights(local, target)
    design = np.column_stack([np.ones(len(x)), x])
    weights = base_weights.copy()
    coefficients = np.array([_weighted_median(y, weights), 0.0], dtype=np.float64)
    for _ in range(8):
        root = np.sqrt(np.maximum(weights, 1e-9))
        coefficients, *_ = np.linalg.lstsq(design * root[:, None], y * root, rcond=None)
        residual = y - design @ coefficients
        scale = 1.4826 * _weighted_median(np.abs(residual - _weighted_median(residual, weights)), weights)
        # A zero MAD can mean a perfect inlier line plus a gross outlier.
        # Do not accept the contaminated least-squares fit in that case.
        scale = max(scale, 0.04)
        normalized = np.abs(residual) / (1.345 * scale)
        robust = np.where(normalized <= 1, 1.0, 1.0 / np.maximum(normalized, 1e-9))
        weights = base_weights * robust
    residual = y - design @ coefficients
    spread = 1.4826 * _weighted_median(np.abs(residual - _weighted_median(residual, weights)), weights)
    effective_n = max(_effective_sample_size(weights), 1.0)
    # Intercept leverage grows near a window edge; sqrt(n) alone understates it.
    normalized_weights = weights / max(weights.sum(), 1e-12)
    mean_x = float(np.dot(normalized_weights, x))
    variance_x = float(np.dot(normalized_weights, (x - mean_x) ** 2))
    leverage = (1.0 + mean_x * mean_x / max(variance_x, 1e-9)) / effective_n
    # Adjacent residuals need not be independent (lighting/pose habits persist).
    inflation = _serial_inflation(residual)
    critical = float(student_t.ppf(0.975, max(1.0, effective_n - 2)))
    half_width_95 = max(0.08, critical * max(spread, 0.04) * math.sqrt(leverage * inflation))
    return float(coefficients[0]), float(half_width_95)


def _temporal_weights(local: list[dict[str, Any]], target: date) -> np.ndarray:
    distances = np.asarray([abs((candidate["day"] - target).days) for candidate in local], dtype=np.float64)
    bandwidth = max(46.0, float(distances.max(initial=0.0)) + 1.0)
    temporal = np.maximum(0.0, 1.0 - (distances / bandwidth) ** 3) ** 3
    quality = np.asarray([candidate.get("confidence_score", 0.5) for candidate in local], dtype=np.float64)
    return np.maximum(temporal * quality, 1e-6)


def _effective_sample_size(weights: np.ndarray) -> float:
    total = float(np.sum(weights))
    squared = float(np.sum(np.square(weights)))
    return total * total / squared if squared > 1e-12 else 0.0


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    values = np.asarray(values, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    order = np.argsort(values)
    ordered_values = values[order]
    ordered_weights = np.maximum(weights[order], 0.0)
    total = float(ordered_weights.sum())
    if total <= 1e-12:
        return float(np.median(values))
    position = int(np.searchsorted(np.cumsum(ordered_weights), total / 2, side="left"))
    if position + 1 < len(ordered_values) and math.isclose(float(np.cumsum(ordered_weights)[position]), total / 2, rel_tol=1e-12):
        return float((ordered_values[position] + ordered_values[position + 1]) / 2)
    return float(ordered_values[min(position, len(ordered_values) - 1)])


def _serial_inflation(values: np.ndarray) -> float:
    """Conservative AR(1) effective-sample adjustment; never shrink intervals."""
    values = np.asarray(values, dtype=float)
    if len(values) < 5 or min(np.std(values[:-1]), np.std(values[1:])) < 1e-9:
        return 1.0
    rho = float(np.clip(np.corrcoef(values[:-1], values[1:])[0, 1], 0, 0.8))
    return min(len(values) / 2, (1 + rho) / (1 - rho))


def _block_indices(n: int, rng: np.random.Generator, repeats: int) -> np.ndarray:
    """Circular moving blocks preserve short-range dependence between days."""
    length = max(1, min(n // 2, math.ceil(n ** (1 / 3))))
    starts = rng.integers(0, n, size=(repeats, math.ceil(n / length)))
    return ((starts[:, :, None] + np.arange(length)) % n).reshape(repeats, -1)[:, :n]


def _median_interval_half_width(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=np.float64)
    if len(values) < 2:
        return 0.25
    rng = np.random.default_rng(20_240_610 + len(values))
    medians = np.median(values[_block_indices(len(values), rng, 400)], axis=1)
    low, high = np.quantile(medians, [0.025, 0.975])
    center = float(np.median(values))
    return max(0.08, float(max(center - low, high - center)))


def _trend_statistics(daily: list[dict[str, Any]], points: list[dict[str, Any]]) -> dict[str, Any]:
    daily = _longest_continuous_segment(daily)
    if len(daily) < 6 or (daily[-1]["day"] - daily[0]["day"]).days < 60:
        return {"status": "insufficient", "method": "Theil-Sen slope with Kendall trend evidence"}
    x = np.asarray([(item["day"] - daily[0]["day"]).days for item in daily], dtype=np.float64)
    y = np.asarray([item["index"] for item in daily], dtype=np.float64)
    slope = _theil_sen_slope(x, y) * 365.25
    rng = np.random.default_rng(20_240_611)
    bootstrap: list[float] = []
    for chosen in _block_indices(len(x), rng, 300):
        candidate = _theil_sen_slope(x[chosen], y[chosen])
        if math.isfinite(candidate):
            bootstrap.append(candidate * 365.25)
    slope_low, slope_high = (np.quantile(bootstrap, [0.025, 0.975]).tolist() if bootstrap else [slope, slope])
    # Kendall's independent-observation p-value is too optimistic when adjacent
    # residuals remain correlated. Inflate its null variance, conservatively.
    residual = y - slope / 365.25 * x
    inflation = _serial_inflation(residual)
    tau, p_value = _kendall_trend(y, variance_inflation=inflation)
    if slope > 0 and slope_low >= 0 and p_value < 0.05:
        direction = "increasing"
    elif slope < 0 and slope_high <= 0 and p_value < 0.05:
        direction = "decreasing"
    else:
        direction = "no_clear_trend"
    trend_by_date = {point["date"]: point.get("trend_index") for point in points if not point.get("is_break")}
    residuals = np.asarray([item["index"] - (trend_by_date.get(item["date"]) if trend_by_date.get(item["date"]) is not None else item["index"]) for item in daily], dtype=np.float64)
    variability = 1.4826 * float(np.median(np.abs(residuals - np.median(residuals))))
    stability = "stable" if variability < 0.35 else "variable" if variability > 0.8 else "typical"
    return {
        "status": "ready",
        "method": "Theil-Sen slope with moving-block interval and dependence-adjusted Kendall evidence",
        "annual_change": round(float(slope), 3),
        "annual_change_lower": round(float(slope_low), 3),
        "annual_change_upper": round(float(slope_high), 3),
        "kendall_tau": round(float(tau), 3),
        "p_value": round(float(p_value), 5),
        "direction": direction,
        "variability": round(variability, 3),
        "stability": stability,
        "observation_days": len(daily),
        "span_days": int(x[-1] - x[0]),
    }


def _theil_sen_slope(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) <= 200:
        left, right = np.triu_indices(len(x), 1)
    else:
        rng = np.random.default_rng(20240916)
        left = rng.integers(0, len(x), 20000)
        right = rng.integers(0, len(x), 20000)
    differences = x[right] - x[left]
    valid = differences != 0
    slopes = (y[right[valid]] - y[left[valid]]) / differences[valid]
    return float(np.median(slopes)) if len(slopes) else math.nan


def _kendall_trend(values: np.ndarray, variance_inflation: float = 1.0) -> tuple[float, float]:
    concordance = 0
    for left in range(len(values)):
        for right in range(left + 1, len(values)):
            difference = float(values[right] - values[left])
            if difference:
                concordance += 1 if difference > 0 else -1
    n = len(values)
    total_pairs = n * (n - 1) / 2
    tie_counts = np.unique(values, return_counts=True)[1]
    tied_pairs = float(np.sum(tie_counts * (tie_counts - 1) / 2))
    denominator = math.sqrt(total_pairs * max(total_pairs - tied_pairs, 0.0))
    tau = concordance / denominator if denominator else 0.0
    tie_adjustment = float(np.sum(tie_counts * (tie_counts - 1) * (2 * tie_counts + 5)))
    variance = (n * (n - 1) * (2 * n + 5) - tie_adjustment) / 18
    corrected_score = math.copysign(max(0, abs(concordance) - 1), concordance)
    z = corrected_score / math.sqrt(variance * variance_inflation) if variance > 0 else 0.0
    return tau, math.erfc(abs(z) / math.sqrt(2))


def _possible_change_point(daily: list[dict[str, Any]]) -> dict[str, Any] | None:
    daily = _longest_continuous_segment(daily)
    if len(daily) < 10 or (daily[-1]["day"] - daily[0]["day"]).days < 90:
        return None
    values = np.asarray([item["index"] for item in daily], dtype=np.float64)
    scale = 1.4826 * float(np.median(np.abs(values - np.median(values))))
    if scale <= 1e-8:
        return None
    candidates: list[tuple[float, int, float]] = []
    splits = np.unique(np.linspace(4, len(values) - 4, min(128, len(values) - 7), dtype=int))
    for split in splits:
        delta = float(np.median(values[split:]) - np.median(values[:split]))
        balance = math.sqrt(split * (len(values) - split) / len(values))
        candidates.append((abs(delta) / scale * balance, split, delta))
    best_score, split, delta = max(candidates)
    effect = abs(delta) / scale
    if abs(delta) < 0.35 or effect < 0.8:
        return None
    # The split was selected after searching many dates. Compare the maximum
    # statistic against block permutations of the entire scan, not a test that
    # pretends the selected date was specified in advance.
    rng = np.random.default_rng(20_240_612)
    block_size = max(2, math.ceil(len(values) ** (1 / 3)))
    blocks = [values[i:i + block_size] for i in range(0, len(values), block_size)]
    exceedances = 0
    for _ in range(199):
        shuffled = np.concatenate([blocks[i] for i in rng.permutation(len(blocks))])
        maximum = max(abs(float(np.median(shuffled[k:]) - np.median(shuffled[:k]))) / scale
                      * math.sqrt(k * (len(values) - k) / len(values))
                      for k in splits)
        exceedances += maximum >= best_score
    scan_p = (1 + exceedances) / 200
    if scan_p >= .05:
        return None
    left = values[:split]
    right = values[split:]
    rng = np.random.default_rng(20_240_612)
    differences = np.median(right[_block_indices(len(right), rng, 300)], axis=1) - np.median(left[_block_indices(len(left), rng, 300)], axis=1)
    low, high = np.quantile(differences, [0.025, 0.975])
    if low <= 0 <= high:
        return None
    return {
        "date": daily[split]["date"],
        "direction": "higher" if delta > 0 else "lower",
        "delta": round(delta, 2),
        "effect_size": round(effect, 2),
        "confidence": "moderate",
        "scan_adjusted_p_value": round(scan_p, 4),
        "label": "Possible sustained shift",
    }


def _longest_continuous_segment(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not daily:
        return []
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in daily:
        grouped[int(item.get("segment", 0))].append(item)
    return max(grouped.values(), key=lambda items: (len(items), (items[-1]["day"] - items[0]["day"]).days))


def _shape_insights(
    latest: dict[str, Any] | None,
    prior: dict[str, Any] | None,
    statistics: dict[str, Any],
    possible_shift: dict[str, Any] | None,
) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []
    if latest and prior:
        latest_components = latest.get("components", {})
        prior_components = prior.get("components", {})
        changes = {
            name: float(latest_components[name] - prior_components[name])
            for name in FEATURE_NAMES
            if name in latest_components and name in prior_components
        }
        if changes:
            feature, delta = max(changes.items(), key=lambda item: abs(item[1]))
            if abs(delta) >= 0.45:
                insights.append(_component_insight(feature, delta))
    if statistics.get("status") == "ready":
        if statistics["direction"] == "no_clear_trend":
            insights.append({"kind": "trend", "title": "No clear long-term direction", "detail": "The overall pattern is not consistently moving in one direction."})
        else:
            word = "fuller" if statistics["direction"] == "increasing" else "leaner"
            insights.append({"kind": "trend", "title": f"A gradual {word}-looking trend", "detail": "The change is consistent across the full history, not just the latest selfies."})
        if statistics["stability"] == "variable":
            insights.append({"kind": "variation", "title": "Selfies vary more than usual", "detail": "Short-term appearance changes are large, so longer windows are more trustworthy."})
    if possible_shift and len(insights) < 3:
        insights.append({"kind": "shift", "title": "A possible sustained shift", "detail": f"The pattern appears to settle at a new level around {possible_shift['date']}."})
    return insights[:3]


def _component_insight(feature: str, delta: float) -> dict[str, str]:
    increasing = delta > 0
    descriptions = {
        "face_width_height": ("Overall outline looks wider", "Overall outline looks narrower"),
        "jaw_cheek_ratio": ("Jaw looks broader relative to cheeks", "Jaw looks more tapered relative to cheeks"),
        "lower_face_width": ("Lower face looks broader", "Lower face looks narrower"),
        "lower_face_area": ("Lower-face outline looks fuller", "Lower-face outline looks leaner"),
        "outline_roundness": ("Outline looks rounder", "Outline looks more angular"),
        "chin_cheek_ratio": ("Chin area looks broader", "Chin area looks more tapered"),
        "temple_cheek_ratio": ("Temple-to-cheek balance has widened", "Cheeks look wider relative to temples"),
        "lower_face_height": ("Lower face looks longer", "Lower face looks shorter"),
        "jaw_angle": ("Jaw angle looks more open", "Jaw angle looks sharper"),
        "outline_asymmetry": ("Outline asymmetry is more visible", "Outline looks more balanced"),
    }
    title = descriptions[feature][0 if increasing else 1]
    return {"kind": "shape", "title": title, "detail": f"This is the clearest regional change over roughly 90 days ({FEATURE_LABELS[feature]})."}


def _comparison_observation(feature: str, delta: float) -> str:
    if abs(delta) < 0.25:
        return "Looks broadly similar"
    return _component_insight(feature, delta)["title"]


def _period_summary(observations: list[ScoredObservation], period: dict[str, str]) -> dict[str, Any]:
    start, end = _validated_period(period)
    items = [item for item in observations if start <= item.day <= end]
    if not items:
        raise ValueError(f"No eligible selfies between {start.isoformat()} and {end.isoformat()}")
    daily = _daily_observations(items)
    values = np.asarray([item["index"] for item in daily], dtype=np.float64)
    index = float(np.median(values))
    uncertainty = _median_interval_half_width(values)
    representative = min(items, key=lambda item: (abs(item.index - index), -item.quality))
    by_hash = {item.hash: item for item in items}
    contour = np.median(np.stack([by_hash[item["hash"]].contour for item in daily]), axis=0)
    components = np.median(np.stack([item["components"] for item in daily]), axis=0)
    profiles = sorted({item.capture_profile for item in items})
    median_quality = float(np.median([item["confidence_score"] for item in daily]))
    confidence = "low"
    if len(daily) >= 8 and uncertainty <= 0.45 and len(profiles) == 1 and median_quality >= .65:
        confidence = "high"
    elif len(daily) >= 4 and uncertainty <= .9 and median_quality >= .35:
        confidence = "medium"
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "index": index,
        "uncertainty": uncertainty,
        "count": len(items),
        "distinct_days": len(daily),
        "confidence": confidence,
        "capture_profiles": profiles,
        "components": components,
        "contour": contour,
        "representative": representative,
    }


def _public_period(period: dict[str, Any]) -> dict[str, Any]:
    representative: ScoredObservation = period["representative"]
    return {
        "start": period["start"],
        "end": period["end"],
        "index": round(float(period["index"]), 2),
        "uncertainty": round(float(period["uncertainty"]), 2),
        "count": period["count"],
        "distinct_days": period["distinct_days"],
        "confidence": period["confidence"],
        "capture_profiles": period["capture_profiles"],
        "contour": np.round(period["contour"], 5).tolist(),
        "representative": {
            "hash": representative.hash,
            "captured_at": representative.captured_at,
            "thumb_url": f"/api/photos/{representative.hash}/thumb",
            "image_url": f"/api/photos/{representative.hash}/image",
            "aligned_url": f"/api/photos/{representative.hash}/aligned",
        },
    }


def _measurement_rows(db: Database, project_id: int) -> list[Any]:
    return db.fetchall(
        """
        SELECT p.hash, p.captured_at, p.quality_score, p.yaw, p.pitch, p.roll,
               p.mouth_open_ratio, m.metrics_json, m.contour_json, m.capture_profile
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        JOIN face_shape_measurements m ON m.photo_hash = p.hash
        WHERE pp.project_id = ? AND p.skipped = 0 AND m.eligible = 1
              AND m.algorithm_version = ? AND m.contour_json IS NOT NULL
        ORDER BY p.captured_at
        """,
        (project_id, ALGORITHM_VERSION),
    )


def _quality_diagnostics(db: Database, project_id: int, profile: dict[str, Any]) -> dict[str, Any]:
    rows = db.fetchall(
        """
        SELECT m.eligible, m.reasons_json, m.capture_profile
        FROM face_shape_measurements m
        JOIN project_photos pp ON pp.photo_hash = m.photo_hash
        WHERE pp.project_id = ? AND m.algorithm_version = ?
        """,
        (project_id, ALGORITHM_VERSION),
    )
    reason_counts: dict[str, int] = defaultdict(int)
    profiles: dict[str, int] = defaultdict(int)
    for row in rows:
        profiles[str(row["capture_profile"])] += 1
        if not row["eligible"]:
            for reason in json.loads(row["reasons_json"] or "[]"):
                reason_counts[str(reason).split(":", 1)[0]] += 1
    corrected_profiles = set(profile["correction"].get("capture_offsets", {}))
    return {
        "exclusion_reasons": dict(sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))),
        "capture_profiles": dict(sorted(profiles.items(), key=lambda item: (-item[1], item[0]))),
        "uncorrected_capture_profiles": sorted(set(profiles) - corrected_profiles),
        "nuisance_correction": profile["correction"].get("method", "eligibility_filters_only"),
        "hair_handling": "forehead_excluded_from_index; no_beard_or_occlusion_model",
        "baseline_distinct_days": profile["baseline"].get("distinct_days"),
        "daily_deduplication": True,
    }


def _load_profile(db: Database, project_id: int) -> dict[str, Any] | None:
    row = db.fetchone("SELECT * FROM face_shape_profiles WHERE project_id = ?", (project_id,))
    if row is None or row["algorithm_version"] != ALGORITHM_VERSION:
        return None
    cached_trend = None
    if row["trend_json"]:
        try:
            cached_trend = json.loads(row["trend_json"])
        except json.JSONDecodeError:
            cached_trend = None
    return {
        "baseline": json.loads(row["baseline_json"]),
        "correction": json.loads(row["correction_json"]),
        "calibration": json.loads(row["calibration_json"]) if row["calibration_json"] else None,
        "source_revision": row["source_revision"],
        "computed_at": str(row["computed_at"]),
        "updated_at": str(row["updated_at"]),
        "cached_trend": cached_trend,
        "trend_cache_key": row["trend_cache_key"],
    }


def _row_features(row: Any) -> list[float]:
    metrics = json.loads(row["metrics_json"])
    return [float(metrics[name]) for name in FEATURE_NAMES]


def _row_nuisance(row: Any) -> list[float]:
    return [_number(row[name]) for name in NUISANCE_NAMES]


def _empty_trend(status: str, landmark_count: int, measurement_count: int, eligible_days: int = 0) -> dict[str, Any]:
    return {
        "status": status,
        "analysis_version": ALGORITHM_VERSION,
        "coverage": {
            "eligible_days": eligible_days,
            "landmark_photos": landmark_count,
            "measured_photos": measurement_count,
            "required": 6,
            "required_unit": "distinct_days",
        },
        "points": [],
        "events": [],
        "metric": {
            "unit": "personal_robust_sd",
            "baseline_value": 0,
            "interval_method": "approximate_robust_with_dependence_adjustment",
            "feature_scope": "lower_face_excluding_forehead",
            "higher_means": "fuller_like",
            "disclaimer": "A personal visual trend, not a weight measurement or medical assessment.",
        },
    }


def _capture_profile(row: Any) -> str:
    make = str(row["camera_make"] or "unknown").strip().lower().replace(" ", "-")
    model = str(row["camera_model"] or "unknown").strip().lower().replace(" ", "-")
    return f"{make}:{model}:{int(row['width'] or 0)}x{int(row['height'] or 0)}"


def _landmark_signature(path: Path | None) -> str:
    if path is None or not path.exists():
        return "missing"
    stat = path.stat()
    return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"


def _validated_period(period: dict[str, str]) -> tuple[date, date]:
    start = _parse_date(period["start"])
    end = _parse_date(period["end"])
    if start > end:
        raise ValueError("Period start must be on or before its end")
    return start, end


def _validate_anchor_samples(items: list[ScoredObservation], label: str) -> None:
    if len({item.day for item in items}) < 5:
        raise ValueError(f"The {label} period needs at least 5 distinct eligible dates")


def _validate_anchor_pose(a: list[ScoredObservation], b: list[ScoredObservation]) -> None:
    a = list({item.day: item for item in sorted(a, key=lambda item: item.quality)}.values())
    b = list({item.day: item for item in sorted(b, key=lambda item: item.quality)}.values())
    for name, threshold in (("yaw", 3.0), ("pitch", 3.0), ("roll", 3.0), ("mouth_open_ratio", 0.04)):
        left = float(np.median([getattr(item, name) for item in a]))
        right = float(np.median([getattr(item, name) for item in b]))
        if abs(left - right) > threshold:
            raise ValueError("Anchor periods differ too much in pose or expression")


def _point_near_days_before(points: list[dict[str, Any]], latest: dict[str, Any], days: int) -> dict[str, Any] | None:
    target = _parse_date(latest["date"]) - timedelta(days=days)
    candidates = [
        point
        for point in points
        if _parse_date(point["date"]) < _parse_date(latest["date"])
        and point.get("segment") == latest.get("segment")
        and abs((_parse_date(point["date"]) - target).days) <= 21
    ]
    return min(candidates, key=lambda point: abs((_parse_date(point["date"]) - target).days), default=None)


def _combined_confidence(a: str, b: str, same_profile: bool) -> str:
    rank = min({"low": 0, "medium": 1, "high": 2}.get(a, 0), {"low": 0, "medium": 1, "high": 2}.get(b, 0))
    if not same_profile:
        rank = min(rank, 0)
    return ("low", "medium", "high")[rank]


def _region_label(feature: str) -> str:
    return FEATURE_LABELS[feature]


def _observation_weight(row: Any) -> float:
    quality = max(0.15, min(1.0, _number(row["quality_score"], default=0.45)))
    pose_penalty = math.exp(
        -0.5
        * (
            (_number(row["yaw"]) / 8.0) ** 2
            + (_number(row["pitch"]) / 10.0) ** 2
            + (_number(row["roll"]) / 8.0) ** 2
            + (_number(row["mouth_open_ratio"]) / 0.10) ** 2
        )
    )
    return max(0.05, quality * pose_penalty)


def _vertex_angle(a: np.ndarray, vertex: np.ndarray, c: np.ndarray) -> float:
    left = np.asarray(a, dtype=np.float64) - np.asarray(vertex, dtype=np.float64)
    right = np.asarray(c, dtype=np.float64) - np.asarray(vertex, dtype=np.float64)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1e-12:
        return 0.0
    cosine = float(np.clip(np.dot(left, right) / denominator, -1.0, 1.0))
    return math.acos(cosine)


def _outline_asymmetry(aligned: np.ndarray, cheek_width: float) -> float:
    midline_x = float(np.median(aligned[[10, 152], 0]))
    pairs = ((338, 109), (297, 67), (332, 103), (284, 54), (251, 21), (389, 162), (356, 127), (454, 234), (323, 93), (361, 132), (288, 58), (397, 172), (365, 136), (379, 150), (378, 149), (400, 176), (377, 148))
    imbalances = [abs(abs(float(aligned[left, 0] - midline_x)) - abs(float(aligned[right, 0] - midline_x))) for left, right in pairs]
    return float(np.median(imbalances) / cheek_width)


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def _polygon_area(points: np.ndarray) -> float:
    return 0.5 * abs(float(np.dot(points[:, 0], np.roll(points[:, 1], 1)) - np.dot(points[:, 1], np.roll(points[:, 0], 1))))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])
