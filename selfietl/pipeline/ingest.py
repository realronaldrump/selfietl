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
CancelCheck = Callable[[], None]


def scan_project(
    db: Database,
    config: AppConfig,
    project_id: int,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict:
    project = db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if project is None:
        raise RuntimeError(f"Project {project_id} does not exist")

    source_folder = Path(project["source_folder"]).expanduser()
    if not source_folder.exists() or not source_folder.is_dir():
        raise RuntimeError(f"Source folder does not exist: {source_folder}")

    files = sorted(path for path in source_folder.rglob("*") if path.is_file() and is_supported_image(path))
    total = len(files)
    inserted = 0
    linked = 0
    exact_duplicates = 0
    perceptual_duplicates = 0
    unlinked = 0
    warnings: list[dict] = []
    seen_hashes: set[str] = set()
    orphaned_hashes: list[str] = []

    with db.connect() as conn:
        for idx, path in enumerate(files):
            if cancel_check:
                cancel_check()
            absolute = path.resolve()
            if progress:
                progress("scan", idx + 1, total, f"Hashing {absolute.name}")

            try:
                digest = sha1_file(absolute)
            except OSError as exc:
                warnings.append({"path": str(absolute), "warning": f"read_failed:{exc.__class__.__name__}"})
                prior = conn.execute(
                    """
                    SELECT p.hash
                    FROM photos p
                    JOIN project_photos pp ON pp.photo_hash = p.hash
                    WHERE pp.project_id = ? AND p.path = ?
                    """,
                    (project_id, str(absolute)),
                ).fetchone()
                if prior:
                    seen_hashes.add(str(prior["hash"]))
                continue

            existing = conn.execute("SELECT hash, path FROM photos WHERE hash = ?", (digest,)).fetchone()
            if existing is None:
                try:
                    width, height = image_dimensions(absolute)
                    meta = exif_metadata(absolute)
                    phash = perceptual_hash(absolute)
                    if phash:
                        duplicate = conn.execute(
                            "SELECT hash FROM photos WHERE perceptual_hash = ? LIMIT 1",
                            (phash,),
                        ).fetchone()
                        if duplicate:
                            perceptual_duplicates += 1

                    conn.execute(
                        """
                        INSERT INTO photos (
                            hash, path, captured_at, width, height, file_size,
                            camera_make, camera_model, perceptual_hash, warnings_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            digest,
                            str(absolute),
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
                if not Path(existing["path"]).is_file():
                    conn.execute("UPDATE photos SET path = ? WHERE hash = ?", (str(absolute), digest))

            cursor = conn.execute(
                "INSERT OR IGNORE INTO project_photos (project_id, photo_hash, added_at) VALUES (?, ?, ?)",
                (project_id, digest, datetime.now().isoformat(sep=" ")),
            )
            linked += int(cursor.rowcount or 0)
            seen_hashes.add(digest)

        linked_hashes = {
            str(row["photo_hash"])
            for row in conn.execute(
                "SELECT photo_hash FROM project_photos WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        }
        stale_hashes = sorted(linked_hashes - seen_hashes)
        if stale_hashes:
            conn.execute(
                "DELETE FROM project_photos WHERE project_id = ? AND photo_hash IN ({})".format(
                    ",".join("?" for _ in stale_hashes)
                ),
                (project_id, *stale_hashes),
            )
            unlinked = len(stale_hashes)
        orphaned_hashes = [
            str(row["hash"])
            for row in conn.execute(
                """
                SELECT p.hash
                FROM photos p
                WHERE NOT EXISTS (
                    SELECT 1 FROM project_photos pp WHERE pp.photo_hash = p.hash
                )
                """
            ).fetchall()
        ]
        if orphaned_hashes:
            conn.execute(
                "DELETE FROM photos WHERE hash IN ({})".format(",".join("?" for _ in orphaned_hashes)),
                tuple(orphaned_hashes),
            )
        conn.execute(
            "UPDATE projects SET last_scanned_at = ? WHERE id = ?",
            (datetime.now().isoformat(sep=" "), project_id),
        )

    for photo_hash in orphaned_hashes:
        _remove_cached_photo(config, photo_hash)
    return {
        "total_files": total,
        "inserted": inserted,
        "linked": linked,
        "exact_duplicates": exact_duplicates,
        "perceptual_duplicates": perceptual_duplicates,
        "unlinked": unlinked,
        "warnings": warnings,
    }


def _remove_cached_photo(config: AppConfig, photo_hash: str) -> None:
    paths = [
        config.thumbs_dir / f"{photo_hash}.jpg",
        config.landmarks_dir / f"{photo_hash}.npz",
        config.aligned_landmarks_dir / f"{photo_hash}.npz",
        config.hair_source_masks_dir / f"{photo_hash}.npz",
        config.hair_aligned_masks_dir / f"{photo_hash}.png",
        config.hair_composites_dir / f"{photo_hash}.png",
        config.aligned_dir / f"{photo_hash}.jpg",
        config.aligned_dir / f"{photo_hash}.png",
    ]
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def create_project(db: Database, name: str, source_folder: str, config_snapshot: dict | None = None) -> int:
    normalized = str(Path(source_folder).expanduser())
    with db.connect() as conn:
        existing = conn.execute(
            "SELECT id FROM projects WHERE source_folder = ? ORDER BY created_at DESC LIMIT 1",
            (normalized,),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cur = conn.execute(
            """
            INSERT INTO projects (name, source_folder, created_at, config_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                name,
                normalized,
                datetime.now().isoformat(sep=" "),
                json.dumps(config_snapshot or {}),
            ),
        )
        return int(cur.lastrowid)
