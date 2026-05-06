from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, AsyncIterator

from selfietl.jobs.runner import Job


async def job_event_stream(job: Job) -> AsyncIterator[str]:
    yield _format_sse({"type": "snapshot", **job.public()})
    while job.status not in {"done", "failed", "cancelled"}:
        try:
            event = await asyncio.wait_for(job.queue.get(), timeout=15)
            yield _format_sse(event)
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"
    yield _format_sse({"type": "snapshot", **job.public()})


def _format_sse(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps(payload, default=_json_default) + "\n\n"


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
