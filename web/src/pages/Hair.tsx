import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CalendarPlus,
  Check,
  Download,
  Film,
  Loader2,
  Play,
  RefreshCw,
  Scissors,
  Sparkles,
  Undo2,
  X,
} from "lucide-react";
import { api, apiUrl, type HairFrame, type HaircutEvent, type JobStatus, type Project } from "@/api/client";
import { Badge, Button, Input, PageFrame, Panel, ProgressBar, cn } from "@/components/ui";
import { useJobEvents } from "@/hooks/useJobEvents";

type Range = "6m" | "1y" | "all";

export function Hair({ project }: { project?: Project | null }) {
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects, enabled: project === undefined });
  const activeProject = project === undefined ? projectsQuery.data?.[0] ?? null : project;
  const queryClient = useQueryClient();
  const recomputeAttempt = useRef<string | null>(null);
  const exportAttempt = useRef<string | null>(null);
  const [range, setRange] = useState<Range>("all");
  const [speed, setSpeed] = useState(1);
  const [selectedHash, setSelectedHash] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobKind, setJobKind] = useState<"analysis" | "export" | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [manualDate, setManualDate] = useState("");
  const videoRef = useRef<HTMLVideoElement>(null);

  const hairQuery = useQuery({
    queryKey: ["hair", activeProject?.id],
    queryFn: () => api.hair(activeProject!.id),
    enabled: Boolean(activeProject),
    refetchInterval: (query) => ["not_ready", "stale"].includes(query.state.data?.status ?? "") ? 2500 : false,
  });

  const recomputeMutation = useMutation({
    mutationFn: () => api.recomputeHair(activeProject!.id),
    onSuccess: (job) => {
      setJobError(null);
      setJobId(job.job_id);
      setJobKind("analysis");
    },
  });
  const exportMutation = useMutation({
    mutationFn: () => api.exportHair(activeProject!.id, exportPayload(range, speed)),
    onSuccess: (job) => {
      setJobError(null);
      setJobId(job.job_id);
      setJobKind("export");
    },
  });
  const onTerminal = useCallback((job: JobStatus) => {
    queryClient.invalidateQueries({ queryKey: ["hair", activeProject?.id] });
    setJobError(job.status === "failed" ? job.error ?? "Hair processing failed" : null);
    setJobId(null);
    setJobKind(null);
  }, [activeProject?.id, queryClient]);
  const activeJob = useJobEvents(jobId, onTerminal);

  useEffect(() => {
    const manifest = hairQuery.data;
    if (!activeProject || !manifest || !["not_ready", "stale"].includes(manifest.status)) return;
    const key = `${activeProject.id}:${manifest.analysis_revision ?? manifest.status}`;
    if (recomputeAttempt.current === key || recomputeMutation.isPending || jobId) return;
    recomputeAttempt.current = key;
    recomputeMutation.mutate();
  }, [activeProject, hairQuery.data, jobId, recomputeMutation]);

  const frames = useMemo(() => filterFrames(hairQuery.data?.frames ?? [], range), [hairQuery.data?.frames, range]);
  const included = frames.filter((frame) => frame.eligible && !frame.excluded);
  const selected = frames.find((frame) => frame.hash === selectedHash) ?? included[included.length - 1] ?? frames[frames.length - 1] ?? null;

  useEffect(() => {
    if (selected && selected.hash !== selectedHash) setSelectedHash(selected.hash);
  }, [selected, selectedHash]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = playbackRate(hairQuery.data?.latest_export?.config.seconds_per_selfie, speed);
  }, [speed, hairQuery.data?.latest_export?.id]);

  useEffect(() => {
    const manifest = hairQuery.data;
    if (!activeProject || !manifest || manifest.status !== "ready" || manifest.coverage.included < 2) return;
    if (manifest.latest_export && !manifest.latest_export.stale) return;
    const key = `${activeProject.id}:${manifest.analysis_revision}:all:1`;
    if (exportAttempt.current === key || exportMutation.isPending || jobId) return;
    exportAttempt.current = key;
    exportMutation.mutate();
  }, [activeProject, exportMutation, hairQuery.data, jobId]);

  const frameMutation = useMutation({
    mutationFn: ({ hash, excluded }: { hash: string; excluded: boolean }) => api.updateHairFrame(hash, excluded),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["hair", activeProject?.id] }),
  });
  const haircutMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: { event_date?: string; status?: HaircutEvent["status"] } }) => api.updateHaircut(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["hair", activeProject?.id] }),
  });
  const addHaircutMutation = useMutation({
    mutationFn: () => api.addHaircut(activeProject!.id, manualDate),
    onSuccess: () => {
      setManualDate("");
      queryClient.invalidateQueries({ queryKey: ["hair", activeProject?.id] });
    },
  });

  if (!activeProject) return <EmptyHair title="No selfie project yet" detail="Create a project before building hair history." />;
  if (hairQuery.isLoading) return <HairLoading />;
  if (hairQuery.isError) return <EmptyHair title="Hair history could not load" detail={hairQuery.error.message} action={<Button onClick={() => hairQuery.refetch()}>Retry</Button>} />;
  const manifest = hairQuery.data;
  if (!manifest || manifest.status === "not_ready") {
    return <HairPreparing job={activeJob} error={jobError ?? recomputeMutation.error?.message} onRetry={() => recomputeMutation.mutate()} />;
  }
  if (manifest.status === "insufficient") {
    return <EmptyHair title="Add a few clear selfies" detail="Hair animation begins after at least two included, face-anchored selfie days." />;
  }

  const latestExport = manifest.latest_export;
  return (
    <PageFrame size="wide" className="space-y-5">
      <section className="relative overflow-hidden rounded-xl bg-white shadow-line ring-1 ring-ink/10">
        <div className="absolute inset-y-0 left-0 w-2 bg-ink" />
        <div className="grid gap-6 p-5 pl-7 md:grid-cols-[1fr_auto] md:items-end md:p-8 md:pl-10">
          <div>
            <div className="flex flex-wrap gap-2">
              <Badge tone="good">Local · black & white</Badge>
              <Badge>{manifest.coverage.included} included days</Badge>
              {manifest.status === "stale" || latestExport?.stale ? <Badge tone="warn">Updating</Badge> : null}
            </div>
            <div className="mt-5 flex items-center gap-3">
              <Scissors className="h-9 w-9 text-ink" strokeWidth={2.4} />
              <h1 className="text-4xl font-black tracking-[-0.055em] text-ink md:text-6xl">Hair, over time.</h1>
            </div>
            <p className="mt-3 max-w-2xl text-sm font-semibold leading-6 text-ink/55">
              Your face stays anchored. Only the silhouette changes—one clear beat for every selfie day.
            </p>
          </div>
          <div className="font-mono text-5xl font-black tracking-[-0.08em] text-ink/12 md:text-7xl">{String(manifest.coverage.included).padStart(3, "0")}</div>
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.55fr)_minmax(19rem,0.75fr)]">
        <Panel className="overflow-hidden p-0 ring-1 ring-ink/8">
          <div className="flex flex-col gap-3 border-b border-ink/10 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="flex items-center gap-2"><Film className="h-4 w-4" /><h2 className="font-black">Daily silhouette</h2></div>
              <p className="mt-1 text-sm font-semibold text-ink/50">Actual days hold still; the black edge moves between them.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Segmented values={["6m", "1y", "all"] as Range[]} value={range} onChange={setRange} label={(value) => value === "all" ? "All" : value.toUpperCase()} />
              <Segmented values={[0.5, 1, 2]} value={speed} onChange={setSpeed} label={(value) => `${value}×`} />
            </div>
          </div>

          <div className="relative aspect-[4/5] max-h-[72vh] w-full overflow-hidden bg-white">
            {latestExport ? (
              <video
                ref={videoRef}
                key={latestExport.id}
                src={apiUrl(latestExport.playback_url)}
                controls
                playsInline
                className="h-full w-full bg-white object-contain"
                onLoadedMetadata={(event) => { event.currentTarget.playbackRate = playbackRate(latestExport.config.seconds_per_selfie, speed); }}
              />
            ) : selected ? (
              <img src={apiUrl(selected.composite_url)} alt={`Hair silhouette on ${selected.date}`} className="h-full w-full object-contain" />
            ) : (
              <div className="grid h-full place-items-center text-sm font-bold text-ink/45">No included hair frames</div>
            )}
            {activeJob && ["queued", "running"].includes(activeJob.status) ? (
              <div className="absolute inset-x-4 bottom-16 rounded-md border border-ink/10 bg-white/95 p-3 shadow-lg backdrop-blur">
                <div className="flex items-center justify-between gap-3 text-xs font-black uppercase tracking-[0.12em]">
                  <span>{jobKind === "analysis" ? "Tracing silhouettes" : "Building movie"}</span><span>{Math.round(activeJob.progress * 100)}%</span>
                </div>
                <div className="mt-2"><ProgressBar value={activeJob.progress} /></div>
              </div>
            ) : null}
          </div>

          <div className="flex flex-col gap-3 border-t border-ink/10 bg-bone/70 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm font-bold text-ink/55">{included.length} frames · {speed === 1 ? "one second" : `${(1 / speed).toFixed(1)} seconds`} per selfie</div>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => exportMutation.mutate()} disabled={exportMutation.isPending || Boolean(jobId) || included.length < 2}>
                {exportMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Rebuild
              </Button>
              {latestExport ? <a href={apiUrl(latestExport.file_url)} download className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-ink px-4 text-sm font-semibold text-white"><Download className="h-4 w-4" /> MP4</a> : null}
            </div>
          </div>
          {jobError ? <div className="border-t border-coral/20 bg-coral/10 p-3 text-sm font-semibold text-coral">{jobError}</div> : null}
        </Panel>

        <Panel className="self-start overflow-hidden p-0 ring-1 ring-ink/8">
          <div className="border-b border-ink/10 p-4">
            <div className="text-xs font-black uppercase tracking-[0.16em] text-ink/45">Selected day</div>
            <div className="mt-1 text-2xl font-black tracking-tight">{selected ? formatDate(selected.date) : "None"}</div>
          </div>
          {selected ? (
            <div>
              <div className="grid aspect-[4/5] place-items-center overflow-hidden bg-white">
                <img src={apiUrl(selected.composite_url)} alt={`Selected hair silhouette from ${selected.date}`} className="h-full w-full object-contain" />
              </div>
              <div className="space-y-3 border-t border-ink/10 p-4">
                <div className="flex flex-wrap gap-2">
                  <Badge tone={selected.quality >= 0.65 ? "good" : "warn"}>{Math.round(selected.quality * 100)}% mask confidence</Badge>
                  {selected.excluded ? <Badge tone="bad">Excluded</Badge> : <Badge>Included</Badge>}
                </div>
                {selected.reasons.length ? <div className="rounded-md bg-amber/12 p-3 text-xs font-semibold leading-5 text-ink/65"><AlertTriangle className="mb-1 h-4 w-4 text-amber" />{selected.reasons.map(reasonLabel).join(" · ")}</div> : null}
                <Button
                  variant={selected.excluded ? "secondary" : "danger"}
                  className="w-full"
                  disabled={frameMutation.isPending}
                  onClick={() => frameMutation.mutate({ hash: selected.hash, excluded: !selected.excluded })}
                >
                  {selected.excluded ? <Undo2 className="h-4 w-4" /> : <X className="h-4 w-4" />}
                  {selected.excluded ? "Restore this day" : "Exclude this hair frame"}
                </Button>
              </div>
            </div>
          ) : null}
        </Panel>
      </div>

      <Panel className="overflow-hidden p-0 ring-1 ring-ink/8">
        <div className="border-b border-ink/10 p-4">
          <h2 className="font-black">Selfie-day contact sheet</h2>
          <p className="mt-1 text-sm font-semibold text-ink/50">Tap any frame to inspect the automatic edge.</p>
        </div>
        <div className="flex snap-x gap-2 overflow-x-auto p-4">
          {frames.map((frame) => (
            <button
              type="button"
              key={frame.hash}
              onClick={() => setSelectedHash(frame.hash)}
              className={cn(
                "min-h-24 w-20 shrink-0 snap-start overflow-hidden rounded-md border bg-white text-left transition",
                selected?.hash === frame.hash ? "border-ink ring-2 ring-ink/15" : "border-ink/10",
                frame.excluded && "opacity-40",
              )}
            >
              <img src={apiUrl(frame.composite_url)} alt="" className="aspect-[4/5] w-full object-cover" />
              <span className="block truncate px-1.5 py-1 font-mono text-[0.62rem] font-bold">{compactDate(frame.date)}</span>
            </button>
          ))}
        </div>
      </Panel>

      <HaircutLedger
        events={manifest.haircuts}
        manualDate={manualDate}
        onManualDate={setManualDate}
        onAdd={() => addHaircutMutation.mutate()}
        adding={addHaircutMutation.isPending}
        onUpdate={(id, payload) => haircutMutation.mutate({ id, payload })}
        updating={haircutMutation.isPending}
      />
    </PageFrame>
  );
}

