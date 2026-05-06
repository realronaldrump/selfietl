import { useEffect, useState } from "react";
import type { JobStatus } from "@/api/client";

export function useJobEvents(jobId: string | null, onTerminal?: (job: JobStatus) => void) {
  const [job, setJob] = useState<JobStatus | null>(null);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    source.onmessage = (event) => {
      const payload = JSON.parse(event.data) as JobStatus;
      setJob(payload);
      if (["done", "failed", "cancelled"].includes(payload.status)) {
        onTerminal?.(payload);
        source.close();
      }
    };
    source.onerror = () => {
      source.close();
    };
    return () => source.close();
  }, [jobId, onTerminal]);

  return job;
}
