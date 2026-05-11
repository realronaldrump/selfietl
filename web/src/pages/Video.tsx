import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Film, Pause, Play, Settings, XCircle } from "lucide-react";
import { api, renderFileUrl, renderPlaybackUrl, renderPosterUrl, type AutoRenderConfig, type JobStatus, type Render } from "@/api/client";
import { Badge, Button, PageFrame, Panel, ProgressBar, cn } from "@/components/ui";
import { useJobEvents } from "@/hooks/useJobEvents";

export function Video({ onSettings }: { onSettings: () => void }) {
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);

  const todayQuery = useQuery({ queryKey: ["today"], queryFn: api.today, refetchInterval: 60_000 });
  const autoQuery = useQuery({ queryKey: ["auto-render"], queryFn: api.autoRender, refetchInterval: 60_000 });
  const projectId = todayQuery.data?.project?.id;
  const rendersQuery = useQuery({
    queryKey: ["renders", projectId ?? "none"],
    queryFn: () => (projectId ? api.renders(projectId) : Promise.resolve([])),
    enabled: Boolean(projectId),
  });

  const onTerminal = useCallback(
    (_job: JobStatus) => {
      queryClient.invalidateQueries({ queryKey: ["today"] });
      queryClient.invalidateQueries({ queryKey: ["auto-render"] });
      queryClient.invalidateQueries({ queryKey: ["renders"] });
    },
    [queryClient],
  );
  const job = useJobEvents(jobId, onTerminal);
  const isJobRunning = Boolean(job && ["queued", "running"].includes(job.status));

  const renderNowMutation = useMutation({
    mutationFn: api.runAutoRenderNow,
    onSuccess: (started) => setJobId(started.job_id),
  });

  const latest = todayQuery.data?.latest_render;
  const renders = rendersQuery.data ?? [];

  return (
    <PageFrame size="narrow">
      <header>
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-black tracking-tight text-ink">Video</h1>
          <Film className="h-5 w-5 text-teal" />
        </div>
        <p className="mt-1 text-sm font-semibold text-ink/55">
          Your timelapse rebuilds every night. Tap render now to refresh on demand.
        </p>
      </header>

      <Panel className="overflow-hidden p-0">
        <div className="flex justify-center bg-ink">
          {latest?.video_url ? (
            <video
              key={latest.id}
              className="block h-auto max-h-[78vh] max-w-full bg-ink"
              src={renderPlaybackUrl(latest.id)}
              poster={renderPosterUrl(latest.id)}
              controls
              playsInline
              preload="metadata"
            />
          ) : (
            <div className="flex aspect-[9/16] items-center justify-center text-paper/45">
              <div className="text-center">
                <Film className="mx-auto h-9 w-9" />
                <div className="mt-2 text-sm font-black">No render yet</div>
                <div className="mt-1 text-xs font-semibold">Tap render now to make the first one.</div>
              </div>
            </div>
          )}
        </div>
        <div className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0 text-xs font-semibold text-ink/55">
            <Clock className="mr-1.5 inline h-3.5 w-3.5" />
            {latest?.finished_at ? `Built ${formatRelative(latest.finished_at)}` : "No build yet"}
            {latest?.output_path ? (
              <div className="mt-1 truncate font-mono text-[0.7rem] text-ink/40">{latest.output_path}</div>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            {jobId && isJobRunning ? (
              <Button variant="danger" size="sm" onClick={() => job && api.cancelJob(job.id)}>
                <XCircle className="h-4 w-4" />
                Cancel
              </Button>
            ) : null}
            <Button size="sm" disabled={renderNowMutation.isPending || isJobRunning} onClick={() => renderNowMutation.mutate()}>
              <Play className="h-4 w-4" />
              {isJobRunning ? "Rendering…" : "Render now"}
            </Button>
          </div>
        </div>
        {job && isJobRunning ? (
          <div className="border-t border-ink/10 px-4 py-3">
            <div className="flex items-center justify-between text-xs font-black text-ink/55">
              <span>{humanizeStage(job.stage) ?? job.message ?? "Working"}</span>
              <span>{Math.round((job.progress ?? 0) * 100)}%</span>
            </div>
            <div className="mt-2">
              <ProgressBar value={job.progress ?? 0} />
            </div>
          </div>
        ) : null}
      </Panel>

      <AutoSummary auto={autoQuery.data} onSettings={onSettings} />

      <Panel>
        <h2 className="text-sm font-black uppercase tracking-[0.12em] text-ink">History</h2>
        {renders.length === 0 ? (
          <p className="mt-3 text-sm font-semibold text-ink/55">No renders yet.</p>
        ) : (
          <div className="mt-3 space-y-2">
            {renders.map((render) => (
              <RenderRow key={render.id} render={render} highlight={render.id === latest?.id} />
            ))}
          </div>
        )}
      </Panel>
    </PageFrame>
  );
}

function AutoSummary({ auto, onSettings }: { auto: AutoRenderConfig | undefined; onSettings: () => void }) {
  return (
    <Panel>
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            {auto?.enabled ? <Badge tone="good">Auto-render on</Badge> : <Badge tone="warn">Paused</Badge>}
            {auto?.scheduler_running ? <Badge tone="good">Scheduler live</Badge> : null}
            {auto?.last_error ? <Badge tone="bad">Last failed</Badge> : null}
          </div>
          <div className="mt-1 text-sm font-semibold text-ink/65">
            {auto?.enabled ? `Runs daily at ${formatTimeLabel(auto.time)}` : "Manual only until you turn it back on"}
          </div>
          {auto ? <div className="mt-1 text-xs font-semibold text-ink/45">Next: {formatNextRun(auto.next_run_at)}</div> : null}
          {auto?.last_error ? <div className="mt-1 max-w-md text-xs font-semibold text-coral">{auto.last_error}</div> : null}
        </div>
        <Button size="sm" variant="ghost" onClick={onSettings}>
          <Settings className="h-4 w-4" />
          Settings
        </Button>
      </div>
    </Panel>
  );
}

function RenderRow({ render, highlight }: { render: Render; highlight: boolean }) {
  const tone =
    render.status === "done"
      ? "good"
      : render.status === "failed"
        ? "bad"
        : render.status === "cancelled"
          ? "warn"
          : "default";
  return (
    <div className={cn("grid gap-3 rounded-md border border-ink/10 bg-paper p-3 sm:grid-cols-[6rem_1fr_auto]", highlight && "border-teal/40")}>
      <div className="overflow-hidden rounded bg-ink">
        {render.status === "done" ? (
          <img className="aspect-[9/16] w-full object-cover" src={renderPosterUrl(render.id)} alt="" loading="lazy" />
        ) : (
          <div className="grid aspect-[9/16] place-items-center text-paper/45">
            {render.status === "running" ? <Pause className="h-6 w-6" /> : <Film className="h-6 w-6" />}
          </div>
        )}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={tone}>{render.status}</Badge>
          <span className="text-xs font-bold text-ink/45">#{render.id}</span>
        </div>
        <div className="mt-1 text-xs font-semibold text-ink/55">
          {render.started_at ? `Started ${formatRelative(render.started_at)}` : "Pending"}
        </div>
        {render.error ? <div className="mt-1 text-xs font-semibold text-coral">{render.error}</div> : null}
      </div>
      {render.status === "done" ? (
        <a
          href={renderFileUrl(render.id)}
          className="self-start rounded-md border border-ink/10 px-3 py-2 text-xs font-black text-ink/65 hover:border-teal/30"
          target="_blank"
          rel="noreferrer"
        >
          Open
        </a>
      ) : null}
    </div>
  );
}

function humanizeStage(stage: string | null) {
  if (!stage) return null;
  const labels: Record<string, string> = {
    canonical: "Updating face anchor",
    align: "Aligning frames",
    render_frames: "Drawing frames",
    prepare_video: "Preparing video frames",
    ffmpeg: "Encoding MP4",
  };
  return labels[stage] ?? stage;
}

function formatRelative(value: string) {
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value;
  const diff = Date.now() - date.getTime();
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatTimeLabel(value: string) {
  const [hour = "0", minute = "0"] = value.split(":");
  const date = new Date();
  date.setHours(Number(hour), Number(minute), 0, 0);
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function formatNextRun(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}
