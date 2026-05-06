from __future__ import annotations

import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from selfietl.api.deps import get_config, get_db
from selfietl.config import AppConfig
from selfietl.db import Database
from selfietl.jobs.runner import runner
from selfietl.pipeline.images import is_supported_image

router = APIRouter(prefix="/system", tags=["system"])


class PathResponse(BaseModel):
    path: str


class RevealRequest(BaseModel):
    path: str | None = None


class ResetRequest(BaseModel):
    confirm: bool = False


class InboxStatusResponse(BaseModel):
    path: str
    total_files: int
    supported_files: int
    project_id: int | None
    cataloged_files: int
    detected_files: int
    last_scanned_at: str | None
    needs_scan: bool
    needs_detection: bool


@router.get("/default-source", response_model=PathResponse)
def default_source(config: AppConfig = Depends(get_config)):
    path = default_source_folder(config)
    return PathResponse(path=str(path))


@router.get("/inbox-status", response_model=InboxStatusResponse)
def inbox_status(config: AppConfig = Depends(get_config), db: Database = Depends(get_db)):
    return get_inbox_status(config, db)


@router.post("/reveal")
def reveal(payload: RevealRequest, config: AppConfig = Depends(get_config)):
    path = Path(payload.path).expanduser() if payload.path else default_source_folder(config)
    path.mkdir(parents=True, exist_ok=True)
    try:
        reveal_path(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open folder: {exc}") from exc
    return {"ok": True, "path": str(path)}


@router.post("/pick-folder", response_model=PathResponse)
def pick_folder():
    try:
        path = pick_local_folder()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not choose folder: {exc}") from exc
    return PathResponse(path=str(path))


@router.post("/reset")
def reset(payload: ResetRequest, config: AppConfig = Depends(get_config), db: Database = Depends(get_db)):
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Reset requires confirmation")
    runner.pause_new_jobs()
    try:
        runner.cancel_active_jobs()
        if not runner.wait_for_idle(timeout_seconds=30):
            raise HTTPException(status_code=409, detail="The current job is still stopping. Try reset again in a moment.")
        reset_app_data(config, db)
        return {"ok": True, "inbox_path": str(default_source_folder(config))}
    finally:
        runner.resume_new_jobs()


def get_inbox_status(config: AppConfig, db: Database) -> InboxStatusResponse:
    path = default_source_folder(config)
    files = [item for item in path.iterdir() if item.is_file()]
    supported = [item for item in files if is_supported_image(item)]
    project = db.fetchone(
        "SELECT id, last_scanned_at FROM projects WHERE source_folder = ? ORDER BY created_at DESC LIMIT 1",
        (str(path),),
    )
    project_id = int(project["id"]) if project else None
    last_scanned_at = str(project["last_scanned_at"]) if project and project["last_scanned_at"] else None
    cataloged = 0
    detected = 0
    if project_id is not None:
        counts = db.fetchone(
            """
            SELECT COUNT(*) AS cataloged,
                   SUM(CASE WHEN p.detected_at IS NOT NULL THEN 1 ELSE 0 END) AS detected
            FROM project_photos pp
            JOIN photos p ON p.hash = pp.photo_hash
            WHERE pp.project_id = ?
            """,
            (project_id,),
        )
        cataloged = int(counts["cataloged"] or 0)
        detected = int(counts["detected"] or 0)
    latest_mtime = max((item.stat().st_mtime for item in supported), default=0)
    last_scan_ts = _parse_timestamp(last_scanned_at).timestamp() if last_scanned_at else 0
    return InboxStatusResponse(
        path=str(path),
        total_files=len(files),
        supported_files=len(supported),
        project_id=project_id,
        cataloged_files=cataloged,
        detected_files=detected,
        last_scanned_at=last_scanned_at,
        needs_scan=project_id is None or last_scan_ts < latest_mtime,
        needs_detection=cataloged > detected,
    )


def reset_app_data(config: AppConfig, db: Database) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM renders")
        conn.execute("DELETE FROM project_photos")
        conn.execute("DELETE FROM photos")
        conn.execute("DELETE FROM projects")
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('projects', 'renders')")
        except Exception:
            pass

    for path in [config.data_dir / "cache", config.aligned_dir, config.exports_dir]:
        if path.exists():
            shutil.rmtree(path)
    config.ensure_dirs()
    default_source_folder(config)


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(value)


def default_source_folder(config: AppConfig) -> Path:
    path = config.data_dir / "inbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reveal_path(path: Path) -> None:
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["open", str(path)], check=True)
    elif system == "Windows":
        subprocess.run(["explorer", str(path)], check=True)
    else:
        subprocess.run(["xdg-open", str(path)], check=True)


def pick_local_folder() -> Path:
    system = platform.system()
    if system == "Darwin":
        completed = subprocess.run(
            [
                "osascript",
                "-e",
                'POSIX path of (choose folder with prompt "Choose your selfie source folder")',
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if completed.returncode != 0:
            raise RuntimeError("Folder selection cancelled")
        value = completed.stdout.strip()
        if not value:
            raise RuntimeError("Folder selection cancelled")
        return Path(value).expanduser().resolve()

    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.update()
    try:
        value = filedialog.askdirectory(title="Choose your selfie source folder")
    finally:
        root.destroy()
    if not value:
        raise RuntimeError("Folder selection cancelled")
    return Path(value).expanduser().resolve()
