from datetime import datetime
from pathlib import Path

from PIL import Image

from selfietl.config import load_config
from selfietl.pipeline.single import discard_photo, import_to_inbox


def _make_jpeg(tmp_path: Path) -> bytes:
    image = Image.new("RGB", (320, 320), (220, 200, 180))
    target = tmp_path / "src.jpg"
    image.save(target, "JPEG", quality=85)
    return target.read_bytes()


def test_import_to_inbox_writes_dated_filename(tmp_path: Path):
    config = load_config(tmp_path / "home")
    contents = _make_jpeg(tmp_path)
    when = datetime(2026, 5, 8, 14, 30, 12)

    saved = import_to_inbox(config, contents=contents, filename="ios.heic", captured_at=when)

    assert saved.exists()
    assert saved.parent == config.inbox_dir
    assert saved.name == "selfie_2026-05-08_143012.heic"
    assert saved.read_bytes() == contents


def test_import_to_inbox_handles_collisions(tmp_path: Path):
    config = load_config(tmp_path / "home")
    contents = _make_jpeg(tmp_path)
    when = datetime(2026, 5, 8, 14, 30, 12)

    first = import_to_inbox(config, contents=contents, filename="a.jpg", captured_at=when)
    second = import_to_inbox(config, contents=contents, filename="a.jpg", captured_at=when)

    assert first != second
    assert first.exists() and second.exists()
    assert second.name.startswith("selfie_2026-05-08_143012_")


def test_import_to_inbox_uses_safe_extension_for_unknown(tmp_path: Path):
    config = load_config(tmp_path / "home")
    saved = import_to_inbox(
        config,
        contents=_make_jpeg(tmp_path),
        filename="weird.bin",
        captured_at=datetime(2026, 5, 8, 9, 0, 0),
    )
    assert saved.suffix == ".jpg"


def test_discard_photo_removes_files_and_row(tmp_path: Path):
    config = load_config(tmp_path / "home")
    from selfietl.db import Database

    db = Database(config.db_path)
    photo_hash = "abc123"
    photo_path = config.inbox_dir / "selfie.jpg"
    photo_path.parent.mkdir(parents=True, exist_ok=True)
    photo_path.write_bytes(b"fake")
    thumb = config.thumbs_dir / f"{photo_hash}.jpg"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"fake-thumb")
    db.execute(
        "INSERT INTO photos (hash, path, captured_at) VALUES (?, ?, ?)",
        (photo_hash, str(photo_path), "2026-05-08 12:00:00"),
    )

    deleted = discard_photo(db, config, photo_hash)
    assert deleted is True
    assert not photo_path.exists()
    assert not thumb.exists()
    assert db.fetchone("SELECT * FROM photos WHERE hash = ?", (photo_hash,)) is None


def test_discard_photo_returns_false_when_missing(tmp_path: Path):
    config = load_config(tmp_path / "home")
    from selfietl.db import Database

    db = Database(config.db_path)
    assert discard_photo(db, config, "missing-hash") is False
