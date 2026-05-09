from __future__ import annotations

from selfietl.jobs.runner import Job, JobRunner


def test_clear_terminal_jobs_keeps_active_jobs():
    runner = JobRunner()
    runner.jobs = {
        "done": Job(id="done", name="done", status="done"),
        "failed": Job(id="failed", name="failed", status="failed"),
        "cancelled": Job(id="cancelled", name="cancelled", status="cancelled"),
        "running": Job(id="running", name="running", status="running"),
        "queued": Job(id="queued", name="queued", status="queued"),
    }

    deleted = runner.clear_terminal()

    assert deleted == 3
    assert set(runner.jobs) == {"running", "queued"}
