from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.pipeline.images import (
    exif_metadata,
    file_size,
    image_dimensions,
    is_supported_image,
    perceptual_hash,
    sha1_file,
    write_thumbnail,
)

Progress = Callable[[str, int, int, str], None]


def scan_project(
    db: Database,
    config: AppConfig,
    project_id: int,
    progress: Progress | None = None,
) -> dict:
    project = db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if project is None:
        raise RuntimeError(f"Project {project_id} does not exist")

    source_folder = Path(project["source_folder"]).expanduser()
    if not source_folder.exists() or not source_folder.is_dir():
        raise RuntimeError(f"Source folder does not exist: {source_folder}")

    files = [path for path in source_folder.rglob("*") if path.is_file() and is_supported_image(path)]
    total = len(files)
    inserted = 0
    linked = 0
    exact_duplicates = 0
    perceptual_duplicates = 0
    warnings: list[dict] = []

    with db.connect() as conn:
        for idx, path in enumerate(files):
            absolute = path.resolve()
            if progress:
                progress("scan", idx + 1, total, f"Hashing {absolute.name}")

            try:
                digest = sha1_file(absolute)
            except OSError as exc:
                warnings.append({"path": str(absolute), "warning": f"read_failed:{exc.__class__.__name__}"})
                continue

            link_hash = digest
            existing = conn.execute("SELECT hash FROM photos WHERE hash = ?", (digest,)).fetchone()
            if existing is None:
                try:
                    width, height = image_dimensions(absolute)
                    meta = exif_metadata(absolute)
                    phash = perceptual_hash(absolute)
                    chosen_path = absolute
                    if phash:
                        duplicate = conn.execute(
                            "SELECT hash, width, height FROM photos WHERE perceptual_hash = ?",
                            (phash,),
                        ).fetchone()
                        if duplicate:
                            duplicate_area = int(duplicate["width"] or 0) * int(duplicate["height"] or 0)
                            new_area = width * height
                            perceptual_duplicates += 1
                            if new_area <= duplicate_area:
                                chosen_path = None
                                link_hash = duplicate["hash"]
                            else:
                                conn.execute(
                                    "UPDATE photos SET path = ?, width = ?, height = ?, file_size = ? WHERE hash = ?",
                                    (str(absolute), width, height, file_size(absolute), duplicate["hash"]),
                                )
                                link_hash = duplicate["hash"]

                    if chosen_path is not None:
                        conn.execute(
                            """
                            INSERT INTO photos (
                                hash, path, captured_at, width, height, file_size,
                                camera_make, camera_model, perceptual_hash, warnings_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                digest,
                                str(chosen_path),
                                meta["captured_at"].isoformat(sep=" "),
                                width,
                                height,
                                file_size(absolute),
                                meta["camera_make"],
                                meta["camera_model"],
                                phash,
                                json.dumps(meta["warnings"]),
                            ),
                        )
                        write_thumbnail(absolute, config.thumbs_dir / f"{digest}.jpg")
                        inserted += 1
                except Exception as exc:
                    warnings.append({"path": str(absolute), "warning": f"image_read_failed:{exc.__class__.__name__}"})
                    continue
            else:
                exact_duplicates += 1

            conn.execute(
                "INSERT OR IGNORE INTO project_photos (project_id, photo_hash, added_at) VALUES (?, ?, ?)",
                (project_id, link_hash, datetime.now().isoformat(sep=" ")),
            )
            linked += 1

    return {
        "total_files": total,
        "inserted": inserted,
        "linked": linked,
        "exact_duplicates": exact_duplicates,
        "perceptual_duplicates": perceptual_duplicates,
        "warnings": warnings,
    }


def create_project(db: Database, name: str, source_folder: str, config_snapshot: dict | None = None) -> int:
    return db.execute(
        """
        INSERT INTO projects (name, source_folder, created_at, config_json)
        VALUES (?, ?, ?, ?)
        """,
        (
            name,
            str(Path(source_folder).expanduser()),
            datetime.now().isoformat(sep=" "),
            json.dumps(config_snapshot or {}),
        ),
    )
