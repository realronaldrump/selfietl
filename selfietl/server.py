from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from selfietl.api import auto_render, capture, photos, projects, renders, system
from selfietl.config import AppConfig, load_config
from selfietl.db import Database
from selfietl.scheduler import AutoRenderScheduler


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or load_config()
    db = Database(config.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = AutoRenderScheduler(db, config)
        await scheduler.start()
        app.state.auto_render_scheduler = scheduler
        try:
            yield
        finally:
            await scheduler.stop()
            app.state.auto_render_scheduler = None

    app = FastAPI(title="SelfieTL", version="0.2.0", lifespan=lifespan)
    app.state.config = config
    app.state.db = db

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(projects.router, prefix="/api")
    app.include_router(photos.router, prefix="/api")
    app.include_router(renders.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(capture.router, prefix="/api")
    app.include_router(auto_render.router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {"ok": True, "data_dir": str(config.data_dir)}

    dist = _web_dist()
    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API endpoint not found")
            dist_root = dist.resolve()
            candidate = (dist_root / path).resolve()
            if candidate.is_relative_to(dist_root) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def missing_frontend():
            return {
                "app": "SelfieTL",
                "message": "Frontend bundle not found. Run `cd web && npm install && npm run build`.",
            }

    return app


def _web_dist() -> Path:
    return Path(__file__).resolve().parents[1] / "web" / "dist"


def __getattr__(name: str):
    """Keep ``uvicorn selfietl.server:app`` compatible without import-time I/O."""
    if name == "app":
        return create_app()
    raise AttributeError(name)
