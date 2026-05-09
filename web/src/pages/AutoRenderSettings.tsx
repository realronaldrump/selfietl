import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Clock,
  Loader2,
  RefreshCw,
  Save,
  Sparkles,
} from "lucide-react";
import { api, type AutoRenderConfig } from "@/api/client";
import { Badge, Button, Input, Label, PageFrame, Panel, Select } from "@/components/ui";

export function AutoRenderSettings({ onBack }: { onBack: () => void }) {
  const queryClient = useQueryClient();
  const autoQuery = useQuery({ queryKey: ["auto-render"], queryFn: api.autoRender });
  const [draft, setDraft] = useState<DraftSettings | null>(null);

  useEffect(() => {
    if (autoQuery.data && draft === null) {
      setDraft(toDraft(autoQuery.data));
    }
  }, [autoQuery.data, draft]);

  const updateMutation = useMutation({
    mutationFn: api.updateAutoRender,
    onSuccess: (data) => {
      setDraft(toDraft(data));
      queryClient.invalidateQueries({ queryKey: ["auto-render"] });
      queryClient.invalidateQueries({ queryKey: ["today"] });
    },
  });

  const runNowMutation = useMutation({
    mutationFn: api.runAutoRenderNow,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auto-render"] });
      queryClient.invalidateQueries({ queryKey: ["today"] });
      queryClient.invalidateQueries({ queryKey: ["renders"] });
    },
  });

  if (!draft) {
    return (
      <Panel className="text-sm font-semibold text-ink/55">
        <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
        Loading settings…
      </Panel>
    );
  }

  function save() {
    updateMutation.mutate({
      enabled: draft!.enabled,
      time: draft!.time,
      render_config: {
        resolution: draft!.resolution,
        aspect_ratio: draft!.aspectRatio,
        intermediate_frames: draft!.intermediateFrames,
        fps: draft!.fps,
        morph_mode: draft!.morphMode,
      },
    });
  }

  return (
    <PageFrame size="narrow">
      <div className="flex items-center justify-between">
        <Button size="sm" variant="ghost" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
          Back
        </Button>
        <div className="text-xs font-bold uppercase tracking-[0.18em] text-ink/55">Auto-render</div>
        <div className="w-12" />
      </div>

      <Panel>
        <div className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-amber" />
          <h1 className="text-xl font-black text-ink">Schedule</h1>
        </div>
        <p className="mt-1 text-sm font-semibold text-ink/65">
          Pick a time when the mini PC is idle. The default 3 AM window works well for most days.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_8rem]">
          <label className="flex min-h-12 items-center justify-between gap-3 rounded-md border border-ink/10 bg-paper px-3 py-2 shadow-line">
            <span className="text-sm font-black text-ink">Run every day</span>
            <input
              type="checkbox"
              checked={draft.enabled}
              className="h-5 w-5 accent-teal"
              onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
            />
          </label>
          <div>
            <Label>Time</Label>
            <Input
              type="time"
              value={draft.time}
              onChange={(event) => setDraft({ ...draft, time: event.target.value || "03:00" })}
            />
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs font-semibold text-ink/55">
          <Clock className="h-4 w-4 text-teal" />
          {autoQuery.data ? <Badge>Next: {formatNextRun(autoQuery.data.next_run_at)}</Badge> : null}
          {autoQuery.data?.last_run_date ? <Badge tone="good">Last: {autoQuery.data.last_run_date}</Badge> : null}
          {autoQuery.data?.last_error ? <Badge tone="bad">Last failed</Badge> : null}
        </div>
        {autoQuery.data?.last_error ? (
          <div className="mt-3 rounded-md bg-coral/10 p-3 text-xs font-semibold text-coral">
            {autoQuery.data.last_error}
          </div>
        ) : null}
      </Panel>

      <Panel>
        <h2 className="text-sm font-black uppercase tracking-[0.12em] text-ink">Default video</h2>
        <p className="mt-1 text-xs font-semibold text-ink/55">
          Used by both the nightly job and the Render now button.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Size">
            <Select
              value={draft.resolution}
              onChange={(event) => setDraft({ ...draft, resolution: event.target.value as DraftSettings["resolution"] })}
            >
              <option value="1080_vertical">1080p vertical · phone friendly</option>
              <option value="1080_square">1080p square</option>
              <option value="original">Full quality original</option>
              <option value="4k_landscape">4K landscape</option>
            </Select>
          </Field>
          <Field label="Shape">
            <Select
              value={draft.aspectRatio}
              onChange={(event) => setDraft({ ...draft, aspectRatio: event.target.value as DraftSettings["aspectRatio"] })}
            >
              <option value="9:16">9:16 vertical</option>
              <option value="square">Square</option>
              <option value="16:9">16:9</option>
              <option value="original">Original</option>
            </Select>
          </Field>
          <Field label="Smoothness">
            <Select
              value={String(draft.intermediateFrames)}
              onChange={(event) => setDraft({ ...draft, intermediateFrames: Number(event.target.value) })}
            >
              <option value="0">Cuts only</option>
              <option value="2">Subtle</option>
              <option value="4">Smooth (recommended)</option>
              <option value="8">Silky</option>
            </Select>
          </Field>
          <Field label="Frame rate">
            <Select
              value={String(draft.fps)}
              onChange={(event) => setDraft({ ...draft, fps: Number(event.target.value) })}
            >
              <option value="24">24 fps</option>
              <option value="30">30 fps (recommended)</option>
              <option value="60">60 fps</option>
            </Select>
          </Field>
          <Field label="Transition style">
            <Select
              value={draft.morphMode}
              onChange={(event) => setDraft({ ...draft, morphMode: event.target.value as DraftSettings["morphMode"] })}
            >
              <option value="landmark_delaunay">Smooth face morph</option>
              <option value="rife">GPU smoother</option>
              <option value="none">No morph</option>
            </Select>
          </Field>
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-2">
        <Button onClick={save} disabled={updateMutation.isPending}>
          {updateMutation.isPending ? <Loader2 className="h-5 w-5 animate-spin" /> : <Save className="h-5 w-5" />}
          Save schedule
        </Button>
        <Button variant="secondary" onClick={() => runNowMutation.mutate()} disabled={runNowMutation.isPending}>
          <RefreshCw className="h-5 w-5" />
          {runNowMutation.isPending ? "Starting…" : "Run a render now"}
        </Button>
      </div>
    </PageFrame>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Label>{label}</Label>
      <div className="mt-1">{children}</div>
    </div>
  );
}

type DraftSettings = {
  enabled: boolean;
  time: string;
  resolution: "1080_vertical" | "1080_square" | "original" | "4k_landscape";
  aspectRatio: "9:16" | "square" | "16:9" | "original";
  intermediateFrames: number;
  fps: number;
  morphMode: "landmark_delaunay" | "rife" | "none";
};

function toDraft(config: AutoRenderConfig): DraftSettings {
  const render = (config.render_config as Record<string, unknown>) ?? {};
  return {
    enabled: config.enabled,
    time: config.time || "03:00",
    resolution: (render.resolution as DraftSettings["resolution"]) ?? "1080_vertical",
    aspectRatio: (render.aspect_ratio as DraftSettings["aspectRatio"]) ?? "9:16",
    intermediateFrames: typeof render.intermediate_frames === "number" ? Number(render.intermediate_frames) : 4,
    fps: typeof render.fps === "number" ? Number(render.fps) : 30,
    morphMode: (render.morph_mode as DraftSettings["morphMode"]) ?? "landmark_delaunay",
  };
}

function formatNextRun(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}
