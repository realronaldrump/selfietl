import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Clapperboard, Film, Play, SlidersHorizontal, XCircle } from "lucide-react";
import { api, type JobStatus, type Project, type RenderConfig } from "@/api/client";
import { JobStatus as JobStatusPanel } from "@/components/JobStatus";
import { Button, Input, Label, Panel, Select, cn } from "@/components/ui";
import { useJobEvents } from "@/hooks/useJobEvents";

const defaultConfig: RenderConfig = {
  alignment_mode: "similarity",
  morph_mode: "landmark_delaunay",
  intermediate_frames: 4,
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
  const latestDone = useMemo(() => historyQuery.data?.find((render) => render.status === "done"), [historyQuery.data]);
  const onTerminal = useCallback(
    (_job: JobStatus) => {
      queryClient.invalidateQueries({ queryKey: ["renders", project.id] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    [project.id, queryClient],
  );
  const job = useJobEvents(jobId, onTerminal);
  const activeJob = Boolean(job && ["queued", "running"].includes(job.status));
  const renderMutation = useMutation({
    mutationFn: () => api.render(project.id, config),
    onSuccess: (started) => {
      setError(null);
      setJobId(started.job_id);
    },
    onError: (err) => setError(err instanceof Error ? err.message : String(err)),
  });

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

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
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
              {activeJob && jobId ? (
                <Button type="button" variant="danger" onClick={() => api.cancelJob(jobId)}>
                  <XCircle className="h-4 w-4" />
                  Cancel
                </Button>
              ) : null}
              <Button disabled={renderMutation.isPending || activeJob || project.active_count === 0} onClick={() => renderMutation.mutate()}>
                <Play className="h-4 w-4" />
                Create video
              </Button>
            </div>
          </div>
          <div className="mt-4 grid gap-2 md:grid-cols-4">
            <Summary label="Motion" value={config.morph_mode === "none" ? "Cuts only" : `${config.intermediate_frames} morph frames`} />
            <Summary label="Speed" value={`${config.fps} fps`} />
            <Summary label="Size" value={videoSizeLabel(config.resolution)} />
            <Summary label="Format" value={config.codec.toUpperCase()} />
          </div>
          <div className="mt-4 grid gap-2 md:grid-cols-3">
            <PresetButton
              active={config.resolution === "1080_vertical" && config.aspect_ratio === "9:16" && config.morph_mode !== "none"}
              title="Fast 1080p"
              body="Recommended. Smooth vertical MP4 without full-resolution morph cost."
              onClick={() => applyPreset("fast")}
            />
            <PresetButton
              active={config.resolution === "original" && config.aspect_ratio === "original"}
              title="Full quality"
              body="Original-size output. Use when you are okay waiting much longer."
              onClick={() => applyPreset("full")}
            />
            <PresetButton
              active={config.morph_mode === "none"}
              title="Quick test"
              body="No morphing. Useful for checking dates and framing first."
              onClick={() => applyPreset("test")}
            />
          </div>
        </div>

        <button
          type="button"
          className="mt-5 flex min-h-11 items-center gap-2 rounded-md px-2 text-sm font-black text-ink/65 hover:bg-ink/5"
          onClick={() => setShowSettings((value) => !value)}
        >
          <ChevronDown className={cn("h-4 w-4 transition-transform", showSettings && "rotate-180")} />
          Video options
          <SlidersHorizontal className="h-4 w-4" />
        </button>

        {showSettings ? (
          <div className="mt-3 rounded-lg border border-ink/10 bg-white p-4">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              <Field label="Face lock style" help="Natural keeps your face steady without reshaping it. Stronger lock is more rigid.">
                <Select value={config.alignment_mode} onChange={(event) => setConfig({ ...config, alignment_mode: event.target.value as RenderConfig["alignment_mode"] })}>
                  <option value="similarity">Natural lock</option>
                  <option value="affine">Stronger lock</option>
                </Select>
              </Field>
              <Field label="Transition style" help="Smooth face movement creates in-between frames instead of a slideshow.">
                <Select value={config.morph_mode} onChange={(event) => setConfig({ ...config, morph_mode: event.target.value as RenderConfig["morph_mode"] })}>
                  <option value="landmark_delaunay">Smooth face movement</option>
                  <option value="rife">GPU smoother</option>
                  <option value="none">No morph</option>
                </Select>
              </Field>
              <Field label="Smoothness" help="More frames makes transitions softer and slower.">
                <Input type="number" min={0} max={60} value={config.intermediate_frames} onChange={(event) => setConfig({ ...config, intermediate_frames: Number(event.target.value) })} />
              </Field>
              <Field label="Video speed" help="30 fps is the normal video default.">
                <Input type="number" min={1} max={120} value={config.fps} onChange={(event) => setConfig({ ...config, fps: Number(event.target.value) })} />
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
              <Field label="Quality" help="Lower means sharper and larger. 18 is high quality.">
                <Input type="number" min={0} max={51} value={config.crf} onChange={(event) => setConfig({ ...config, crf: Number(event.target.value) })} />
              </Field>
              <Field label="Date format" help="%b %Y becomes labels like May 2026.">
                <Input value={config.date_overlay.format} onChange={(event) => setConfig({ ...config, date_overlay: { ...config.date_overlay, format: event.target.value } })} />
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
        <JobStatusPanel job={job} onCancel={jobId ? () => api.cancelJob(jobId) : undefined} />
      </div>
    </div>
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

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-white p-3 shadow-line">
      <div className="text-[0.68rem] font-bold uppercase tracking-[0.08em] text-ink/45">{label}</div>
      <div className="mt-1 truncate text-sm font-black text-ink">{value}</div>
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

function Field({ label, help, children }: { label: string; help?: string; children: React.ReactNode }) {
  return (
    <div>
      <Label>{label}</Label>
      <div className="mt-1">{children}</div>
      {help ? <p className="mt-1 text-xs font-semibold leading-5 text-ink/45">{help}</p> : null}
    </div>
  );
}
