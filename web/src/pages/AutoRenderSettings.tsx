import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  Clock,
  Film,
  Loader2,
  MonitorSmartphone,
  RefreshCw,
  Save,
  Sparkles,
  TimerReset,
} from "lucide-react";
import { api, type AutoRenderConfig } from "@/api/client";
import { Badge, Button, Input, Label, PageFrame, Panel, Select, cn } from "@/components/ui";

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

  function applyMotion(profile: MotionProfile) {
    const settings: Record<MotionProfile, Pick<DraftSettings, "morphMode" | "intermediateFrames" | "fps">> = {
      still: { morphMode: "none", intermediateFrames: 0, fps: 15 },
      natural: { morphMode: "landmark_delaunay", intermediateFrames: 4, fps: 30 },
      extra: { morphMode: "landmark_delaunay", intermediateFrames: 8, fps: 30 },
    };
    setDraft({ ...draft!, ...settings[profile] });
  }

  function applyOutput(profile: OutputProfile) {
    const settings: Record<OutputProfile, Pick<DraftSettings, "resolution" | "aspectRatio">> = {
      phone: { resolution: "1080_vertical", aspectRatio: "9:16" },
      square: { resolution: "1080_square", aspectRatio: "square" },
      archive: { resolution: "original", aspectRatio: "original" },
    };
    setDraft({ ...draft!, ...settings[profile] });
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
          Used by both the nightly job and the Render now button. Pick the outcome, not the codec details.
        </p>
        <div className="mt-4 grid gap-5 lg:grid-cols-2">
          <ChoiceGroup
            title="How should it move?"
            choices={[
              {
                title: "Quick cuts",
                body: "Fast slideshow. Good for checking the nightly pipeline.",
                active: currentMotionProfile(draft) === "still",
                icon: <TimerReset className="h-4 w-4" />,
                onClick: () => applyMotion("still"),
              },
              {
                title: "Natural morph",
                body: "Recommended. Smooth face movement without a huge render cost.",
                active: currentMotionProfile(draft) === "natural",
                icon: <Sparkles className="h-4 w-4" />,
                onClick: () => applyMotion("natural"),
              },
              {
                title: "Extra smooth",
                body: "More in-between frames. Softer motion, slower nightly render.",
                active: currentMotionProfile(draft) === "extra",
                icon: <Film className="h-4 w-4" />,
                onClick: () => applyMotion("extra"),
              },
            ]}
          />
          <ChoiceGroup
            title="Where will it be watched?"
            choices={[
              {
                title: "Phone story",
                body: "Vertical 1080p. The clearest default for daily viewing.",
                active: currentOutputProfile(draft) === "phone",
                icon: <MonitorSmartphone className="h-4 w-4" />,
                onClick: () => applyOutput("phone"),
              },
              {
                title: "Square post",
                body: "1080p square. Balanced for phones, tablets, and desktop.",
                active: currentOutputProfile(draft) === "square",
                icon: <Film className="h-4 w-4" />,
                onClick: () => applyOutput("square"),
              },
              {
                title: "Keep original",
                body: "Full source shape and detail. Bigger files, slower renders.",
                active: currentOutputProfile(draft) === "archive",
                icon: <Sparkles className="h-4 w-4" />,
                onClick: () => applyOutput("archive"),
              },
            ]}
          />
        </div>

        <details className="mt-5 rounded-md border border-ink/10 bg-paper p-3">
          <summary className="cursor-pointer text-sm font-black text-ink">Fine tuning</summary>
          <p className="mt-1 text-xs font-semibold leading-5 text-ink/50">
            Only adjust these when you need a very specific export.
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
                <option value="9:16">Vertical phone story</option>
                <option value="square">Square post</option>
                <option value="16:9">Wide landscape</option>
                <option value="original">Keep original</option>
              </Select>
            </Field>
            <Field label="In-between frames">
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
            <Field label="Transition engine">
            <Select
              value={draft.morphMode}
              onChange={(event) => setDraft({ ...draft, morphMode: event.target.value as DraftSettings["morphMode"] })}
            >
              <option value="landmark_delaunay">Face morph</option>
              <option value="rife">GPU smoother</option>
              <option value="none">No morph</option>
            </Select>
            </Field>
          </div>
        </details>
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

type Choice = {
  title: string;
  body: string;
  active: boolean;
  icon: React.ReactNode;
  onClick: () => void;
};

function ChoiceGroup({ title, choices }: { title: string; choices: Choice[] }) {
  return (
    <div>
      <div className="text-sm font-black text-ink">{title}</div>
      <div className="mt-3 grid gap-2">
        {choices.map((choice) => (
          <button
            key={choice.title}
            type="button"
            onClick={choice.onClick}
            className={cn(
              "flex min-h-20 items-start gap-3 rounded-md border p-3 text-left transition",
              choice.active ? "border-teal bg-teal/10 shadow-line" : "border-ink/10 bg-white hover:border-teal/40",
            )}
          >
            <span className={cn("mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md", choice.active ? "bg-teal text-paper" : "bg-paper text-ink/55")}>
              {choice.active ? <Check className="h-4 w-4" /> : choice.icon}
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-black text-ink">{choice.title}</span>
              <span className="mt-1 block text-xs font-semibold leading-5 text-ink/55">{choice.body}</span>
            </span>
          </button>
        ))}
      </div>
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

type MotionProfile = "still" | "natural" | "extra";
type OutputProfile = "phone" | "square" | "archive";

function currentMotionProfile(draft: DraftSettings): MotionProfile {
  if (draft.morphMode === "none" || draft.intermediateFrames === 0) return "still";
  if (draft.intermediateFrames >= 8) return "extra";
  return "natural";
}

function currentOutputProfile(draft: DraftSettings): OutputProfile {
  if (draft.resolution === "original" && draft.aspectRatio === "original") return "archive";
  if (draft.aspectRatio === "square" || draft.resolution === "1080_square") return "square";
  return "phone";
}

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