function HaircutLedger({ events, manualDate, onManualDate, onAdd, adding, onUpdate, updating }: {
  events: HaircutEvent[];
  manualDate: string;
  onManualDate: (value: string) => void;
  onAdd: () => void;
  adding: boolean;
  onUpdate: (id: number, payload: { event_date?: string; status?: HaircutEvent["status"] }) => void;
  updating: boolean;
}) {
  return (
    <Panel className="overflow-hidden p-0 ring-1 ring-ink/8">
      <div className="grid gap-4 border-b border-ink/10 p-4 md:grid-cols-[1fr_auto] md:items-end">
        <div><div className="flex items-center gap-2"><Scissors className="h-4 w-4" /><h2 className="font-black">Haircut ledger</h2></div><p className="mt-1 text-sm font-semibold text-ink/50">Suggestions stay guesses until you confirm them.</p></div>
        <div className="flex gap-2"><Input aria-label="Haircut date" type="date" value={manualDate} onChange={(event) => onManualDate(event.target.value)} className="w-auto" /><Button onClick={onAdd} disabled={!manualDate || adding}><CalendarPlus className="h-4 w-4" /> Add</Button></div>
      </div>
      {events.length ? (
        <div className="divide-y divide-ink/8">
          {events.map((event) => (
            <div key={event.id} className="grid gap-3 p-4 sm:grid-cols-[auto_1fr_auto] sm:items-center">
              <div className={cn("grid h-11 w-11 place-items-center rounded-full", event.status === "confirmed" ? "bg-ink text-white" : "bg-amber/20 text-ink")}><Scissors className="h-4 w-4" /></div>
              <div><div className="flex flex-wrap items-center gap-2"><span className="font-black">{formatDate(event.event_date)}</span><Badge tone={event.status === "confirmed" ? "good" : "warn"}>{event.status === "confirmed" ? "Confirmed" : "Possible cut"}</Badge>{event.source === "manual" ? <Badge>Manual</Badge> : null}</div><div className="mt-1 text-xs font-semibold text-ink/45">{event.source === "automatic" ? "Suggested from a persistent silhouette change" : "Added by you"}</div></div>
              <div className="flex flex-wrap gap-2">
                <Input aria-label={`Date for haircut ${event.id}`} type="date" defaultValue={event.event_date} onBlur={(e) => e.currentTarget.value !== event.event_date && onUpdate(event.id, { event_date: e.currentTarget.value })} className="w-auto" />
                {event.status !== "confirmed" ? <Button size="icon" aria-label="Confirm haircut" disabled={updating} onClick={() => onUpdate(event.id, { status: "confirmed" })}><Check className="h-4 w-4" /></Button> : null}
                <Button size="icon" variant="ghost" aria-label="Dismiss haircut" disabled={updating} onClick={() => onUpdate(event.id, { status: "dismissed" })}><X className="h-4 w-4" /></Button>
              </div>
            </div>
          ))}
        </div>
      ) : <div className="p-6 text-sm font-semibold text-ink/50">No confirmed or suggested haircuts yet.</div>}
    </Panel>
  );
}

