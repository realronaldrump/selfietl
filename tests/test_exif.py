from pathlib import Path

import piexif
from PIL import Image

from selfietl.pipeline.images import copy_exif


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
