import { useQuery } from "@tanstack/react-query";
import { Clipboard, Film } from "lucide-react";
import { api, type Project, type Render } from "@/api/client";
import { Badge, Button, PageFrame, Panel } from "@/components/ui";

export function History({ project }: { project: Project }) {
  const rendersQuery = useQuery({ queryKey: ["renders", project.id], queryFn: () => api.renders(project.id) });
  const renders = rendersQuery.data ?? [];
  return (
    <PageFrame size="narrow">
      <Panel>
        <h2 className="text-xl font-black text-ink">Video history</h2>
        <p className="mt-1 text-sm font-medium text-ink/55">Finished MP4 files stay on disk. This page keeps the path so you can find them again.</p>
      </Panel>
      {renders.length === 0 ? (
        <Panel>No videos created yet.</Panel>
      ) : (
        <div className="space-y-3">
          {renders.map((render) => (
            <RenderRow key={render.id} render={render} />
          ))}
        </div>
      )}
    </PageFrame>
  );
}

function RenderRow({ render }: { render: Render }) {
  const tone = render.status === "done" ? "good" : render.status === "failed" ? "bad" : render.status === "cancelled" ? "warn" : "default";
  return (
    <Panel className="grid gap-4 lg:grid-cols-[12rem_1fr_auto] lg:items-center">
      <div className="overflow-hidden rounded-md bg-ink">
        {render.status === "done" ? (
          <video className="aspect-video w-full" src={`/api/renders/${render.id}/file`} controls />
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
      <Button
        variant="secondary"
        size="sm"
        disabled={!render.output_path}
        onClick={() => render.output_path && navigator.clipboard.writeText(render.output_path)}
      >
        <Clipboard className="h-4 w-4" />
        Copy path
      </Button>
    </Panel>
  );
}
