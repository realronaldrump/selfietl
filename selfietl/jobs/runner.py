from __future__ import annotations

import asyncio
import time
import traceback
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine


class CancellationRequested(RuntimeError):
    pass


class JobsPaused(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    name: str
    status: str = "queued"
    progress: float = 0.0
    progress_done: int = 0
    progress_total: int = 0
    stage: str | None = None
    message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "progress": self.progress,
            "progress_done": self.progress_done,
            "progress_total": self.progress_total,
            "stage": self.stage,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobRunner:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.compute_semaphore = asyncio.Semaphore(1)
        self._state_lock = threading.Lock()
        self._paused = False

    def start(self, name: str, work: Callable[[Callable, Callable], dict[str, Any]]) -> Job:
        with self._state_lock:
            if self._paused:
                raise JobsPaused("The app is resetting. Wait for reset to finish before starting another step.")
            for existing in self.jobs.values():
                if existing.name == name and existing.status in {"queued", "running"}:
                    return existing
            loop = asyncio.get_running_loop()
            job = Job(id=uuid.uuid4().hex, name=name)
            self.jobs[job.id] = job

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(job.queue.put_nowait, event)

        def progress(stage: str, done: int, total: int, message: str) -> None:
            total = max(total, 1)
            job.stage = stage
            job.progress_done = done
            job.progress_total = total
            job.progress = max(0.0, min(1.0, done / total))
            job.message = message
            emit({"type": "progress", **job.public()})

        def cancel_check() -> None:
            if job.cancel_event.is_set():
                raise CancellationRequested("Job cancelled")

        async def run() -> None:
            emit({"type": "status", **job.public()})
            try:
                async with self.compute_semaphore:
                    cancel_check()
                    job.status = "running"
                    job.started_at = datetime.now()
                    job.message = job.message or "Running"
                    emit({"type": "status", **job.public()})
                    result = await asyncio.to_thread(work, progress, cancel_check)
                job.status = "done"
                job.progress = 1.0
                job.result = result
                job.message = "Done"
            except CancellationRequested as exc:
                job.status = "cancelled"
                job.error = str(exc)
                job.message = "Cancelled"
            except Exception as exc:
                job.status = "failed"
                job.error = f"{exc.__class__.__name__}: {exc}"
                job.result = {"traceback": traceback.format_exc(limit=8)}
                job.message = "Failed"
            finally:
                job.finished_at = datetime.now()
                emit({"type": "terminal", **job.public()})

        asyncio.create_task(run())
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        job.cancel_event.set()
        if job.status in {"queued", "running"}:
            job.message = "Cancelling after the current file finishes"
            job.stage = "cancel"
            job.queue.put_nowait({"type": "status", **job.public()})
        return True

    def has_active_jobs(self, except_name: str | None = None) -> bool:
        return any(
            job.status in {"queued", "running"} and (except_name is None or job.name != except_name)
            for job in self.jobs.values()
        )

    def pause_new_jobs(self) -> None:
        with self._state_lock:
            self._paused = True

    def resume_new_jobs(self) -> None:
        with self._state_lock:
            self._paused = False

    def cancel_active_jobs(self) -> None:
        for job in list(self.jobs.values()):
            if job.status in {"queued", "running"}:
                self.cancel(job.id)

    def wait_for_idle(self, timeout_seconds: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self.has_active_jobs():
                return True
            time.sleep(0.1)
        return not self.has_active_jobs()


runner = JobRunner()
