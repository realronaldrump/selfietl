from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def normalize_to_reference(source_path: str | Path, reference_path: str | Path, output_path: str | Path) -> Path:
    source = Image.open(source_path).convert("RGB")
    reference = Image.open(reference_path).convert("RGB")
    try:
        matched = _skimage_lab_match(source, reference)
    except Exception:
        matched = _mean_std_match(source, reference)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    matched.save(output_path, "JPEG", quality=95, optimize=True)
    return output_path


def _skimage_lab_match(source: Image.Image, reference: Image.Image) -> Image.Image:
    from skimage import color, exposure

    src = np.asarray(source, dtype=np.float32) / 255.0
    ref = np.asarray(reference.resize(source.size), dtype=np.float32) / 255.0
    src_lab = color.rgb2lab(src)
    ref_lab = color.rgb2lab(ref)
    matched_lab = exposure.match_histograms(src_lab, ref_lab, channel_axis=-1)
    rgb = np.clip(color.lab2rgb(matched_lab) * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb)


def _mean_std_match(source: Image.Image, reference: Image.Image) -> Image.Image:
    src = np.asarray(source, dtype=np.float32)
    ref = np.asarray(reference.resize(source.size), dtype=np.float32)
    src_mean = src.reshape(-1, 3).mean(axis=0)
    src_std = src.reshape(-1, 3).std(axis=0) + 1e-6
    ref_mean = ref.reshape(-1, 3).mean(axis=0)
    ref_std = ref.reshape(-1, 3).std(axis=0) + 1e-6
    out = (src - src_mean) / src_std * ref_std + ref_mean
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
