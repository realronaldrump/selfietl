import { XCircle } from "lucide-react";
import type { JobStatus as JobStatusType } from "@/api/client";
import { Badge, Button, Panel, ProgressBar } from "@/components/ui";

export function JobStatus({
  job,
  onCancel,
}: {
  job: JobStatusType | null;
  onCancel?: () => void;
}) {
  if (!job) return null;
  const tone = job.status === "done" ? "good" : job.status === "failed" ? "bad" : job.status === "cancelled" ? "warn" : "default";
  return (
    <Panel className="border border-ink/10">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Badge tone={tone}>{job.status}</Badge>
            <span className="truncate text-sm font-bold text-ink">{job.stage ?? job.name}</span>
          </div>
          <p className="mt-1 text-sm font-medium text-ink/55">{job.message ?? "Queued"}</p>
        </div>
        {["queued", "running"].includes(job.status) && onCancel ? (
          <Button variant="secondary" size="sm" onClick={onCancel}>
            <XCircle className="h-4 w-4" />
            Cancel
          </Button>
        ) : null}
      </div>
      <div className="mt-4">
        <ProgressBar value={job.progress ?? 0} />
      </div>
      {job.error ? <pre className="mt-3 overflow-auto rounded-md bg-coral/10 p-3 text-xs font-semibold text-coral">{job.error}</pre> : null}
    </Panel>
  );
}
