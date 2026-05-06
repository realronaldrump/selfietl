import math

import numpy as np

from selfietl.pipeline.canonical import apply_transform, similarity_transform


def test_similarity_transform_recovers_known_transform():
    rng = np.random.default_rng(42)
    source = rng.normal(size=(80, 2)) * 120
    angle = math.radians(18)
    scale = 1.37
    rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    translation = np.array([240.0, -91.0])
    target = source @ (scale * rotation).T + translation

    matrix = similarity_transform(source, target)
    recovered = apply_transform(source, matrix)

    assert np.max(np.abs(recovered - target)) < 1e-6


def test_similarity_transform_handles_synthetic_photo_set_within_one_pixel():
    rng = np.random.default_rng(7)
    canonical = rng.uniform([250, 180], [760, 900], size=(468, 2))
    for idx in range(50):
        angle = math.radians(-12 + idx * 0.5)
        scale = 0.88 + idx * 0.006
        rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        translation = np.array([idx * 3.0, -idx * 1.7])
        observed = canonical @ (scale * rotation).T + translation
        matrix = similarity_transform(observed, canonical)
        aligned = apply_transform(observed, matrix)
        rms = np.sqrt(np.mean(np.sum((aligned - canonical) ** 2, axis=1)))
        assert rms < 1.0
