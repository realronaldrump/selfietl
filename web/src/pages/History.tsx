import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clipboard, Film, Trash2, XCircle } from "lucide-react";
import { api, renderFileUrl, renderPosterUrl, type Project, type Render } from "@/api/client";
import { Badge, Button, PageFrame, Panel } from "@/components/ui";

export function History({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const rendersQuery = useQuery({ queryKey: ["renders", project.id], queryFn: () => api.renders(project.id) });
  const renders = rendersQuery.data ?? [];
  const failedCount = renders.filter((render) => render.status === "failed" || render.status === "cancelled").length;
  const cleanupMutation = useMutation({
    mutationFn: () => api.deleteRenderHistory(project.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["renders", project.id] }),
  });
  const cacheMutation = useMutation({ mutationFn: api.clearRenderCache });
  const jobsMutation = useMutation({ mutationFn: api.clearCompletedJobs });

  function clearFailed() {
    if (failedCount === 0) return;
    if (!window.confirm(`Delete ${failedCount} failed or cancelled history entr${failedCount === 1 ? "y" : "ies"} and clean stale render cache?`)) return;
    cleanupMutation.mutate();
  }

  function clearCache() {
    if (!window.confirm("Clear leftover render working files? This does not delete completed videos.")) return;
    cacheMutation.mutate();
  }

  function clearJobs() {
    if (!window.confirm("Clear old completed, failed, and cancelled background jobs? This does not delete videos.")) return;
    jobsMutation.mutate();
  }

  return (
    <PageFrame size="narrow">
      <Panel>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-xl font-black text-ink">Video history</h2>
            <p className="mt-1 text-sm font-medium text-ink/55">Finished MP4 files stay on disk. This page keeps the path so you can find them again.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="secondary" disabled={failedCount === 0 || cleanupMutation.isPending} onClick={clearFailed}>
              <XCircle className="h-4 w-4" />
              Clear failed
            </Button>
            <Button type="button" size="sm" variant="secondary" disabled={cacheMutation.isPending} onClick={clearCache}>
              <Trash2 className="h-4 w-4" />
              Clean cache
            </Button>
            <Button type="button" size="sm" variant="secondary" disabled={jobsMutation.isPending} onClick={clearJobs}>
              <XCircle className="h-4 w-4" />
              Clear jobs
            </Button>
          </div>
        </div>
        {cleanupMutation.data ? (
          <CleanupNote label="Removed" count={cleanupMutation.data.deleted_render_ids?.length ?? 0} bytes={cleanupMutation.data.freed_bytes ?? 0} />
        ) : null}
        {cacheMutation.data ? <CleanupNote label="Cleaned cache" count={cacheMutation.data.deleted_cache_dirs?.length ?? 0} bytes={cacheMutation.data.freed_bytes ?? 0} /> : null}
        {jobsMutation.data ? <CleanupNote label="Cleared jobs" count={jobsMutation.data.deleted ?? 0} bytes={0} /> : null}
      </Panel>
      {renders.length === 0 ? (
        <Panel>No videos created yet.</Panel>
      ) : (
        <div className="space-y-3">
          {renders.map((render) => (
            <RenderRow key={render.id} projectId={project.id} render={render} />
          ))}
        </div>
      )}
    </PageFrame>
  );
}

function RenderRow({ projectId, render }: { projectId: number; render: Render }) {
  const queryClient = useQueryClient();
  const tone = render.status === "done" ? "good" : render.status === "failed" ? "bad" : render.status === "cancelled" ? "warn" : "default";
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteRender(render.id, { deleteFile: true, deleteCache: true }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["renders", projectId] }),
  });

  function deleteRender() {
    const fileText = render.status === "done" && render.output_path ? " This will also delete the MP4 file." : "";
    if (!window.confirm(`Delete video #${render.id} from history?${fileText}`)) return;
    deleteMutation.mutate();
  }

  return (
    <Panel className="grid gap-4 lg:grid-cols-[12rem_1fr_auto] lg:items-center">
      <div className="overflow-hidden rounded-md bg-ink">
        {render.status === "done" ? (
          <video className="aspect-video w-full" src={renderFileUrl(render.id)} poster={renderPosterUrl(render.id)} controls />
        ) : (
          <div className="flex aspect-video items-center justify-center">
            <Film className="h-8 w-8 text-paper/45" />
          </div>
        )}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={tone}>{render.status}</Badge>
          <span className="font-mono text-xs font-bold text-ink/45">video #{render.id}</span>
        </div>
        <div className="mt-2 break-all font-mono text-sm font-semibold text-ink">{render.output_path ?? "No output yet"}</div>
        {render.error ? <div className="mt-2 rounded bg-coral/10 p-2 text-xs font-semibold text-coral">{render.error}</div> : null}
      </div>
      <div className="flex flex-wrap gap-2 lg:justify-end">
        <Button
          variant="secondary"
          size="sm"
          disabled={!render.output_path}
          onClick={() => render.output_path && navigator.clipboard.writeText(render.output_path)}
        >
          <Clipboard className="h-4 w-4" />
          Copy path
        </Button>
        <Button variant="danger" size="sm" disabled={deleteMutation.isPending || render.status === "queued" || render.status === "running"} onClick={deleteRender}>
          <Trash2 className="h-4 w-4" />
          Delete
        </Button>
      </div>
    </Panel>
  );
}

function CleanupNote({ label, count, bytes }: { label: string; count: number; bytes: number }) {
  return (
    <div className="mt-3 rounded-md bg-teal/10 p-3 text-xs font-bold text-teal">
      {label}: {count.toLocaleString()} item{count === 1 ? "" : "s"} · {formatBytes(bytes)} freed
    </div>
  );
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
}
