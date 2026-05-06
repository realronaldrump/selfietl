from __future__ import annotations

import hashlib
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageCms, ImageOps

try:
    import piexif
except Exception:  # pragma: no cover - optional at import time
    piexif = None


SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
}


def register_heif() -> None:
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except Exception:
        pass


def is_supported_image(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def sha1_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def open_oriented_image(path: str | Path) -> Image.Image:
    register_heif()
    image = Image.open(path)
    image = ImageOps.exif_transpose(image)
    image = _convert_to_srgb(image)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.getchannel("A"))
        image = background
    return image


def _convert_to_srgb(image: Image.Image) -> Image.Image:
    icc = image.info.get("icc_profile")
    if not icc:
        return image
    try:
        source = ImageCms.ImageCmsProfile(bytes(icc))
        target = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(image, source, target, outputMode="RGB")
    except Exception:
        return image.convert("RGB")


def image_dimensions(path: str | Path) -> tuple[int, int]:
    with open_oriented_image(path) as image:
        return image.size


def exif_metadata(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    warnings: list[str] = []
    captured_at: datetime | None = None
    filename_captured_at = parse_filename_datetime(path.name)
    make = None
    model = None
    try:
        register_heif()
        with Image.open(path) as image:
            exif = image.getexif()
            original_date = parse_exif_datetime(str(exif.get(36867))) if exif.get(36867) else None
            fallback_exif_date = parse_exif_datetime(str(exif.get(306))) if exif.get(306) else None
            if original_date:
                captured_at = original_date
                if filename_captured_at and abs((filename_captured_at - captured_at).total_seconds()) > 3600:
                    warnings.append("filename_datetime_differs_from_exif")
            elif filename_captured_at:
                captured_at = filename_captured_at
                warnings.append("missing_datetime_original")
                warnings.append("datetime_from_filename")
                if fallback_exif_date and abs((fallback_exif_date - filename_captured_at).total_seconds()) > 3600:
                    warnings.append("exif_datetime_ignored_for_filename")
            elif fallback_exif_date:
                captured_at = fallback_exif_date
                warnings.append("missing_datetime_original")
                warnings.append("datetime_from_exif_datetime")
            make = _clean_exif_text(exif.get(271))
            model = _clean_exif_text(exif.get(272))
    except Exception as exc:
        warnings.append(f"exif_read_failed:{exc.__class__.__name__}")

    if captured_at is None:
        warnings.append("missing_datetime_original")
        if filename_captured_at:
            captured_at = filename_captured_at
            warnings.append("datetime_from_filename")
        else:
            captured_at = datetime.fromtimestamp(path.stat().st_mtime)
            warnings.append("datetime_from_file_modified_time")

    return {
        "captured_at": captured_at,
        "camera_make": make,
        "camera_model": model,
        "warnings": warnings,
    }


def parse_exif_datetime(value: str) -> datetime | None:
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S%z"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def parse_filename_datetime(value: str) -> datetime | None:
    patterns = (
        r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[_ -](?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})",
        r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})[_ -]?(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})",
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        try:
            parts = {key: int(raw) for key, raw in match.groupdict().items()}
            return datetime(
                parts["year"],
                parts["month"],
                parts["day"],
                parts["hour"],
                parts["minute"],
                parts["second"],
            )
        except ValueError:
            continue
    return None


def _clean_exif_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def perceptual_hash(path: str | Path) -> str | None:
    try:
        import imagehash

        with open_oriented_image(path) as image:
            return str(imagehash.phash(image))
    except Exception:
        return None


def write_thumbnail(source: str | Path, output: str | Path, max_side: int = 256) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open_oriented_image(source) as image:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (max_side, max_side), (22, 24, 27))
        x = math.floor((max_side - image.width) / 2)
        y = math.floor((max_side - image.height) / 2)
        canvas.paste(image, (x, y))
        canvas.save(output, "JPEG", quality=86, optimize=True)


def copy_exif(source: str | Path, destination: str | Path) -> bool:
    if piexif is None:
        return False
    source = str(source)
    destination = str(destination)
    try:
        exif = piexif.load(source)
        exif.setdefault("0th", {})[piexif.ImageIFD.Orientation] = 1
        exif_bytes = piexif.dump(exif)
        piexif.insert(exif_bytes, destination)
        return True
    except Exception:
        return False


def replace_extension(path: Path, suffix: str) -> Path:
    return path.with_suffix("." + suffix.lstrip("."))


def file_size(path: str | Path) -> int:
    return int(os.stat(path).st_size)
