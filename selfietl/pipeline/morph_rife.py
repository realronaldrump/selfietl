from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def rife_available(binary: str = "rife-ncnn-vulkan") -> bool:
    return shutil.which(binary) is not None


def interpolate_pair(
    image_a: str | Path,
    image_b: str | Path,
    output_dir: str | Path,
    intermediate_frames: int,
    binary: str = "rife-ncnn-vulkan",
) -> list[Path]:
    if not rife_available(binary):
        raise RuntimeError("rife-ncnn-vulkan is not available on PATH")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        binary,
        "-0",
        str(image_a),
        "-1",
        str(image_b),
        "-o",
        str(output_dir),
        "-n",
        str(intermediate_frames),
    ]
    subprocess.run(command, check=True)
    return sorted(output_dir.glob("*.png")) + sorted(output_dir.glob("*.jpg"))
