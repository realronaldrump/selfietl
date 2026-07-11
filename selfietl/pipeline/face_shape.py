from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np

from selfietl.db import Database


ALGORITHM_VERSION = "face-shape-v1"
FEATURE_NAMES = (
    "face_width_height",
    "cheek_jaw_ratio",
    "lower_face_width",
    "lower_face_area",
    "perimeter_area_ratio",
)
FEATURE_DIRECTIONS = np.array([1.0, -1.0, 1.0, 1.0, -1.0], dtype=np.float64)
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
            metrics, contour_array = extract_features(landmarks)
            contour = np.round(contour_array, 6).tolist()
        except Exception as exc:
            reasons.append(f"invalid_landmarks:{exc.__class__.__name__}")

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


def extract_features(landmarks: np.ndarray) -> tuple[dict[str, float], np.ndarray]:
    pts = np.asarray(landmarks, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] < 2 or len(pts) <= max(FACE_OVAL):
        raise ValueError("MediaPipe face landmarks are incomplete")
    pts = pts[:, :2]

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

    face_height = float(oval[:, 1].max() - oval[:, 1].min())
    cheek_width = _distance(aligned[234], aligned[454])
    jaw_width = _distance(aligned[172], aligned[397])
    lower_width = _distance(aligned[58], aligned[288])
    oval_area = _polygon_area(oval)
    lower_area = _polygon_area(lower)
    perimeter = float(np.sum(np.linalg.norm(oval - np.roll(oval, 1, axis=0), axis=1)))
    if min(face_height, cheek_width, jaw_width, lower_width, oval_area, lower_area, perimeter) <= 1e-8:
        raise ValueError("Face contour is degenerate")

    metrics = {
        "face_width_height": cheek_width / face_height,
        "cheek_jaw_ratio": cheek_width / jaw_width,
        "lower_face_width": lower_width,
        "lower_face_area": lower_area,
        "perimeter_area_ratio": perimeter / oval_area,
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
    if len(active) < 6:
        return {"status": "insufficient", "measured": len(rows), "eligible": len(active), "required": 6}

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
            updated_at = excluded.updated_at
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
        status = "insufficient" if landmark_count < 6 and measurement_count >= landmark_count else "not_ready"
        return _empty_trend(status, landmark_count, measurement_count)

    current_revision = project_source_revision(db, project_id)
    stale = profile["source_revision"] != current_revision or measurement_count < landmark_count
    observations = _score_rows(_measurement_rows(db, project_id), profile)
    if len(observations) < 3:
        return _empty_trend("insufficient", landmark_count, measurement_count)

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
    baseline = profile["baseline"]
    return {
        "status": "stale" if stale else "ready",
        "analysis_version": ALGORITHM_VERSION,
        "analysis_revision": current_revision,
        "generated_at": profile["updated_at"],
        "metric": {
            "unit": "personal_robust_sd",
            "baseline_value": 0,
            "higher_means": "fuller_like",
            "disclaimer": "A personal visual trend, not a weight measurement or medical assessment.",
        },
        "baseline": {
            "start": baseline["start"],
            "end": baseline["end"],
            "observation_count": baseline["observation_count"],
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
        "coverage": {
            "eligible_photos": len(observations),
            "eligible_days": len(daily),
            "excluded_photos": excluded,
            "first_date": daily[0]["date"] if daily else None,
            "last_date": daily[-1]["date"] if daily else None,
        },
        "points": points,
        "events": events,
    }


def compare_periods(
    db: Database,
    project_id: int,
    a: dict[str, str],
    b: dict[str, str],
) -> dict[str, Any]:
    profile = _load_profile(db, project_id)
    if profile is None:
        raise ValueError("Face-shape analysis is not ready")
    observations = _score_rows(_measurement_rows(db, project_id), profile)
    period_a = _period_summary(observations, a)
    period_b = _period_summary(observations, b)
    delta = float(period_b["index"] - period_a["index"])
    threshold = max(0.2, period_a["uncertainty"] + period_b["uncertainty"])
    same_profile = bool(set(period_a["capture_profiles"]) & set(period_b["capture_profiles"]))
    if abs(delta) <= threshold:
        conclusion = "no_clear_change"
    else:
        conclusion = "fuller" if delta > 0 else "leaner"
    confidence = _combined_confidence(period_a["confidence"], period_b["confidence"], same_profile)
    contributions = []
    for index, name in enumerate(FEATURE_NAMES):
        contributions.append(
            {
                "region": _region_label(name),
                "feature": name,
                "delta": round(float(period_b["components"][index] - period_a["components"][index]), 2),
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
        light_components = np.median(np.stack([item.components for item in light]), axis=0)
        full_components = np.median(np.stack([item.components for item in full]), axis=0)
        difference = full_components - light_components
        orientation = 1.0 if float(np.median(difference)) >= 0 else -1.0
        strengths = np.abs(difference)
        if float(np.median(strengths)) < 0.2:
            raise ValueError("The selected periods are not separated enough for reliable calibration")
        personalized = strengths / max(float(strengths.sum()), 1e-9)
        weights = 0.5 * personalized + 0.5 * (np.ones(len(FEATURE_NAMES)) / len(FEATURE_NAMES))
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
        "UPDATE face_shape_profiles SET calibration_json = ?, updated_at = ? WHERE project_id = ?",
        (json.dumps(calibration, separators=(",", ":")) if calibration else None, now, project_id),
    )
    return calibration or {"status": "automatic"}


def project_source_revision(db: Database, project_id: int) -> str:
    rows = db.fetchall(
        """
        SELECT p.hash, p.captured_at, p.skipped, p.landmarks_path,
               COALESCE(m.algorithm_version, ''), COALESCE(m.source_signature, '')
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
        digest.update(b"\n")
    return digest.hexdigest()


def _build_profile(rows: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    matrix = np.array([_row_features(row) for row in rows], dtype=np.float64)
    nuisance = np.array([_row_nuisance(row) for row in rows], dtype=np.float64)
    nuisance_center = np.median(nuisance, axis=0)
    centered_nuisance = nuisance - nuisance_center
    slopes = np.zeros((len(FEATURE_NAMES), len(NUISANCE_NAMES)), dtype=np.float64)
    corrected = matrix.copy()
    for feature_index in range(len(FEATURE_NAMES)):
        coefficients = _robust_slopes(centered_nuisance, matrix[:, feature_index])
        slopes[feature_index] = coefficients
        corrected[:, feature_index] -= centered_nuisance @ coefficients
    capture_profiles = [str(row["capture_profile"]) for row in rows]
    capture_offsets = _overlapping_capture_offsets(rows, corrected, capture_profiles)
    for row_index, capture_profile in enumerate(capture_profiles):
        corrected[row_index] -= np.asarray(capture_offsets.get(capture_profile, [0.0] * len(FEATURE_NAMES)))
    center = np.median(corrected, axis=0)
    scale = 1.4826 * np.median(np.abs(corrected - center), axis=0)
    fallback = np.std(corrected, axis=0)
    scale = np.where(scale > 1e-8, scale, np.where(fallback > 1e-8, fallback, 1.0))
    dates = sorted(str(row["captured_at"])[:10] for row in rows)
    baseline = {
        "feature_names": list(FEATURE_NAMES),
        "center": np.round(center, 10).tolist(),
        "scale": np.round(scale, 10).tolist(),
        "start": dates[0],
        "end": dates[-1],
        "observation_count": len(rows),
    }
    correction = {
        "nuisance_names": list(NUISANCE_NAMES),
        "nuisance_center": np.round(nuisance_center, 10).tolist(),
        "slopes": np.round(slopes, 10).tolist(),
        "capture_offsets": capture_offsets,
    }
    return baseline, correction


def _robust_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    weights = np.ones(len(y), dtype=np.float64)
    coefficients = np.zeros(design.shape[1], dtype=np.float64)
    for _ in range(8):
        weighted_design = design * np.sqrt(weights)[:, None]
        weighted_y = y * np.sqrt(weights)
        coefficients, *_ = np.linalg.lstsq(weighted_design, weighted_y, rcond=None)
        residual = y - design @ coefficients
        scale = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
        if scale <= 1e-10:
            break
        normalized = np.abs(residual) / (1.345 * scale)
        weights = np.where(normalized <= 1, 1.0, 1.0 / np.maximum(normalized, 1e-9))
    return coefficients[1:]


def _overlapping_capture_offsets(rows: list[Any], corrected: np.ndarray, profiles: list[str]) -> dict[str, list[float]]:
    counts = {profile: profiles.count(profile) for profile in set(profiles)}
    reference = max(counts, key=counts.get)
    reference_indices = [index for index, profile in enumerate(profiles) if profile == reference]
    reference_dates = [_parse_date(str(rows[index]["captured_at"])[:10]) for index in reference_indices]
    offsets: dict[str, list[float]] = {reference: [0.0] * len(FEATURE_NAMES)}
    for profile in counts:
        if profile == reference:
            continue
        indices = [index for index, value in enumerate(profiles) if value == profile]
        dates = [_parse_date(str(rows[index]["captured_at"])[:10]) for index in indices]
        overlap_start = max(min(reference_dates), min(dates))
        overlap_end = min(max(reference_dates), max(dates))
        ref_overlap = [index for index in reference_indices if overlap_start <= _parse_date(str(rows[index]["captured_at"])[:10]) <= overlap_end]
        profile_overlap = [index for index in indices if overlap_start <= _parse_date(str(rows[index]["captured_at"])[:10]) <= overlap_end]
        if len(ref_overlap) >= 3 and len(profile_overlap) >= 3:
            offset = np.median(corrected[profile_overlap], axis=0) - np.median(corrected[ref_overlap], axis=0)
            offsets[profile] = np.round(offset, 10).tolist()
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
        (calibration or {}).get("weights", np.ones(len(FEATURE_NAMES)) / len(FEATURE_NAMES)),
        dtype=np.float64,
    )
    orientation = float((calibration or {}).get("orientation", 1.0))
    result: list[ScoredObservation] = []
    for row in rows:
        raw = np.asarray(_row_features(row), dtype=np.float64)
        nuisance = np.asarray(_row_nuisance(row), dtype=np.float64)
        corrected = raw - slopes @ (nuisance - nuisance_center)
        corrected -= np.asarray(capture_offsets.get(row["capture_profile"], [0.0] * len(FEATURE_NAMES)), dtype=np.float64)
        components = FEATURE_DIRECTIONS * ((corrected - center) / scale)
        index = orientation * float(np.dot(components, weights))
        contour = np.asarray(json.loads(row["contour_json"]), dtype=np.float64)
        result.append(
            ScoredObservation(
                hash=row["hash"],
                captured_at=str(row["captured_at"]),
                day=_parse_date(str(row["captured_at"])[:10]),
                index=index,
                components=orientation * components,
                contour=contour,
                capture_profile=row["capture_profile"],
                quality=_number(row["quality_score"], default=0.0),
                yaw=_number(row["yaw"]),
                pitch=_number(row["pitch"]),
                roll=_number(row["roll"]),
                mouth_open_ratio=_number(row["mouth_open_ratio"]),
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
        index = float(np.median([item.index for item in items]))
        representative = min(items, key=lambda item: (abs(item.index - index), -item.quality))
        agreement = float(np.median(np.std(np.stack([item.components for item in items]), axis=1)))
        confidence_score = max(0.15, min(1.0, (0.55 + representative.quality * 0.45) * (1.0 - min(agreement, 2.5) / 4)))
        result.append(
            {
                "date": day.isoformat(),
                "day": day,
                "index": index,
                "hash": representative.hash,
                "quality": representative.quality,
                "confidence_score": confidence_score,
                "capture_profile": representative.capture_profile,
            }
        )
    return result


def _trend_points(daily: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    points: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
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
        local = [candidate for candidate in daily if abs((candidate["day"] - item["day"]).days) <= 22]
        if len(local) < 3:
            local = [candidate for candidate in daily if abs((candidate["day"] - item["day"]).days) <= 45]
        trend = uncertainty = lower = upper = None
        confidence = "low"
        window_start = window_end = item["date"]
        if len(local) >= 3:
            values = np.asarray([candidate["index"] for candidate in local], dtype=np.float64)
            trend = float(np.median(values))
            spread = 1.4826 * float(np.median(np.abs(values - trend)))
            uncertainty = max(0.08, spread / math.sqrt(len(values)))
            lower = trend - uncertainty
            upper = trend + uncertainty
            profile_count = len({candidate["capture_profile"] for candidate in local})
            if len(local) >= 8 and uncertainty <= 0.35 and profile_count == 1:
                confidence = "high"
            elif len(local) >= 4 and uncertainty <= 0.75:
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
            }
        )
        previous = item
    return points, events


def _period_summary(observations: list[ScoredObservation], period: dict[str, str]) -> dict[str, Any]:
    start, end = _validated_period(period)
    items = [item for item in observations if start <= item.day <= end]
    if not items:
        raise ValueError(f"No eligible selfies between {start.isoformat()} and {end.isoformat()}")
    values = np.asarray([item.index for item in items], dtype=np.float64)
    index = float(np.median(values))
    spread = 1.4826 * float(np.median(np.abs(values - index)))
    uncertainty = max(0.08, spread / math.sqrt(len(values)))
    representative = min(items, key=lambda item: (abs(item.index - index), -item.quality))
    contour = np.median(np.stack([item.contour for item in items]), axis=0)
    components = np.median(np.stack([item.components for item in items]), axis=0)
    profiles = sorted({item.capture_profile for item in items})
    confidence = "high" if len(items) >= 8 and uncertainty <= 0.35 and len(profiles) == 1 else "medium" if len(items) >= 4 and uncertainty <= 0.75 else "low"
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "index": index,
        "uncertainty": uncertainty,
        "count": len(items),
        "distinct_days": len({item.day for item in items}),
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


def _load_profile(db: Database, project_id: int) -> dict[str, Any] | None:
    row = db.fetchone("SELECT * FROM face_shape_profiles WHERE project_id = ?", (project_id,))
    if row is None or row["algorithm_version"] != ALGORITHM_VERSION:
        return None
    return {
        "baseline": json.loads(row["baseline_json"]),
        "correction": json.loads(row["correction_json"]),
        "calibration": json.loads(row["calibration_json"]) if row["calibration_json"] else None,
        "source_revision": row["source_revision"],
        "computed_at": str(row["computed_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _row_features(row: Any) -> list[float]:
    metrics = json.loads(row["metrics_json"])
    return [float(metrics[name]) for name in FEATURE_NAMES]


def _row_nuisance(row: Any) -> list[float]:
    return [_number(row[name]) for name in NUISANCE_NAMES]


def _empty_trend(status: str, landmark_count: int, measurement_count: int) -> dict[str, Any]:
    return {
        "status": status,
        "analysis_version": ALGORITHM_VERSION,
        "coverage": {
            "landmark_photos": landmark_count,
            "measured_photos": measurement_count,
            "required": 6,
        },
        "points": [],
        "events": [],
        "metric": {
            "unit": "personal_robust_sd",
            "baseline_value": 0,
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
    if len(items) < 5 or len({item.day for item in items}) < 3:
        raise ValueError(f"The {label} period needs at least 5 eligible selfies across 3 dates")


def _validate_anchor_pose(a: list[ScoredObservation], b: list[ScoredObservation]) -> None:
    for name, threshold in (("yaw", 3.0), ("pitch", 3.0), ("roll", 3.0), ("mouth_open_ratio", 0.04)):
        left = float(np.median([getattr(item, name) for item in a]))
        right = float(np.median([getattr(item, name) for item in b]))
        if abs(left - right) > threshold:
            raise ValueError("Anchor periods differ too much in pose or expression")


def _point_near_days_before(points: list[dict[str, Any]], latest: dict[str, Any], days: int) -> dict[str, Any] | None:
    target = _parse_date(latest["date"]) - timedelta(days=days)
    candidates = [point for point in points if _parse_date(point["date"]) < _parse_date(latest["date"])]
    return min(candidates, key=lambda point: abs((_parse_date(point["date"]) - target).days), default=None)


def _combined_confidence(a: str, b: str, same_profile: bool) -> str:
    rank = min({"low": 0, "medium": 1, "high": 2}.get(a, 0), {"low": 0, "medium": 1, "high": 2}.get(b, 0))
    if not same_profile:
        rank = min(rank, 0)
    return ("low", "medium", "high")[rank]


def _region_label(feature: str) -> str:
    labels = {
        "face_width_height": "overall face outline",
        "cheek_jaw_ratio": "cheek-to-jaw balance",
        "lower_face_width": "lower cheek width",
        "lower_face_area": "jaw and chin area",
        "perimeter_area_ratio": "outline roundness",
    }
    return labels[feature]


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
