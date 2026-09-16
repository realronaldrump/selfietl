"""Controlled perturbations: geometry must stay stable, evidence must stay honest."""
import json
from datetime import date, timedelta

import numpy as np
import pytest

from selfietl.pipeline import face_shape as shape
from test_face_shape import create_project_with_landmarks, synthetic_landmarks


def test_pixel_geometry_is_invariant_to_canvas_aspect_ratio():
    pixels = synthetic_landmarks() * np.array([600, 600, 1])
    results = []
    for size in [(1000, 1000), (1000, 1600), (1600, 1000)]:
        normalized = pixels / np.array([*size, 1])
        results.append(shape.extract_features(normalized, image_size=size)[0])
    for result in results[1:]:
        assert result == pytest.approx(results[0])


def test_forehead_displacement_does_not_change_fullness_features():
    points = synthetic_landmarks()
    original, _ = shape.extract_features(points)
    upper = list(set(shape.FACE_OVAL) - set(shape.LOWER_FACE))
    points[upper, 1] -= 0.10
    changed, _ = shape.extract_features(points)
    for name in shape.FULLNESS_FEATURE_NAMES:
        assert changed[name] == pytest.approx(original[name]), name


def test_nonfinite_quality_is_not_eligible(tmp_path):
    db, project = create_project_with_landmarks(tmp_path, [1.0] * 6)
    db.execute("UPDATE photos SET yaw = ? WHERE hash = 'shape-0'", (float('inf'),))
    assert not shape.measure_photo(db, 'shape-0')['eligible']
    db.execute("UPDATE photos SET yaw = 0, quality_score = NULL WHERE hash = 'shape-0'")
    assert not shape.measure_photo(db, 'shape-0')['eligible']


def test_pose_correlated_with_time_does_not_erase_shape_change(tmp_path):
    db, project = create_project_with_landmarks(tmp_path, np.linspace(.9, 1.1, 20).tolist())
    for i in range(20):
        db.execute('UPDATE photos SET yaw = ? WHERE hash = ?', (-5 + i * .5, f'shape-{i}'))
    shape.recompute_project(db, project)
    profile = shape._load_profile(db, project)
    assert np.max(np.abs(profile['correction']['slopes'])) == 0
    scores = shape._score_rows(shape._measurement_rows(db, project), profile)
    assert scores[-1].index - scores[0].index > .5


def test_burst_does_not_reweight_baseline(tmp_path):
    db, project = create_project_with_landmarks(tmp_path, [.9] * 6 + [1.1] * 6)
    for row in db.fetchall('SELECT hash FROM photos'):
        shape.measure_photo(db, row['hash'])
    rows = shape._measurement_rows(db, project)
    original, _ = shape._build_profile(rows)
    repeated, _ = shape._build_profile(rows + [rows[0]] * 100)
    assert repeated['center'] == pytest.approx(original['center'])
    assert repeated['scale'] == pytest.approx(original['scale'])
    assert repeated['default_weights'] == pytest.approx(original['default_weights'])


def test_camera_switch_cannot_support_fullness_conclusion(tmp_path):
    db, project = create_project_with_landmarks(tmp_path, [.9] * 6 + [1.1] * 6)
    db.execute("UPDATE photos SET camera_model = 'other' WHERE captured_at >= '2024-03-21'")
    shape.recompute_project(db, project)
    result = shape.compare_periods(db, project, {'start': '2024-01-01', 'end': '2024-03-20'}, {'start': '2024-03-21', 'end': '2024-07-01'})
    assert result['conclusion'] == 'no_clear_change'
    assert result['confidence'] == 'low'
    assert 'incompatible_capture_profiles' in result['limitations']


def test_short_history_does_not_invent_90_day_change(tmp_path):
    db, project = create_project_with_landmarks(tmp_path, [.9, .95, 1, 1.05, 1.1, 1.15])
    for i in range(6):
        db.execute('UPDATE photos SET captured_at = ? WHERE hash = ?', (f'2024-01-{i+1:02} 10:00:00', f'shape-{i}'))
    shape.recompute_project(db, project)
    assert shape.get_project_trend(db, project)['summary']['change_90d'] is None


def test_low_variance_baseline_has_measurement_floor(tmp_path):
    db, project = create_project_with_landmarks(tmp_path, [1 + i * 1e-7 for i in range(12)])
    shape.recompute_project(db, project)
    profile = shape._load_profile(db, project)
    assert min(profile['baseline']['scale']) >= .01


def test_revision_tracks_quality_and_landmark_file_changes(tmp_path):
    db, project = create_project_with_landmarks(tmp_path, [1.] * 6)
    shape.recompute_project(db, project)
    revision = shape.project_source_revision(db, project)
    db.execute("UPDATE photos SET quality_score = .5 WHERE hash = 'shape-0'")
    assert shape.project_source_revision(db, project) != revision
    revision = shape.project_source_revision(db, project)
    path = db.fetchone("SELECT landmarks_path FROM photos WHERE hash = 'shape-0'")[0]
    np.savez_compressed(path, landmarks=synthetic_landmarks(1.1))
    assert shape.project_source_revision(db, project) != revision


