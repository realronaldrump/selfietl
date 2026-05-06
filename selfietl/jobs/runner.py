from __future__ import annotations

import asyncio
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine


class CancellationRequested(RuntimeError):
    pass


@dataclass
class Job:
    id: str
    name: str
    status: str = "queued"
    progress: float = 0.0
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

    def start(self, name: str, work: Callable[[Callable, Callable], dict[str, Any]]) -> Job:
        loop = asyncio.get_running_loop()
        job = Job(id=uuid.uuid4().hex, name=name)
        self.jobs[job.id] = job

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(job.queue.put_nowait, event)

        def progress(stage: str, done: int, total: int, message: str) -> None:
            total = max(total, 1)
            job.stage = stage
            job.progress = max(0.0, min(1.0, done / total))
            job.message = message
            emit({"type": "progress", **job.public()})

        def cancel_check() -> None:
            if job.cancel_event.is_set():
                raise CancellationRequested("Job cancelled")

        async def run() -> None:
            job.status = "running"
            job.started_at = datetime.now()
            emit({"type": "status", **job.public()})
            try:
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

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        job.cancel_event.set()
        return True


runner = JobRunner()
