import { useEffect, useState } from "react";
import { api, type JobStatus } from "@/api/client";

export function useJobEvents(jobId: string | null, onTerminal?: (job: JobStatus) => void) {
  const [job, setJob] = useState<JobStatus | null>(null);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let stopped = false;
    let terminalDelivered = false;

    function update(payload: JobStatus) {
      if (stopped) return;
      setJob(payload);
      if (!terminalDelivered && ["done", "failed", "cancelled"].includes(payload.status)) {
        terminalDelivered = true;
        onTerminal?.(payload);
        source.close();
      }
    }

    const source = new EventSource(`/api/jobs/${jobId}/events`);
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data) as JobStatus;
      update(payload);
    };
    source.onerror = () => {
      source.close();
    };

    const poll = window.setInterval(() => {
      if (terminalDelivered) {
        window.clearInterval(poll);
        return;
      }
      api.job(jobId).then(update).catch(() => undefined);
    }, 2000);

    return () => {
      stopped = true;
      window.clearInterval(poll);
      source.close();
    };
  }, [jobId, onTerminal]);

  return job;
}
