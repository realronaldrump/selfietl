import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Clapperboard, Eye, Film, MonitorSmartphone, Play, SlidersHorizontal, Sparkles, TimerReset, XCircle } from "lucide-react";
import { api, type JobStatus, type Project, type RenderConfig } from "@/api/client";
import { JobStatus as JobStatusPanel } from "@/components/JobStatus";
import { Button, Input, Label, PageFrame, Panel, Select, cn } from "@/components/ui";
import { useJobEvents } from "@/hooks/useJobEvents";

const defaultConfig: RenderConfig = {
  alignment_mode: "similarity",
  morph_mode: "landmark_delaunay",
  intermediate_frames: 4,
  start_date: null,
  end_date: null,
  color_normalize: false,
  fps: 30,
  resolution: "1080_vertical",
  aspect_ratio: "9:16",
  date_overlay: {
    enabled: true,
    format: "%b %Y",
    position: "bottom-right",
    font_size_px: 48,
    opacity: 0.85,
  },
  audio_path: null,
  music_sync: false,
  fade_in_seconds: 0.5,
  fade_out_seconds: 0.5,
  codec: "h264",
  crf: 18,
  output_path: null,
};

export function Render({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const [config, setConfig] = useState<RenderConfig>(defaultConfig);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const historyQuery = useQuery({ queryKey: ["renders", project.id], queryFn: () => api.renders(project.id) });
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: api.jobs, refetchInterval: 1200 });
  const statsQuery = useQuery({ queryKey: ["stats", project.id], queryFn: () => api.stats(project.id) as Promise<RenderStats> });
  const latestDone = useMemo(() => historyQuery.data?.find((render) => render.status === "done"), [historyQuery.data]);
  const activeDates = useMemo(
    () => (statsQuery.data?.timeline ?? []).filter((item) => !item.skipped).map((item) => item.date),
    [statsQuery.data],
  );
  const firstDate = activeDates[0]?.slice(0, 10) ?? "";
  const lastDate = activeDates[activeDates.length - 1]?.slice(0, 10) ?? "";
  const selectedPhotoCount = useMemo(() => countDatesInRange(activeDates, config.start_date, config.end_date), [activeDates, config.end_date, config.start_date]);
  const onTerminal = useCallback(
    (_job: JobStatus) => {
      queryClient.invalidateQueries({ queryKey: ["renders", project.id] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    [project.id, queryClient],
  );
  const job = useJobEvents(jobId, onTerminal);
  const visibleJob = job ?? jobsQuery.data?.find((item) => ["queued", "running"].includes(item.status)) ?? null;
  const activeJob = Boolean(visibleJob && ["queued", "running"].includes(visibleJob.status));
  const renderMutation = useMutation({
    mutationFn: (payload: RenderConfig) => api.render(project.id, payload),
    onSuccess: (started) => {
      setError(null);
      setJobId(started.job_id);
    },
    onError: (err) => setError(err instanceof Error ? err.message : String(err)),
  });

  function previewConfig(): RenderConfig {
    return {
      ...config,
      morph_mode: "none",
      intermediate_frames: 0,
      fps: 15,
      crf: 24,
    };
  }

  function applyRangePreset(preset: RangePreset) {
    if (!activeDates.length) return;
    const lastIndex = activeDates.length - 1;
    const dateAt = (index: number) => activeDates[Math.max(0, Math.min(lastIndex, index))].slice(0, 10);

    const ranges: Record<RangePreset, [string, string]> = {
      first10: [dateAt(0), dateAt(9)],
      latest10: [dateAt(lastIndex - 9), dateAt(lastIndex)],
      first30: [dateAt(0), dateAt(29)],
      latest30: [dateAt(lastIndex - 29), dateAt(lastIndex)],
      middle10: [dateAt(Math.floor(lastIndex / 2) - 4), dateAt(Math.floor(lastIndex / 2) + 5)],
      firstMonth: [dateAt(0), addDays(dateAt(0), 30)],
      latestMonth: [addDays(dateAt(lastIndex), -30), dateAt(lastIndex)],
    };
    const [start, end] = ranges[preset];
    setConfig({ ...config, start_date: start, end_date: end });
  }

  function applyPreset(preset: "fast" | "full" | "test") {
    if (preset === "full") {
      setConfig({
        ...config,
        resolution: "original",
        aspect_ratio: "original",
        intermediate_frames: 8,
        morph_mode: "landmark_delaunay",
        crf: 18,
      });
      return;
    }
    if (preset === "test") {
      setConfig({
        ...config,
        resolution: "1080_vertical",
        aspect_ratio: "9:16",
        intermediate_frames: 0,
        morph_mode: "none",
        crf: 24,
      });
      return;
    }
    setConfig({
      ...config,
      resolution: "1080_vertical",
      aspect_ratio: "9:16",
      intermediate_frames: 4,
      morph_mode: "landmark_delaunay",
      crf: 20,
    });
  }

  function applyMotion(profile: MotionProfile) {
    const settings: Record<MotionProfile, Pick<RenderConfig, "morph_mode" | "intermediate_frames" | "fps">> = {
      still: { morph_mode: "none", intermediate_frames: 0, fps: 15 },
      natural: { morph_mode: "landmark_delaunay", intermediate_frames: 4, fps: 30 },
      extra: { morph_mode: "landmark_delaunay", intermediate_frames: 8, fps: 30 },
    };
    setConfig({ ...config, ...settings[profile] });
  }

  function applyOutput(profile: OutputProfile) {
    const settings: Record<OutputProfile, Pick<RenderConfig, "resolution" | "aspect_ratio" | "crf">> = {
      phone: { resolution: "1080_vertical", aspect_ratio: "9:16", crf: 20 },
      square: { resolution: "1080_square", aspect_ratio: "square", crf: 20 },
      archive: { resolution: "original", aspect_ratio: "original", crf: 18 },
    };
    setConfig({ ...config, ...settings[profile] });
  }

  return (
    <PageFrame size="wide" className="grid gap-4 space-y-0 xl:grid-cols-[minmax(0,1fr)_24rem]">
      <Panel>
        <div className="flex items-center gap-2">
          <Film className="h-5 w-5 text-teal" />
          <h2 className="text-xl font-black text-ink">Create video</h2>
        </div>

        <div className="mt-5 rounded-lg border border-teal/25 bg-teal/10 p-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="text-sm font-black text-ink">One-click MP4</div>
              <p className="mt-1 max-w-2xl text-sm font-semibold leading-6 text-ink/60">
                Recommended: fast 1080p vertical video with smooth face movement. Full quality is available, but it can take much longer.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {activeJob && visibleJob ? (
                <Button type="button" variant="danger" onClick={() => visibleJob && api.cancelJob(visibleJob.id)}>
                  <XCircle className="h-4 w-4" />
                  Cancel
                </Button>
              ) : null}
              <Button
                type="button"
                variant="secondary"
                disabled={renderMutation.isPending || activeJob || selectedPhotoCount === 0}
                onClick={() => renderMutation.mutate(previewConfig())}
              >
                <Eye className="h-4 w-4" />
                Preview range
              </Button>
              <Button disabled={renderMutation.isPending || activeJob || selectedPhotoCount === 0} onClick={() => renderMutation.mutate(config)}>
                <Play className="h-4 w-4" />
                Create video
              </Button>
            </div>
          </div>
          <div className="mt-4 grid gap-2 md:grid-cols-4">
            <Summary label="Motion" value={motionLabel(config)} />
            <Summary label="Speed" value={`${config.fps} fps`} />
            <Summary label="Size" value={videoSizeLabel(config.resolution)} />
            <Summary label="Range" value={rangeSummary(selectedPhotoCount, config.start_date, config.end_date)} />
          </div>
          <div className="mt-4 grid gap-2 md:grid-cols-3">
            <PresetButton
              active={config.resolution === "1080_vertical" && config.aspect_ratio === "9:16" && config.morph_mode !== "none"}
              title="Everyday video"
              body="Best starting point: phone-shaped, smooth, and reasonably quick."
              onClick={() => applyPreset("fast")}
            />
            <PresetButton
              active={config.resolution === "original" && config.aspect_ratio === "original"}
              title="Archive quality"
              body="Keeps the original shape and detail. Use when waiting longer is fine."
              onClick={() => applyPreset("full")}
            />
            <PresetButton
              active={config.morph_mode === "none"}
              title="Quick proof"
              body="A fast slideshow pass for checking dates, framing, and selected photos."
              onClick={() => applyPreset("test")}
            />
          </div>
          <div className="mt-4 rounded-md bg-white p-3 shadow-line">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
              <div className="min-w-0">
                <div className="text-sm font-black text-ink">Test a smaller date range</div>
                <p className="mt-1 text-xs font-semibold leading-5 text-ink/55">
                  Pick a slice, preview it quickly, then clear the range for the full timeline.
                </p>
              </div>
              <div className="grid gap-2 md:grid-cols-[10rem_10rem_auto]">
                <div>
                  <Label>From</Label>
                  <Input
                    type="date"
                    min={firstDate || undefined}
                    max={lastDate || undefined}
                    value={config.start_date ?? ""}
                    onChange={(event) => setConfig({ ...config, start_date: event.target.value || null })}
                  />
                </div>
                <div>
                  <Label>To</Label>
                  <Input
                    type="date"
                    min={firstDate || undefined}
                    max={lastDate || undefined}
                    value={config.end_date ?? ""}
                    onChange={(event) => setConfig({ ...config, end_date: event.target.value || null })}
                  />
                </div>
                <Button type="button" variant="ghost" className="self-end" onClick={() => setConfig({ ...config, start_date: null, end_date: null })}>
                  Clear
                </Button>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <RangePresetButton label="First 10" disabled={!activeDates.length} onClick={() => applyRangePreset("first10")} />
              <RangePresetButton label="Latest 10" disabled={!activeDates.length} onClick={() => applyRangePreset("latest10")} />
              <RangePresetButton label="Middle 10" disabled={!activeDates.length} onClick={() => applyRangePreset("middle10")} />
              <RangePresetButton label="First 30" disabled={!activeDates.length} onClick={() => applyRangePreset("first30")} />
              <RangePresetButton label="Latest 30" disabled={!activeDates.length} onClick={() => applyRangePreset("latest30")} />
              <RangePresetButton label="First month" disabled={!activeDates.length} onClick={() => applyRangePreset("firstMonth")} />
              <RangePresetButton label="Latest month" disabled={!activeDates.length} onClick={() => applyRangePreset("latestMonth")} />
            </div>
            <div className="mt-3 text-xs font-black text-ink/55">
              {selectedPhotoCount.toLocaleString()} of {activeDates.length.toLocaleString()} included photos selected
            </div>
          </div>
        </div>

        <button
          type="button"
          className="mt-5 flex min-h-11 items-center gap-2 rounded-md px-2 text-sm font-black text-ink/65 hover:bg-ink/5"
          onClick={() => setShowSettings((value) => !value)}
        >
          <ChevronDown className={cn("h-4 w-4 transition-transform", showSettings && "rotate-180")} />
          Customize video
          <SlidersHorizontal className="h-4 w-4" />
        </button>

        {showSettings ? (
          <div className="mt-3 rounded-lg border border-ink/10 bg-white p-4">
            <div className="grid gap-4 xl:grid-cols-2">
              <ChoiceGroup
                title="How should it move?"
                helper="This controls the feel of the transitions between photos."
                choices={[
                  {
                    title: "Quick cuts",
                    body: "A clean slideshow. Fastest, best for a proof video.",
                    active: currentMotionProfile(config) === "still",
                    icon: <TimerReset className="h-4 w-4" />,
                    onClick: () => applyMotion("still"),
                  },
                  {
                    title: "Natural morph",
                    body: "Recommended. Faces glide without making the render painfully slow.",
                    active: currentMotionProfile(config) === "natural",
                    icon: <Sparkles className="h-4 w-4" />,
                    onClick: () => applyMotion("natural"),
                  },
                  {
                    title: "Extra smooth",
                    body: "More in-between frames. Softer motion, longer render.",
                    active: currentMotionProfile(config) === "extra",
                    icon: <Film className="h-4 w-4" />,
                    onClick: () => applyMotion("extra"),
                  },
                ]}
              />
              <ChoiceGroup
                title="Where will you watch it?"
                helper="This sets both size and shape so you do not have to pair two technical menus."
                choices={[
                  {
                    title: "Phone story",
                    body: "Vertical 1080p. Best for phones and sharing.",
                    active: currentOutputProfile(config) === "phone",
                    icon: <MonitorSmartphone className="h-4 w-4" />,
                    onClick: () => applyOutput("phone"),
                  },
                  {
                    title: "Square post",
                    body: "1080p square. Easy to preview on any screen.",
                    active: currentOutputProfile(config) === "square",
                    icon: <Clapperboard className="h-4 w-4" />,
                    onClick: () => applyOutput("square"),
                  },
                  {
                    title: "Keep original",
                    body: "Full source size and shape. Largest file, slowest render.",
                    active: currentOutputProfile(config) === "archive",
                    icon: <Film className="h-4 w-4" />,
                    onClick: () => applyOutput("archive"),
                  },
                ]}
              />
            </div>

            <div className="mt-5 rounded-md border border-ink/10 bg-paper p-3">
              <div className="text-sm font-black text-ink">Fine tuning</div>
              <p className="mt-1 text-xs font-semibold leading-5 text-ink/50">
                These are here for unusual exports. The choices above are enough for most videos.
              </p>
              <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                <Field label="Face lock style" help="Natural keeps your face steady without reshaping it. Stronger lock is more rigid.">
                <Select value={config.alignment_mode} onChange={(event) => setConfig({ ...config, alignment_mode: event.target.value as RenderConfig["alignment_mode"] })}>
                  <option value="similarity">Natural lock</option>
                  <option value="affine">Stronger lock</option>
                </Select>
              </Field>
              <Field label="Transition engine" help="Face morph is the normal choice. GPU smoother is experimental and needs compatible hardware.">
                <Select value={config.morph_mode} onChange={(event) => setConfig({ ...config, morph_mode: event.target.value as RenderConfig["morph_mode"] })}>
                  <option value="landmark_delaunay">Face morph</option>
                  <option value="rife">GPU smoother</option>
                  <option value="none">No morph</option>
                </Select>
              </Field>
              <Field label="In-between frames" help="More frames means gentler movement between photos and a longer render.">
                <Select value={String(config.intermediate_frames)} onChange={(event) => setConfig({ ...config, intermediate_frames: Number(event.target.value) })}>
                  <option value="0">Preview cuts only</option>
                  <option value="2">Subtle movement</option>
                  <option value="4">Smooth recommended</option>
                  <option value="8">Silky slow</option>
                  <option value="12">Ultra smooth</option>
                </Select>
              </Field>
              <Field label="Video speed" help="30 fps is the normal choice. 60 fps looks smoother and creates a larger file.">
                <Select value={String(config.fps)} onChange={(event) => setConfig({ ...config, fps: Number(event.target.value) })}>
                  <option value="15">15 fps preview</option>
                  <option value="24">24 fps cinematic</option>
                  <option value="30">30 fps standard</option>
                  <option value="60">60 fps extra smooth</option>
                </Select>
              </Field>
              <Field label="Video size" help="Original keeps the aligned photo size. Smaller presets export faster.">
                <Select value={config.resolution} onChange={(event) => setConfig({ ...config, resolution: event.target.value as RenderConfig["resolution"] })}>
                  <option value="1080_vertical">Fast 1080p vertical</option>
                  <option value="1080_square">Fast 1080p square</option>
                  <option value="original">Full quality original size</option>
                  <option value="4k_landscape">Large 4K landscape</option>
                </Select>
              </Field>
              <Field label="Shape" help="Original keeps your photo shape. Square and vertical are social-friendly crops.">
                <Select value={config.aspect_ratio} onChange={(event) => setConfig({ ...config, aspect_ratio: event.target.value as RenderConfig["aspect_ratio"] })}>
                  <option value="original">Original</option>
                  <option value="square">Square</option>
                  <option value="9:16">9:16</option>
                  <option value="16:9">16:9</option>
                </Select>
              </Field>
              <Field label="Video format" help="H.264 works almost everywhere. H.265 is smaller but less universal.">
                <Select value={config.codec} onChange={(event) => setConfig({ ...config, codec: event.target.value as RenderConfig["codec"] })}>
                  <option value="h264">H.264</option>
                  <option value="h265">H.265</option>
                </Select>
              </Field>
              <Field label="Quality" help="Higher quality creates a larger file. Balanced is usually enough for previews.">
                <Select value={String(config.crf)} onChange={(event) => setConfig({ ...config, crf: Number(event.target.value) })}>
                  <option value="24">Preview smaller file</option>
                  <option value="20">Balanced</option>
                  <option value="18">High quality</option>
                  <option value="14">Very high quality</option>
                </Select>
              </Field>
              <Field label="Date label" help="Choose how the date appears on the video.">
                <Select value={config.date_overlay.format} onChange={(event) => setConfig({ ...config, date_overlay: { ...config.date_overlay, format: event.target.value } })}>
                  <option value="%b %Y">May 2026</option>
                  <option value="%B %Y">May 2026, full month</option>
                  <option value="%Y">2026 only</option>
                  <option value="%b %d, %Y">May 05, 2026</option>
                  <option value="%Y-%m-%d">2026-05-05</option>
                </Select>
              </Field>
              <Field label="Overlay position" help="Where the date appears on the video.">
                <Select value={config.date_overlay.position} onChange={(event) => setConfig({ ...config, date_overlay: { ...config.date_overlay, position: event.target.value as RenderConfig["date_overlay"]["position"] } })}>
                  <option value="bottom-right">Bottom right</option>
                  <option value="bottom-left">Bottom left</option>
                  <option value="top-right">Top right</option>
                  <option value="top-left">Top left</option>
                </Select>
              </Field>
              <Field label="Output path" help="Leave blank to save into ~/.selfietl/exports.">
                <Input value={config.output_path ?? ""} placeholder="~/Movies/selfietl.mp4" onChange={(event) => setConfig({ ...config, output_path: event.target.value || null })} />
              </Field>
              <Field label="Audio path" help="Optional song or audio file to attach.">
                <Input value={config.audio_path ?? ""} placeholder="Optional local audio file" onChange={(event) => setConfig({ ...config, audio_path: event.target.value || null })} />
              </Field>
              </div>
            </div>
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <label className="flex min-h-11 items-center gap-2 rounded-md bg-paper px-3 text-sm font-bold shadow-line">
                <input
                  type="checkbox"
                  checked={config.color_normalize}
                  onChange={(event) => setConfig({ ...config, color_normalize: event.target.checked })}
                />
                Normalize color
              </label>
              <label className="flex min-h-11 items-center gap-2 rounded-md bg-paper px-3 text-sm font-bold shadow-line">
                <input
                  type="checkbox"
                  checked={config.date_overlay.enabled}
                  onChange={(event) => setConfig({ ...config, date_overlay: { ...config.date_overlay, enabled: event.target.checked } })}
                />
                Date overlay
              </label>
            </div>
          </div>
        ) : null}
        {error ? <div className="mt-4 rounded-md bg-coral/10 p-3 text-sm font-semibold text-coral">{error}</div> : null}
      </Panel>

      <Panel>
        <div className="flex items-center gap-2">
          <Clapperboard className="h-5 w-5 text-coral" />
          <h3 className="font-black text-ink">Latest output</h3>
        </div>
        {latestDone ? (
          <div className="mt-4">
            <video className="aspect-video w-full rounded-md bg-ink" controls src={`/api/renders/${latestDone.id}/file`} />
            <div className="mt-3 break-all rounded-md bg-white p-3 font-mono text-xs text-ink/70 shadow-line">{latestDone.output_path}</div>
          </div>
        ) : (
          <p className="mt-4 text-sm font-semibold text-ink/55">No completed renders yet.</p>
        )}
      </Panel>

      <div className="xl:col-span-2">
        <JobStatusPanel job={visibleJob} onCancel={visibleJob ? () => api.cancelJob(visibleJob.id) : undefined} />
      </div>
    </PageFrame>
  );
}

function PresetButton({
  active,
  title,
  body,
  onClick,
}: {
  active: boolean;
  title: string;
  body: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "min-h-24 rounded-md border p-3 text-left transition",
        active ? "border-teal bg-white shadow-line" : "border-ink/10 bg-paper hover:border-teal/40",
      )}
    >
      <div className="text-sm font-black text-ink">{title}</div>
      <div className="mt-1 text-xs font-semibold leading-5 text-ink/55">{body}</div>
    </button>
  );
}

function RangePresetButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="min-h-9 rounded-md border border-ink/10 bg-paper px-3 text-xs font-black text-ink/65 shadow-line transition hover:border-teal/45 hover:bg-white hover:text-ink disabled:cursor-not-allowed disabled:opacity-45"
    >
      {label}
    </button>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-white p-3 shadow-line">
      <div className="text-[0.68rem] font-bold uppercase tracking-[0.08em] text-ink/45">{label}</div>
      <div className="mt-1 truncate text-sm font-black text-ink">{value}</div>
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

function ChoiceGroup({ title, helper, choices }: { title: string; helper: string; choices: Choice[] }) {
  return (
    <div>
      <div className="text-sm font-black text-ink">{title}</div>
      <p className="mt-1 text-xs font-semibold leading-5 text-ink/50">{helper}</p>
      <div className="mt-3 grid gap-2">
        {choices.map((choice) => (
          <button
            key={choice.title}
            type="button"
            onClick={choice.onClick}
            className={cn(
              "flex min-h-20 items-start gap-3 rounded-md border p-3 text-left transition",
              choice.active ? "border-teal bg-teal/10 shadow-line" : "border-ink/10 bg-paper hover:border-teal/40 hover:bg-white",
            )}
          >
            <span className={cn("mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md", choice.active ? "bg-teal text-paper" : "bg-white text-ink/55")}>
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

function videoSizeLabel(value: RenderConfig["resolution"]) {
  const labels = {
    original: "Full quality",
    "1080_square": "1080p square",
    "1080_vertical": "1080p vertical",
    "4k_landscape": "4K landscape",
  };
  return labels[value];
}

function motionLabel(config: RenderConfig) {
  if (config.morph_mode === "none" || config.intermediate_frames === 0) return "Cuts only";
  if (config.intermediate_frames <= 2) return "Subtle movement";
  if (config.intermediate_frames <= 4) return "Smooth";
  if (config.intermediate_frames <= 8) return "Silky";
  return "Ultra smooth";
}

type MotionProfile = "still" | "natural" | "extra";
type OutputProfile = "phone" | "square" | "archive";

function currentMotionProfile(config: RenderConfig): MotionProfile {
  if (config.morph_mode === "none" || config.intermediate_frames === 0) return "still";
  if (config.intermediate_frames >= 8) return "extra";
  return "natural";
}

function currentOutputProfile(config: RenderConfig): OutputProfile {
  if (config.resolution === "original" && config.aspect_ratio === "original") return "archive";
  if (config.aspect_ratio === "square" || config.resolution === "1080_square") return "square";
  return "phone";
}

type RenderStats = {
  timeline: Array<{ date: string; skipped: boolean }>;
};

type RangePreset = "first10" | "latest10" | "middle10" | "first30" | "latest30" | "firstMonth" | "latestMonth";

function addDays(day: string, offset: number) {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}

function countDatesInRange(dates: string[], startDate?: string | null, endDate?: string | null) {
  if (!startDate && !endDate) return dates.length;
  return dates.filter((date) => {
    const day = date.slice(0, 10);
    if (startDate && day < startDate) return false;
    if (endDate && day > endDate) return false;
    return true;
  }).length;
}

function rangeSummary(count: number, startDate?: string | null, endDate?: string | null) {
  if (!startDate && !endDate) return `${count.toLocaleString()} photos`;
  const label = startDate && endDate ? `${startDate} to ${endDate}` : startDate ? `from ${startDate}` : `to ${endDate}`;
  return `${count.toLocaleString()} photos, ${label}`;
}

function Field({ label, help, children }: { label: string; help?: string; children: React.ReactNode }) {
  return (
    <div>
      <Label>{label}</Label>
      <div className="mt-1">{children}</div>
      {help ? <p className="mt-1 text-xs font-semibold leading-5 text-ink/45">{help}</p> : null}
    </div>
  );
}
