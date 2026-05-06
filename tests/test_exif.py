from pathlib import Path

import piexif
from PIL import Image

from selfietl.pipeline.images import copy_exif
from selfietl.pipeline.images import exif_metadata, parse_filename_datetime


def test_copy_exif_preserves_datetime_original(tmp_path: Path):
    source = tmp_path / "source.jpg"
    output = tmp_path / "output.jpg"
    image = Image.new("RGB", (32, 32), (220, 100, 40))
    exif = {
        "0th": {piexif.ImageIFD.Make: "SelfieTLTest"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: "2020:01:02 03:04:05"},
    }
    image.save(source, "JPEG", exif=piexif.dump(exif))
    Image.new("RGB", (32, 32), (40, 100, 220)).save(output, "JPEG")

    assert copy_exif(source, output)
    copied = piexif.load(str(output))

    assert copied["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2020:01:02 03:04:05"
    assert copied["0th"][piexif.ImageIFD.Make] == b"SelfieTLTest"


def test_age_lapse_filename_is_used_when_datetime_original_is_missing(tmp_path: Path):
    source = tmp_path / "2021-03-02_14-12-40.jpg"
    image = Image.new("RGB", (32, 32), (220, 100, 40))
    exif = {"0th": {piexif.ImageIFD.DateTime: "2026:05:05 20:56:42"}}
    image.save(source, "JPEG", exif=piexif.dump(exif))

    metadata = exif_metadata(source)

    assert metadata["captured_at"].isoformat(sep=" ") == "2021-03-02 14:12:40"
    assert "datetime_from_filename" in metadata["warnings"]
    assert "exif_datetime_ignored_for_filename" in metadata["warnings"]


def test_parse_filename_datetime_accepts_age_lapse_pattern():
    parsed = parse_filename_datetime("2025-09-18_16-25-49.jpg")

    assert parsed is not None
    assert parsed.isoformat(sep=" ") == "2025-09-18 16:25:49"
