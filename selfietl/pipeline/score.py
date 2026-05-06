from __future__ import annotations

from dataclasses import dataclass

from selfietl.config import QualityConfig


@dataclass(frozen=True)
class QualityBreakdown:
    score: float
    confidence_component: float
    pose_component: float
    eye_component: float
    landmark_component: float


def compute_quality_score(
    *,
    confidence: float | None,
    yaw: float | None,
    pitch: float | None,
    roll: float | None,
    eye_open_ratio: float | None,
    landmark_zscore: float | None,
    config: QualityConfig,
) -> QualityBreakdown:
    confidence_component = clamp(confidence if confidence is not None else 0.0)
    pose_component = _pose_component(yaw, pitch, roll, config)
    eye_component = _eye_component(eye_open_ratio, config)
    landmark_component = _landmark_component(landmark_zscore, config)
    score = (
        0.32 * confidence_component
        + 0.30 * pose_component
        + 0.22 * eye_component
        + 0.16 * landmark_component
    )
    return QualityBreakdown(
        score=round(clamp(score), 4),
        confidence_component=confidence_component,
        pose_component=pose_component,
        eye_component=eye_component,
        landmark_component=landmark_component,
    )


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _axis_component(value: float | None, threshold: float) -> float:
    if value is None:
        return 0.5
    over = abs(value) / max(threshold, 1e-6)
    if over <= 1:
        return 1.0
    return clamp(1.0 - (over - 1.0) / 1.5)


def _pose_component(yaw: float | None, pitch: float | None, roll: float | None, config: QualityConfig) -> float:
    return min(
        _axis_component(yaw, config.max_yaw_degrees),
        _axis_component(pitch, config.max_pitch_degrees),
        _axis_component(roll, config.max_roll_degrees),
    )


def _eye_component(eye_open_ratio: float | None, config: QualityConfig) -> float:
    if eye_open_ratio is None:
        return 0.5
    return clamp(eye_open_ratio / max(config.min_eye_open_ratio, 1e-6))


def _landmark_component(zscore: float | None, config: QualityConfig) -> float:
    if zscore is None:
        return 1.0
    threshold = max(config.landmark_zscore_threshold, 1e-6)
    if zscore <= threshold:
        return 1.0
    return clamp(1.0 - (zscore - threshold) / threshold)
