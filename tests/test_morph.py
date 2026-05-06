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
