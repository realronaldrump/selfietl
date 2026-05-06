import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clapperboard, Film, Play } from "lucide-react";
import { api, type JobStatus, type Project, type RenderConfig } from "@/api/client";
import { JobStatus as JobStatusPanel } from "@/components/JobStatus";
import { Button, Input, Label, Panel, Select } from "@/components/ui";
import { useJobEvents } from "@/hooks/useJobEvents";

const defaultConfig: RenderConfig = {
  alignment_mode: "similarity",
  morph_mode: "landmark_delaunay",
  intermediate_frames: 8,
  color_normalize: false,
  fps: 30,
  resolution: "original",
  aspect_ratio: "original",
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
  const renderMutation = useMutation({
    mutationFn: () => api.render(project.id, config),
    onSuccess: (started) => {
      setError(null);
      setJobId(started.job_id);
    },
    onError: (err) => setError(err instanceof Error ? err.message : String(err)),
  });

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
      <Panel>
        <div className="flex items-center gap-2">
          <Film className="h-5 w-5 text-teal" />
          <h2 className="text-xl font-black text-ink">Render export</h2>
        </div>

        <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <Field label="Alignment">
            <Select value={config.alignment_mode} onChange={(event) => setConfig({ ...config, alignment_mode: event.target.value as RenderConfig["alignment_mode"] })}>
              <option value="similarity">Similarity</option>
              <option value="affine">Affine</option>
            </Select>
          </Field>
          <Field label="Morph">
            <Select value={config.morph_mode} onChange={(event) => setConfig({ ...config, morph_mode: event.target.value as RenderConfig["morph_mode"] })}>
              <option value="landmark_delaunay">Landmark Delaunay</option>
              <option value="rife">RIFE fallback</option>
              <option value="none">None</option>
            </Select>
          </Field>
          <Field label="Intermediate frames">
            <Input type="number" min={0} max={60} value={config.intermediate_frames} onChange={(event) => setConfig({ ...config, intermediate_frames: Number(event.target.value) })} />
          </Field>
          <Field label="FPS">
            <Input type="number" min={1} max={120} value={config.fps} onChange={(event) => setConfig({ ...config, fps: Number(event.target.value) })} />
          </Field>
          <Field label="Resolution">
            <Select value={config.resolution} onChange={(event) => setConfig({ ...config, resolution: event.target.value as RenderConfig["resolution"] })}>
              <option value="original">Original</option>
              <option value="1080_square">1080 square</option>
              <option value="1080_vertical">1080 vertical</option>
              <option value="4k_landscape">4K landscape</option>
            </Select>
          </Field>
          <Field label="Aspect">
            <Select value={config.aspect_ratio} onChange={(event) => setConfig({ ...config, aspect_ratio: event.target.value as RenderConfig["aspect_ratio"] })}>
              <option value="original">Original</option>
              <option value="square">Square</option>
              <option value="9:16">9:16</option>
              <option value="16:9">16:9</option>
            </Select>
          </Field>
          <Field label="Codec">
            <Select value={config.codec} onChange={(event) => setConfig({ ...config, codec: event.target.value as RenderConfig["codec"] })}>
              <option value="h264">H.264</option>
              <option value="h265">H.265</option>
            </Select>
          </Field>
          <Field label="CRF">
            <Input type="number" min={0} max={51} value={config.crf} onChange={(event) => setConfig({ ...config, crf: Number(event.target.value) })} />
          </Field>
          <Field label="Date format">
            <Input value={config.date_overlay.format} onChange={(event) => setConfig({ ...config, date_overlay: { ...config.date_overlay, format: event.target.value } })} />
          </Field>
          <Field label="Overlay position">
            <Select value={config.date_overlay.position} onChange={(event) => setConfig({ ...config, date_overlay: { ...config.date_overlay, position: event.target.value as RenderConfig["date_overlay"]["position"] } })}>
              <option value="bottom-right">Bottom right</option>
              <option value="bottom-left">Bottom left</option>
              <option value="top-right">Top right</option>
              <option value="top-left">Top left</option>
            </Select>
          </Field>
          <Field label="Output path">
            <Input value={config.output_path ?? ""} placeholder="~/Movies/selfietl.mp4" onChange={(event) => setConfig({ ...config, output_path: event.target.value || null })} />
          </Field>
          <Field label="Audio path">
            <Input value={config.audio_path ?? ""} placeholder="Optional local audio file" onChange={(event) => setConfig({ ...config, audio_path: event.target.value || null })} />
          </Field>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-4">
          <label className="flex min-h-11 items-center gap-2 rounded-md bg-white px-3 text-sm font-bold shadow-line">
            <input
              type="checkbox"
              checked={config.color_normalize}
              onChange={(event) => setConfig({ ...config, color_normalize: event.target.checked })}
            />
            Normalize color
          </label>
          <label className="flex min-h-11 items-center gap-2 rounded-md bg-white px-3 text-sm font-bold shadow-line">
            <input
              type="checkbox"
              checked={config.date_overlay.enabled}
              onChange={(event) => setConfig({ ...config, date_overlay: { ...config.date_overlay, enabled: event.target.checked } })}
            />
            Date overlay
          </label>
          <Button disabled={renderMutation.isPending || project.active_count === 0} onClick={() => renderMutation.mutate()}>
            <Play className="h-4 w-4" />
            Start render
          </Button>
        </div>
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Label>{label}</Label>
      <div className="mt-1">{children}</div>
    </div>
  );
}
