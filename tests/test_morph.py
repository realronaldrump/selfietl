import pytest
import numpy as np
from PIL import Image

from selfietl.pipeline.morph_delaunay import morph_images


def test_morph_can_be_cancelled_inside_frame_work():
    image_a = Image.new("RGB", (24, 24), (20, 40, 80))
    image_b = Image.new("RGB", (24, 24), (80, 40, 20))
    landmarks = np.array([[6, 6], [18, 6], [12, 18]], dtype=float)

    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        list(morph_images(image_a, image_b, landmarks, landmarks, 1, cancel_check=cancel))


def test_morph_images_keeps_frame_size():
    image_a = Image.new("RGB", (48, 48), (20, 40, 80))
    image_b = Image.new("RGB", (48, 48), (80, 40, 20))
    landmarks_a = np.array([[10, 10], [36, 10], [24, 36], [16, 24], [32, 24]], dtype=float)
    landmarks_b = np.array([[12, 11], [34, 9], [25, 35], [17, 23], [31, 25]], dtype=float)

    frames = list(morph_images(image_a, image_b, landmarks_a, landmarks_b, 2))

    assert len(frames) == 2
    assert all(frame.size == (48, 48) for frame in frames)
