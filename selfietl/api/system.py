from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from selfietl.api.deps import get_config
from selfietl.config import AppConfig

router = APIRouter(prefix="/system", tags=["system"])


class PathResponse(BaseModel):
    path: str


class RevealRequest(BaseModel):
    path: str | None = None


@router.get("/default-source", response_model=PathResponse)
def default_source(config: AppConfig = Depends(get_config)):
    path = default_source_folder(config)
    return PathResponse(path=str(path))


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