function Segmented<T extends string | number>({ values, value, onChange, label }: { values: T[]; value: T; onChange: (value: T) => void; label: (value: T) => string }) {
  return <div className="grid auto-cols-fr grid-flow-col rounded-md border border-ink/10 bg-bone p-1">{values.map((item) => <button type="button" key={item} onClick={() => onChange(item)} className={cn("min-h-10 min-w-12 rounded px-2 text-xs font-black", value === item ? "bg-ink text-white" : "text-ink/50")}>{label(item)}</button>)}</div>;
}

function HairPreparing({ job, error, onRetry }: { job: JobStatus | null; error?: string; onRetry: () => void }) {
  return <PageFrame size="narrow"><Panel className="overflow-hidden p-0 text-center"><div className="bg-white p-8"><Sparkles className="mx-auto h-10 w-10" /><h1 className="mt-4 text-3xl font-black tracking-tight">Tracing your hair archive</h1><p className="mx-auto mt-2 max-w-lg text-sm font-semibold leading-6 text-ink/55">Each selfie stays local. The first pass creates one reusable silhouette mask per photo.</p>{job ? <div className="mx-auto mt-5 max-w-md"><ProgressBar value={job.progress} /><div className="mt-2 text-xs font-black uppercase tracking-[0.14em]">{Math.round(job.progress * 100)}% · {job.message}</div></div> : null}{error ? <div className="mt-4 text-sm font-semibold text-coral">{error}<div className="mt-3"><Button onClick={onRetry}>Retry</Button></div></div> : null}</div><div className="h-3 bg-ink" /></Panel></PageFrame>;
}

