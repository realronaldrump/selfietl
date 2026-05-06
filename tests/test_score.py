from selfietl.config import QualityConfig
from selfietl.pipeline.score import compute_quality_score


def test_quality_score_rewards_confident_frontal_open_eye_photo():
    score = compute_quality_score(
        confidence=0.98,
        yaw=1,
        pitch=-2,
        roll=0.5,
        eye_open_ratio=0.28,
        landmark_zscore=0.2,
        config=QualityConfig(),
    )

    assert score.score > 0.9


def test_quality_score_penalizes_pose_and_closed_eyes():
    score = compute_quality_score(
        confidence=0.9,
        yaw=44,
        pitch=28,
        roll=31,
        eye_open_ratio=0.05,
        landmark_zscore=4.5,
        config=QualityConfig(),
    )

    assert score.score < 0.6
