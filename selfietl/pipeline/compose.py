from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from selfietl.config import AppConfig, RenderConfig
from selfietl.db import Database
from selfietl.pipeline.align import align_project, aligned_path
from selfietl.pipeline.canonical import compute_canonical_face
from selfietl.pipeline.morph_delaunay import morph_pair
from selfietl.pipeline.normalize import normalize_to_reference
from selfietl.pipeline.overlay import draw_date_overlay

Progress = Callable[[str, int, int, str], None]
CancelCheck = Callable[[], None]


def render_project(
    db: Database,
    config: AppConfig,
    project_id: int,
    render_config: RenderConfig,
    render_id: int,
    progress: Progress | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict:
    def check_cancel() -> None:
        if cancel_check:
            cancel_check()

    project = db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if project is None:
        raise RuntimeError(f"Project {project_id} does not exist")

    with db.connect() as conn:
        conn.execute(
            "UPDATE renders SET status = ?, started_at = ?, config_json = ? WHERE id = ?",
            ("running", datetime.now().isoformat(sep=" "), render_config.model_dump_json(), render_id),
        )

    if not project["canonical_landmarks_path"] or not Path(project["canonical_landmarks_path"]).exists():
        compute_canonical_face(db, config, project_id, progress=progress, cancel_check=cancel_check)
    check_cancel()
    align_project(db, config, project_id, mode=render_config.alignment_mode, progress=progress, cancel_check=cancel_check)
    check_cancel()

    rows = _active_rows(db, project_id)
    if len(rows) < 1:
        raise RuntimeError("No active aligned photos are available to render")

    output_path = Path(render_config.output_path).expanduser() if render_config.output_path else _default_output_path(config)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = config.render_cache_dir / f"render_{render_id}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    source_assets = _source_assets(config, rows, render_config, work_dir, progress, cancel_check)

    frame_index = 1
    total_pairs = max(0, len(rows) - 1)
    expected_frames = len(rows) + total_pairs * render_config.intermediate_frames
    for idx, row in enumerate(rows):
        check_cancel()
        captured_at = _parse_datetime(row["captured_at"])
        image = Image.open(source_assets[row["hash"]]["image"]).convert("RGB")
        image = draw_date_overlay(image, captured_at, render_config.date_overlay)
        _save_frame(frames_dir, frame_index, image)
        frame_index += 1
        if progress:
            progress("render_frames", frame_index - 1, expected_frames, f"Wrote frame {frame_index - 1}")

        if idx < len(rows) - 1 and render_config.intermediate_frames > 0 and render_config.morph_mode != "none":
            next_row = rows[idx + 1]
            if render_config.morph_mode == "rife":
                intermediates = _rife_or_fallback(
                    source_assets[row["hash"]]["image"],
                    source_assets[next_row["hash"]]["image"],
                    source_assets[row["hash"]]["landmarks"],
                    source_assets[next_row["hash"]]["landmarks"],
                    render_config.intermediate_frames,
                    work_dir / "rife" / f"{idx:05d}",
                )
            else:
                intermediates = morph_pair(
                    source_assets[row["hash"]]["image"],
                    source_assets[next_row["hash"]]["image"],
                    source_assets[row["hash"]]["landmarks"],
                    source_assets[next_row["hash"]]["landmarks"],
                    render_config.intermediate_frames,
                )
            for intermediate in intermediates:
                check_cancel()
                frame = draw_date_overlay(intermediate.convert("RGB"), captured_at, render_config.date_overlay)
                _save_frame(frames_dir, frame_index, frame)
                frame_index += 1
                if progress:
                    progress("render_frames", frame_index - 1, expected_frames, f"Wrote frame {frame_index - 1}")

    if progress:
        progress("ffmpeg", 0, 1, "Assembling video with FFmpeg")
    _run_ffmpeg(frames_dir, output_path, render_config, frame_index - 1, cancel_check=cancel_check)

    finished_at = datetime.now().isoformat(sep=" ")
    with db.connect() as conn:
        conn.execute(
            "UPDATE renders SET output_path = ?, finished_at = ?, status = ? WHERE id = ?",
            (str(output_path), finished_at, "done", render_id),
        )
    if progress:
        progress("ffmpeg", 1, 1, "Export complete")
    return {"output_path": str(output_path), "frames": frame_index - 1}


def create_render_row(db: Database, project_id: int, render_config: RenderConfig) -> int:
    return db.execute(
        """
        INSERT INTO renders (project_id, output_path, config_json, started_at, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            project_id,
            render_config.output_path,
            render_config.model_dump_json(),
            datetime.now().isoformat(sep=" "),
            "queued",
        ),
    )


def mark_render_failed(db: Database, render_id: int, error: str, status: str = "failed") -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE renders SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, error, datetime.now().isoformat(sep=" "), render_id),
        )


def quick_preview(db: Database, config: AppConfig, project_id: int) -> Path:
    rows = _active_rows(db, project_id)
    if not rows:
        raise RuntimeError("No active photos available for preview")
    preview_config = RenderConfig(
        morph_mode="none",
        intermediate_frames=0,
        fps=10,
        resolution="original",
        aspect_ratio="original",
        date_overlay={"enabled": True, "format": "%Y-%m-%d", "font_size_px": 28, "opacity": 0.8},
        fade_in_seconds=0,
        fade_out_seconds=0,
        crf=24,
    )
    work_dir = config.render_cache_dir / f"preview_{project_id}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for idx, row in enumerate(rows, start=1):
        source = aligned_path(config, row["hash"])
        image = Image.open(source).convert("RGB")
        image.thumbnail((960, 960), Image.Resampling.LANCZOS)
        image = prepare_frame(image, preview_config, _parse_datetime(row["captured_at"]))
        _save_frame(frames_dir, idx, image)
    output = config.exports_dir / f"preview_project_{project_id}.mp4"
    _run_ffmpeg(frames_dir, output, preview_config, len(rows))
    return output


def prepare_frame(image: Image.Image, render_config: RenderConfig, captured_at: datetime) -> Image.Image:
    image = prepare_base_frame(image, render_config)
    return draw_date_overlay(image, captured_at, render_config.date_overlay)


def prepare_base_frame(image: Image.Image, render_config: RenderConfig) -> Image.Image:
    image = _crop_aspect(image, render_config.aspect_ratio)
    image = _resize_for_preset(image, render_config.resolution)
    return _ensure_even_dimensions(image)


def _active_rows(db: Database, project_id: int) -> list:
    return db.fetchall(
        """
        SELECT p.hash, p.path, p.captured_at, p.quality_score
        FROM photos p
        JOIN project_photos pp ON pp.photo_hash = p.hash
        WHERE pp.project_id = ? AND p.skipped = 0 AND p.landmarks_path IS NOT NULL
        ORDER BY p.captured_at
        """,
        (project_id,),
    )


def _source_assets(
    config: AppConfig,
    rows: list,
    render_config: RenderConfig,
    work_dir: Path,
    progress: Progress | None,
    cancel_check: CancelCheck | None,
) -> dict[str, dict[str, Path]]:
    def check_cancel() -> None:
        if cancel_check:
            cancel_check()

    paths = {row["hash"]: aligned_path(config, row["hash"]) for row in rows}
    landmarks = {row["hash"]: config.aligned_landmarks_dir / f"{row['hash']}.npz" for row in rows}

    normalized_dir = work_dir / "normalized"
    prepared_dir = work_dir / "prepared"
    reference = max(rows, key=lambda row: row["quality_score"] or 0)
    reference_path = paths[reference["hash"]]
    if render_config.color_normalize and len(rows) >= 2:
        normalized_dir.mkdir(parents=True, exist_ok=True)
        for idx, row in enumerate(rows, start=1):
            check_cancel()
            source = paths[row["hash"]]
            output = normalized_dir / f"{row['hash']}.jpg"
            if source == reference_path:
                shutil.copyfile(source, output)
            else:
                normalize_to_reference(source, reference_path, output)
            paths[row["hash"]] = output
            if progress:
                progress("prepare_video", idx, len(rows), "Matching color")

    needs_prepared = render_config.resolution != "original" or render_config.aspect_ratio != "original"
    if not needs_prepared:
        return {row["hash"]: {"image": paths[row["hash"]], "landmarks": landmarks[row["hash"]]} for row in rows}

    prepared_dir.mkdir(parents=True, exist_ok=True)
    for idx, row in enumerate(rows, start=1):
        check_cancel()
        source_path = paths[row["hash"]]
        landmarks_path = landmarks[row["hash"]]
        output_image = prepared_dir / f"{row['hash']}.jpg"
        output_landmarks = prepared_dir / f"{row['hash']}.npz"
        with Image.open(source_path) as image:
            payload = np.load(landmarks_path)
            source_landmarks = np.asarray(payload["landmarks"], dtype=np.float64)
            prepared, prepared_landmarks = _prepare_image_and_landmarks(image.convert("RGB"), source_landmarks, render_config)
            prepared.save(output_image, "JPEG", quality=95, optimize=True)
            np.savez_compressed(
                output_landmarks,
                landmarks=prepared_landmarks.astype(np.float32),
                target_size=np.array(prepared.size, dtype=np.int32),
            )
        paths[row["hash"]] = output_image
        landmarks[row["hash"]] = output_landmarks
        if progress:
            progress("prepare_video", idx, len(rows), "Preparing fast video frames")

    return {row["hash"]: {"image": paths[row["hash"]], "landmarks": landmarks[row["hash"]]} for row in rows}


def _prepare_image_and_landmarks(
    image: Image.Image,
    landmarks: np.ndarray,
    render_config: RenderConfig,
) -> tuple[Image.Image, np.ndarray]:
    image, landmarks = _crop_aspect_with_landmarks(image, landmarks, render_config.aspect_ratio)
    image, landmarks = _resize_with_landmarks(image, landmarks, render_config.resolution)
    image, landmarks = _ensure_even_with_landmarks(image, landmarks)
    return image, landmarks


def _rife_or_fallback(
    image_a: Path,
    image_b: Path,
    landmarks_a: Path,
    landmarks_b: Path,
    intermediate_frames: int,
    output_dir: Path,
):
    try:
        from selfietl.pipeline.morph_rife import interpolate_pair

        for path in interpolate_pair(image_a, image_b, output_dir, intermediate_frames):
            yield Image.open(path).convert("RGB")
    except Exception:
        yield from morph_pair(image_a, image_b, landmarks_a, landmarks_b, intermediate_frames)


def _save_frame(frames_dir: Path, index: int, image: Image.Image) -> Path:
    path = frames_dir / f"frame_{index:06d}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=95, optimize=True)
    return path


def _run_ffmpeg(
    frames_dir: Path,
    output_path: Path,
    render_config: RenderConfig,
    frame_count: int,
    cancel_check: CancelCheck | None = None,
) -> None:
    codec = "libx264" if render_config.codec == "h264" else "libx265"
    duration = frame_count / render_config.fps
    filters = ["format=yuv420p"]
    if render_config.fade_in_seconds > 0:
        filters.append(f"fade=t=in:st=0:d={render_config.fade_in_seconds}")
    if render_config.fade_out_seconds > 0 and duration > render_config.fade_out_seconds:
        start = max(0, duration - render_config.fade_out_seconds)
        filters.append(f"fade=t=out:st={start:.3f}:d={render_config.fade_out_seconds}")
    command = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(render_config.fps),
        "-i",
        str(frames_dir / "frame_%06d.jpg"),
    ]
    if render_config.audio_path:
        command.extend(["-i", str(Path(render_config.audio_path).expanduser())])
    command.extend(["-vf", ",".join(filters), "-c:v", codec, "-crf", str(render_config.crf), "-pix_fmt", "yuv420p"])
    if render_config.audio_path:
        command.extend(["-shortest", "-c:a", "aac", "-b:a", "192k"])
    command.extend(["-movflags", "+faststart", str(output_path)])
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while process.poll() is None:
            if cancel_check:
                try:
                    cancel_check()
                except Exception:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    raise
            time.sleep(0.25)
        stdout, stderr = process.communicate()
    except Exception:
        if process.poll() is None:
            process.kill()
        raise
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command, output=stdout, stderr=stderr)


def _crop_aspect(image: Image.Image, aspect_ratio: str) -> Image.Image:
    ratios = {"square": 1.0, "9:16": 9 / 16, "16:9": 16 / 9}
    target = ratios.get(aspect_ratio)
    if target is None:
        return image
    width, height = image.size
    current = width / height
    if abs(current - target) < 0.001:
        return image
    if current > target:
        new_width = int(height * target)
        left = (width - new_width) // 2
        return image.crop((left, 0, left + new_width, height))
    new_height = int(width / target)
    top = (height - new_height) // 2
    return image.crop((0, top, width, top + new_height))


def _crop_aspect_with_landmarks(
    image: Image.Image,
    landmarks: np.ndarray,
    aspect_ratio: str,
) -> tuple[Image.Image, np.ndarray]:
    ratios = {"square": 1.0, "9:16": 9 / 16, "16:9": 16 / 9}
    target = ratios.get(aspect_ratio)
    if target is None:
        return image, landmarks
    width, height = image.size
    current = width / height
    if abs(current - target) < 0.001:
        return image, landmarks
    shifted = np.asarray(landmarks, dtype=np.float64).copy()
    if current > target:
        new_width = int(height * target)
        left = (width - new_width) // 2
        shifted[:, 0] -= left
        return image.crop((left, 0, left + new_width, height)), shifted
    new_height = int(width / target)
    top = (height - new_height) // 2
    shifted[:, 1] -= top
    return image.crop((0, top, width, top + new_height)), shifted


def _resize_for_preset(image: Image.Image, resolution: str) -> Image.Image:
    sizes = {
        "1080_square": (1080, 1080),
        "1080_vertical": (1080, 1920),
        "4k_landscape": (3840, 2160),
    }
    size = sizes.get(resolution)
    if size is None:
        return image
    return image.resize(size, Image.Resampling.LANCZOS)


def _resize_with_landmarks(
    image: Image.Image,
    landmarks: np.ndarray,
    resolution: str,
) -> tuple[Image.Image, np.ndarray]:
    sizes = {
        "1080_square": (1080, 1080),
        "1080_vertical": (1080, 1920),
        "4k_landscape": (3840, 2160),
    }
    size = sizes.get(resolution)
    if size is None:
        return image, landmarks
    width, height = image.size
    scale = np.array([size[0] / width, size[1] / height], dtype=np.float64)
    resized = image.resize(size, Image.Resampling.LANCZOS)
    scaled = np.asarray(landmarks, dtype=np.float64).copy()
    scaled[:, :2] *= scale
    return resized, scaled


def _ensure_even_dimensions(image: Image.Image) -> Image.Image:
    width, height = image.size
    even = (width - width % 2, height - height % 2)
    if even == image.size:
        return image
    return image.crop((0, 0, even[0], even[1]))


def _ensure_even_with_landmarks(image: Image.Image, landmarks: np.ndarray) -> tuple[Image.Image, np.ndarray]:
    width, height = image.size
    even = (width - width % 2, height - height % 2)
    if even == image.size:
        return image, landmarks
    return image.crop((0, 0, even[0], even[1])), landmarks


def _parse_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(text)


def _default_output_path(config: AppConfig) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return config.exports_dir / f"timelapse_{stamp}.mp4"