function HairLoading() { return <PageFrame size="narrow"><Panel className="grid min-h-72 place-items-center"><div className="text-center"><Loader2 className="mx-auto h-7 w-7 animate-spin" /><div className="mt-3 text-sm font-black">Loading hair history</div></div></Panel></PageFrame>; }

function EmptyHair({ title, detail, action }: { title: string; detail: string; action?: React.ReactNode }) { return <PageFrame size="narrow"><Panel className="text-center"><Scissors className="mx-auto h-8 w-8" /><h1 className="mt-4 text-2xl font-black">{title}</h1><p className="mt-2 text-sm font-semibold text-ink/55">{detail}</p>{action ? <div className="mt-4">{action}</div> : null}</Panel></PageFrame>; }

function filterFrames(frames: HairFrame[], range: Range) {
  if (range === "all" || !frames.length) return frames;
  const latest = new Date(`${frames[frames.length - 1].date}T12:00:00`);
  latest.setMonth(latest.getMonth() - (range === "6m" ? 6 : 12));
  return frames.filter((frame) => new Date(`${frame.date}T12:00:00`) >= latest);
}

function exportPayload(range: Range, speed: number) {
  const payload: { start_date?: string; seconds_per_selfie: number } = { seconds_per_selfie: 1 / speed };
  if (range !== "all") {
    const date = new Date();
    date.setMonth(date.getMonth() - (range === "6m" ? 6 : 12));
    payload.start_date = date.toISOString().slice(0, 10);
  }
  return payload;
}

function playbackRate(encodedSeconds: number | undefined, speed: number) { return Math.max(0.25, Math.min(4, (encodedSeconds ?? 1) * speed)); }

function formatDate(value: string) { return new Date(`${value}T12:00:00`).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" }); }
function compactDate(value: string) { return new Date(`${value}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }); }
function reasonLabel(value: string) { return ({ low_hair_confidence: "Edge confidence is low", hair_touches_frame_edge: "Hair reaches the photo edge", implausible_hair_area: "Mask size looks unusual", alignment_not_ready: "Waiting for face alignment" } as Record<string, string>)[value] ?? value.replace(/_/g, " "); }