def test_local_interval_accounts_for_endpoint_leverage():
    rng = np.random.default_rng(12)
    start = date(2024, 1, 1)
    local = [{'day': start + timedelta(days=i), 'index': .01*i + rng.normal(0, .3), 'confidence_score': .9} for i in range(25)]
    _, center = shape._local_robust_estimate(local, start + timedelta(days=12))
    _, edge = shape._local_robust_estimate(local, start)
    assert edge > center * 1.2


def test_real_within_day_pose_effect_is_learned_without_temporal_confounding():
    rows = []
    for day in range(12):
        for yaw in (0., 1. + day % 4):
            rows.append({
                'captured_at': f'2024-01-{day+1:02} 10:00:00',
                'capture_profile': 'camera', 'yaw': yaw, 'pitch': 0., 'roll': 0., 'mouth_open_ratio': 0.,
                'metrics_json': json.dumps({name: 1 + .03 * day + .02 * yaw for name in shape.FEATURE_NAMES}),
            })
    baseline, correction = shape._build_profile(rows)
    assert correction['method'] == 'within_day'
    assert np.asarray(correction['slopes'])[:, 0] == pytest.approx(.02, abs=.002)
    assert baseline['distinct_days'] == 12


def test_camera_offsets_require_matched_dates():
    rows = [{'captured_at': f'2024-01-{day:02}'} for day in [1, 5, 9, 3, 6, 8]]
    matrix = np.tile(np.arange(6.)[:, None], (1, len(shape.FEATURE_NAMES)))
    offsets = shape._overlapping_capture_offsets(rows, matrix, ['a'] * 3 + ['b'] * 3)
    assert set(offsets) == {'a'}
    rows[3:] = rows[:3]
    matrix[3:] = matrix[:3] + .2
    offsets = shape._overlapping_capture_offsets(rows, matrix, ['a'] * 3 + ['b'] * 3)
    assert offsets['b'] == pytest.approx([.2] * len(shape.FEATURE_NAMES))


def test_archive_dimensions_override_catalog_dimensions(tmp_path):
    db, _ = create_project_with_landmarks(tmp_path, [1.] * 6)
    path = db.fetchone("SELECT landmarks_path FROM photos WHERE hash='shape-0'")[0]
    points = synthetic_landmarks()
    np.savez_compressed(path, landmarks=points, image_size=[600, 600])
    result = shape.measure_photo(db, 'shape-0')
    assert result['eligible']
    assert result['metrics'] == pytest.approx(shape.extract_features(points)[0])


def test_old_profiles_are_rebuilt_and_not_mixed_with_new_features(tmp_path):
    db, project = create_project_with_landmarks(tmp_path, [.9] * 6 + [1.1] * 6)
    shape.recompute_project(db, project)
    db.execute("UPDATE face_shape_profiles SET algorithm_version='face-shape-v2'")
    db.execute("UPDATE face_shape_measurements SET algorithm_version='face-shape-v2'")
    assert shape.get_project_trend(db, project)['status'] == 'not_ready'
    shape.recompute_project(db, project)
    assert shape.get_project_trend(db, project)['analysis_version'] == 'face-shape-v3'


def test_six_photos_on_one_day_do_not_establish_baseline(tmp_path):
    db, project = create_project_with_landmarks(tmp_path, [1.] * 6)
    db.execute("UPDATE photos SET captured_at='2024-01-01 10:00:00'")
    assert shape.recompute_project(db, project)['status'] == 'insufficient'


def test_stale_inputs_cannot_be_used_for_comparison(tmp_path):
    db, project = create_project_with_landmarks(tmp_path, [.9] * 6 + [1.1] * 6)
    shape.recompute_project(db, project)
    db.execute("UPDATE photos SET quality_score=.6 WHERE hash='shape-0'")
    with pytest.raises(ValueError, match='Recompute'):
        shape.compare_periods(db, project, {'start': '2024-01-01', 'end': '2024-03-20'}, {'start': '2024-03-21', 'end': '2024-07-01'})


def test_even_weight_median_does_not_systematically_choose_lower_value():
    assert shape._weighted_median(np.array([0., 2.]), np.ones(2)) == 1.


def test_single_outlier_does_not_drive_sustained_trend():
    start = date(2024, 1, 1)
    local = [{'day': start + timedelta(days=i), 'index': .01 * i, 'confidence_score': .9} for i in range(31)]
    local[15]['index'] = 10.
    estimate, uncertainty = shape._local_robust_estimate(local, start + timedelta(days=15))
    assert estimate == pytest.approx(.15, abs=.08)
    assert np.isfinite(uncertainty)


def test_long_clear_trend_remains_detectable():
    start = date(2024, 1, 1)
    rng = np.random.default_rng(1)
    daily = [{'day': start + timedelta(days=7*i), 'date': (start + timedelta(days=7*i)).isoformat(),
              'index': .04*i + rng.normal(0, .02), 'segment': 0} for i in range(50)]
    stats = shape._trend_statistics(daily, [])
    assert stats['direction'] == 'increasing'
    assert stats['annual_change_lower'] > 0


def test_dependence_widens_uncertainty():
    independent = np.array([1., -1., 1., -1., 1., -1., 1., -1.])
    dependent = np.repeat([1., -1.], 8)
    assert shape._serial_inflation(dependent) > shape._serial_inflation(independent)
