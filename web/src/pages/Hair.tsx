import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, Loader2, RefreshCw, Scissors, Undo2, X } from "lucide-react";
import { api, apiUrl, type HairFrame, type HaircutEvent, type HairManifest, type JobStatus, type Project } from "@/api/client";
import { Badge, Button, Input, PageFrame, Panel, ProgressBar, cn } from "@/components/ui";
import { useJobEvents } from "@/hooks/useJobEvents";

type Range = "6m" | "1y" | "all";
type ExportPayload = { start_date: string | null; end_date: string | null; seconds_per_selfie: number };

export function Hair({ project }: { project?: Project | null }) {
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects, enabled: project === undefined });
  const activeProject = project === undefined ? projectsQuery.data?.[0] ?? null : project;
  const queryClient = useQueryClient();
  const recomputeAttempt = useRef<string | null>(null);
  const exportAttempt = useRef<string | null>(null);
  const [range, setRange] = useState<Range>("all");
  const [speed, setSpeed] = useState(1);
  const [selectedHash, setSelectedHash] = useState<string | null>(null);
  const [preview, setPreview] = useState<"photo" | "mask">("photo");
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobKind, setJobKind] = useState<"analysis" | "export" | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [manualDate, setManualDate] = useState(localDate);
  const today = useToday();
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    setSelectedHash(null);
    setJobId(null);
    setJobKind(null);
    setJobError(null);
    setManualDate(localDate());
    recomputeAttempt.current = null;
    exportAttempt.current = null;
  }, [activeProject?.id]);

  const hairQuery = useQuery({
    queryKey: ["hair", activeProject?.id],
    queryFn: () => api.hair(activeProject!.id),
    enabled: Boolean(activeProject),
    refetchInterval: (query) => ["not_ready", "stale"].includes(query.state.data?.status ?? "") ? 2500 : 10000,
  });
  const recomputeMutation = useMutation({
    mutationFn: () => api.recomputeHair(activeProject!.id),
    onSuccess: (job) => { setJobError(null); setJobId(job.job_id); setJobKind("analysis"); },
  });
  const exportMutation = useMutation({
    mutationFn: (payload: ExportPayload) => api.exportHair(activeProject!.id, payload),
    onSuccess: (job) => { setJobError(null); setJobId(job.job_id); setJobKind("export"); },
  });
  const onTerminal = useCallback((job: JobStatus) => {
    queryClient.invalidateQueries({ queryKey: ["hair"] });
    setJobError(job.status === "failed" ? job.error ?? "Hair processing failed." : null);
    setJobId(null);
    setJobKind(null);
  }, [queryClient]);
  const activeJob = useJobEvents(jobId, onTerminal);
  const busy = Boolean(jobId) || recomputeMutation.isPending || exportMutation.isPending;
  const manifest = hairQuery.data;

  useEffect(() => {
    if (!activeProject || !manifest || !["not_ready", "stale"].includes(manifest.status)) return;
    const key = `${activeProject.id}:${manifest.analysis_revision ?? manifest.status}`;
    if (recomputeAttempt.current === key || busy || jobError || recomputeMutation.isError) return;
    recomputeAttempt.current = key;
    recomputeMutation.mutate();
  }, [activeProject?.id, manifest, busy, jobError, recomputeMutation.isError, recomputeMutation.mutate]);

  const frames = useMemo(() => filterFrames(manifest?.frames ?? [], range), [manifest?.frames, range]);
  const included = frames.filter((frame) => frame.eligible && !frame.excluded);
  const selected = frames.find((frame) => frame.hash === selectedHash) ?? frames[frames.length - 1] ?? null;
  const payload = exportPayload(frames, range, speed);
  const latestExport = manifest?.latest_export;
  const matchingVideo = latestExport && (latestExport.config.start_date ?? null) === payload.start_date && (latestExport.config.end_date ?? null) === payload.end_date ? latestExport : null;

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = playbackRate(matchingVideo?.config.seconds_per_selfie, speed);
  }, [speed, matchingVideo?.id, matchingVideo?.config.seconds_per_selfie]);

  useEffect(() => {
    if (!activeProject || !manifest || manifest.status !== "ready" || included.length < 2 || busy || jobError || exportMutation.isError) return;
    if (matchingVideo && !matchingVideo.stale) return;
    const key = `${activeProject.id}:${manifest.analysis_revision}:${payload.start_date}:${payload.end_date}`;
    if (exportAttempt.current === key) return;
    exportAttempt.current = key;
    exportMutation.mutate({ ...payload, seconds_per_selfie: 1 });
  }, [activeProject?.id, manifest, included.length, busy, jobError, matchingVideo, payload.start_date, payload.end_date, exportMutation.isError, exportMutation.mutate]);

  const frameMutation = useMutation({
    mutationFn: ({ hash, excluded }: { hash: string; excluded: boolean }) => api.updateHairFrame(hash, excluded),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["hair"] }),
  });
  const haircutMutation = useMutation({
    mutationFn: ({ id, payload: update }: { id: number; payload: { event_date?: string; status?: HaircutEvent["status"] } }) => api.updateHaircut(id, update),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["hair"] }),
  });
  const addHaircutMutation = useMutation({
    mutationFn: () => api.addHaircut(activeProject!.id, manualDate),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["hair"] }),
  });

  if (projectsQuery.isLoading || hairQuery.isLoading) return <HairLoading />;
  if (!activeProject) return <EmptyHair title="No selfie project" detail="Create a project to start tracking haircuts." />;
  if (hairQuery.isError) return <EmptyHair title="Could not load hair history" detail={hairQuery.error.message} action={<Button onClick={() => hairQuery.refetch()}>Retry</Button>} />;
  if (!manifest) return <HairLoading />;

  const error = jobError ?? recomputeMutation.error?.message ?? exportMutation.error?.message ?? frameMutation.error?.message ?? haircutMutation.error?.message ?? addHaircutMutation.error?.message;
  return (
    <PageFrame size="wide" className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="flex items-center gap-3 text-3xl font-black tracking-tight"><Scissors className="h-7 w-7" />Hair</h1>
        <Button variant="secondary" disabled={busy} onClick={() => { setJobError(null); recomputeMutation.mutate(); }}>
          {jobKind === "analysis" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Recheck photos
        </Button>
      </header>

      <HairSummary manifest={manifest} today={today} />
      {error ? <div role="alert" className="rounded-md bg-coral/10 p-3 text-sm text-coral">{error}</div> : null}
      {manifest.analysis.failed_photos ? <p role="status" className="text-sm text-ink/65">Hair analysis failed for {manifest.analysis.failed_photos} {manifest.analysis.failed_photos === 1 ? "photo" : "photos"}. Use Recheck photos to retry.</p> : null}
      {activeJob ? <Panel><div className="mb-2 flex justify-between gap-3 text-sm font-semibold"><span>{jobKind === "analysis" ? "Analyzing hair" : "Creating video"}</span><span>{Math.round(activeJob.progress * 100)}%</span></div><ProgressBar value={activeJob.progress} /></Panel> : null}

      <HaircutLedger events={manifest.haircuts} manualDate={manualDate} today={today} onManualDate={setManualDate}
        onAdd={() => addHaircutMutation.mutate()} adding={addHaircutMutation.isPending}
        onUpdate={(id, update) => haircutMutation.mutate({ id, payload: update })} updating={haircutMutation.isPending} />

      {manifest.status === "not_ready" || manifest.status === "insufficient" ? (
        <Panel><p className="text-sm text-ink/65">{manifest.status === "not_ready" ? "Analyzing your photos. Haircuts can be recorded while this runs." : "Add a clear selfie to start your hair history."}</p></Panel>
      ) : (
        <>
          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(18rem,0.8fr)]">
            <Panel className="min-w-0 overflow-hidden p-0">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink/10 p-4">
                <h2 className="font-bold">Hair timeline</h2>
                <div className="flex flex-wrap gap-2">
                  <Segmented values={["6m", "1y", "all"] as Range[]} value={range} onChange={setRange} label={(value) => value === "all" ? "All" : value === "6m" ? "6 months" : "1 year"} ariaLabel="Timeline range" />
                  <Segmented values={[0.5, 1, 2]} value={speed} onChange={setSpeed} label={(value) => `${value}×`} ariaLabel="Playback speed" />
                </div>
              </div>
              <div className="aspect-[4/5] max-h-[65vh] overflow-hidden bg-white">
                {matchingVideo ? <video ref={videoRef} key={matchingVideo.id} src={apiUrl(matchingVideo.playback_url)} controls playsInline preload="metadata" aria-label="Hair timeline video" className="h-full w-full object-contain" onLoadedMetadata={(event) => { event.currentTarget.playbackRate = playbackRate(matchingVideo.config.seconds_per_selfie, speed); }} />
                  : selected?.composite_url ? <img src={apiUrl(selected.composite_url)} alt={`Hair outline on ${formatDate(selected.date)}`} className="h-full w-full object-contain" />
                  : <div className="grid h-full place-items-center p-6 text-center text-sm text-ink/55">A video needs two included days.</div>}
              </div>
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-ink/10 p-4">
                <span className="text-sm text-ink/60">{included.length} included days{matchingVideo?.stale ? " · Video needs updating" : ""}</span>
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" onClick={() => { setJobError(null); exportMutation.mutate(payload); }} disabled={busy || manifest.status !== "ready" || included.length < 2}>{matchingVideo ? "Update video" : "Create video"}</Button>
                  {matchingVideo ? <a href={apiUrl(matchingVideo.file_url)} download className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-ink px-4 text-sm font-semibold text-white"><Download className="h-4 w-4" /> Download MP4</a> : null}
                </div>
              </div>
            </Panel>
            <Panel className="min-w-0 self-start overflow-hidden p-0">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink/10 p-4">
                <h2 className="font-bold">{selected ? formatDate(selected.date) : "Select a day"}</h2>
                <Segmented values={["photo", "mask"] as const} value={preview} onChange={setPreview} label={(value) => value === "photo" ? "Photo" : "Mask"} ariaLabel="Photo preview" />
              </div>
              {selected ? <>
                <div className="aspect-[4/5] overflow-hidden bg-white">
                  <img src={apiUrl(preview === "mask" && selected.composite_url ? selected.composite_url : selected.source_url)} alt={`${preview === "mask" && selected.composite_url ? "Hair mask" : "Selfie"} on ${formatDate(selected.date)}`} className="h-full w-full object-contain" />
                </div>
                <div className="space-y-3 border-t border-ink/10 p-4">
                  <Badge tone={selected.eligible && !selected.excluded ? "good" : "warn"}>{selected.excluded ? "Excluded" : selected.eligible ? "Included" : "Not used"}</Badge>
                  {selected.reasons.length ? <p className="text-sm leading-5 text-ink/65">{selected.reasons.map(reasonLabel).join(". ")}.</p> : null}
                  <Button variant="secondary" className="w-full" disabled={frameMutation.isPending || !selected.eligible && !selected.excluded} onClick={() => frameMutation.mutate({ hash: selected.hash, excluded: !selected.excluded })}>
                    {selected.excluded ? <Undo2 className="h-4 w-4" /> : <X className="h-4 w-4" />}{selected.excluded ? "Restore this photo" : "Exclude this hair frame"}
                  </Button>
                </div>
              </> : null}
            </Panel>
          </div>
          <Panel className="min-w-0 overflow-hidden p-0">
            <h2 className="border-b border-ink/10 p-4 font-bold">Photos</h2>
            <div className="flex snap-x gap-2 overflow-x-auto p-4">
              {frames.map((frame) => <button type="button" key={frame.hash} aria-label={`View ${formatDate(frame.date)}`} aria-pressed={selected?.hash === frame.hash} onClick={() => setSelectedHash(frame.hash)} className={cn("w-20 shrink-0 snap-start overflow-hidden rounded-md border bg-white text-left", selected?.hash === frame.hash ? "border-ink ring-2 ring-ink/15" : "border-ink/10", (!frame.eligible || frame.excluded) && "opacity-45")}>
                <img loading="lazy" src={apiUrl(frame.thumb_url)} alt="" className="aspect-[4/5] w-full object-cover" /><span className="block truncate px-1.5 py-1 text-xs">{compactDate(frame.date)}</span>
              </button>)}
            </div>
          </Panel>
        </>
      )}
    </PageFrame>
  );
}

function HairSummary({ manifest, today }: { manifest: HairManifest; today: string }) {
  const cut = manifest.last_haircut;
  const elapsed = cut ? daysBetween(cut.event_date, today) : null;
  const change = manifest.change_since_haircut;
  return <div className="grid gap-3 sm:grid-cols-3">
    <Panel><h2 className="text-sm font-semibold text-ink/60">Since last haircut</h2>
      {elapsed !== null ? <><p className="mt-2 text-3xl font-black tracking-tight" data-testid="haircut-elapsed">{elapsed.toLocaleString()} {elapsed === 1 ? "day" : "days"}</p><p className="mt-1 text-sm text-ink/60">{formatDate(cut!.event_date)}</p>{elapsed >= 7 ? <p className="mt-1 text-xs text-ink/45">{Math.floor(elapsed / 7)} {Math.floor(elapsed / 7) === 1 ? "week" : "weeks"}{elapsed % 7 ? `, ${elapsed % 7} ${elapsed % 7 === 1 ? "day" : "days"}` : ""}</p> : null}</>
        : <><p className="mt-2 font-bold">No haircut recorded</p><Button variant="ghost" className="mt-1 px-0" onClick={() => document.getElementById("haircut-date")?.focus()}>Add a date</Button></>}
    </Panel>
    <Panel><h2 className="text-sm font-semibold text-ink/60">Last analyzed selfie</h2><p className="mt-2 text-lg font-bold">{manifest.analysis.latest_analyzed_date ? formatDate(manifest.analysis.latest_analyzed_date) : "None yet"}</p><p className="mt-1 text-sm text-ink/60">{manifest.coverage.included} included days{manifest.analysis.pending_photos ? ` · ${manifest.analysis.pending_photos} pending` : ""}</p></Panel>
    <Panel><h2 className="text-sm font-semibold text-ink/60">Outline change since haircut</h2><p className="mt-2 text-3xl font-black tracking-tight">{change ? `${change.area_change_percent > 0 ? "+" : ""}${Math.round(change.area_change_percent)}%` : "—"}</p><p className="mt-1 text-xs leading-5 text-ink/55">{change ? `Visible hair area · ${compactDate(change.baseline_date)} to ${compactDate(change.latest_date)}` : "Needs comparable photos after a recorded haircut."}</p></Panel>
  </div>;
}

function HaircutLedger({ events, manualDate, today, onManualDate, onAdd, adding, onUpdate, updating }: {
  events: HaircutEvent[]; manualDate: string; today: string; onManualDate: (value: string) => void; onAdd: () => void; adding: boolean;
  onUpdate: (id: number, payload: { event_date?: string; status?: HaircutEvent["status"] }) => void; updating: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? events : events.slice(0, 5);
  return <Panel className="min-w-0 overflow-hidden p-0">
    <div className="flex flex-wrap items-end justify-between gap-4 border-b border-ink/10 p-4">
      <h2 className="font-bold">Haircuts</h2>
      <form className="flex min-w-0 flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); if (manualDate && manualDate <= today) onAdd(); }}>
        <label htmlFor="haircut-date" className="min-w-0 text-xs font-semibold text-ink/60">Haircut date<Input id="haircut-date" type="date" max={today} required value={manualDate} onChange={(event) => onManualDate(event.target.value)} className="mt-1 max-w-full" /></label>
        <Button type="submit" disabled={!manualDate || manualDate > today || adding}>{adding ? "Saving…" : "Add haircut"}</Button>
      </form>
    </div>
    {events.length ? <div className="divide-y divide-ink/10">{visible.map((event) => <HaircutRow key={event.id} event={event} today={today} onUpdate={onUpdate} updating={updating} />)}</div>
      : <p className="p-4 text-sm text-ink/55">No haircuts recorded.</p>}
    {events.length > 5 ? <div className="border-t border-ink/10 px-4 py-2"><Button variant="ghost" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>{expanded ? "Show recent haircuts" : `Show all ${events.length} haircuts`}</Button></div> : null}
  </Panel>;
}

function HaircutRow({ event, today, onUpdate, updating }: { event: HaircutEvent; today: string; onUpdate: (id: number, payload: { event_date?: string; status?: HaircutEvent["status"] }) => void; updating: boolean }) {
  const [draftDate, setDraftDate] = useState(event.event_date);
  const [reviewing, setReviewing] = useState(false);
  useEffect(() => setDraftDate(event.event_date), [event.event_date]);
  const evidence = event.evidence;
  const valid = Boolean(draftDate) && draftDate <= today;
  return <div className="p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><div className="flex flex-wrap items-center gap-2"><span className="font-semibold">{formatDate(event.event_date)}</span><Badge tone={event.status === "confirmed" ? "good" : "warn"}>{event.status === "confirmed" ? "Confirmed" : "Possible haircut"}</Badge></div>
        {event.status !== "confirmed" && evidence.earliest_date && evidence.latest_date ? <p className="mt-1 text-xs text-ink/60">{evidence.earliest_date === evidence.latest_date ? "Change first seen on this date." : `Between ${compactDate(evidence.earliest_date)} and ${compactDate(evidence.latest_date)}.`}{event.status === "provisional" ? " Waiting for more photos." : ""}</p> : null}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {event.status !== "confirmed" && evidence.before_photo_hash && evidence.after_photo_hash ? <Button variant="ghost" onClick={() => setReviewing(!reviewing)} aria-expanded={reviewing}>{reviewing ? "Hide photos" : "Review photos"}</Button> : null}
        <Input aria-label={`Date for haircut ${event.id}`} type="date" max={today} value={draftDate} onChange={(e) => setDraftDate(e.target.value)} className="max-w-full sm:w-auto" />
        {draftDate !== event.event_date ? <Button variant="secondary" disabled={updating || !valid} onClick={() => onUpdate(event.id, { event_date: draftDate })}>Save date</Button> : null}
        {event.status !== "confirmed" ? <Button size="icon" aria-label="Confirm haircut" disabled={updating || !valid} onClick={() => onUpdate(event.id, { status: "confirmed", event_date: draftDate })}><Check className="h-4 w-4" /></Button> : null}
        <Button size="icon" variant="ghost" aria-label={event.status === "confirmed" ? "Remove haircut" : "Dismiss haircut"} disabled={updating} onClick={() => onUpdate(event.id, { status: "dismissed" })}><X className="h-4 w-4" /></Button>
      </div>
    </div>
    {reviewing && evidence.before_photo_hash && evidence.after_photo_hash ? <div className="mt-3 grid max-w-xl grid-cols-2 gap-3">
      <figure><img loading="lazy" src={apiUrl(`/api/photos/${evidence.before_photo_hash}/image`)} alt="Selfie before the possible haircut" className="aspect-[3/4] w-full rounded object-cover" /><figcaption className="mt-1 text-xs text-ink/60">Before</figcaption></figure>
      <figure><img loading="lazy" src={apiUrl(`/api/photos/${evidence.after_photo_hash}/image`)} alt="Selfie after the possible haircut" className="aspect-[3/4] w-full rounded object-cover" /><figcaption className="mt-1 text-xs text-ink/60">After</figcaption></figure>
    </div> : null}
  </div>;
}

function Segmented<T extends string | number>({ values, value, onChange, label, ariaLabel }: { values: readonly T[]; value: T; onChange: (value: T) => void; label: (value: T) => string; ariaLabel: string }) {
  return <div role="group" aria-label={ariaLabel} className="grid auto-cols-fr grid-flow-col rounded-md border border-ink/10 bg-bone p-1">{values.map((item) => <button type="button" aria-pressed={value === item} key={item} onClick={() => onChange(item)} className={cn("min-h-10 min-w-12 rounded px-2 text-xs font-semibold", value === item ? "bg-ink text-white" : "text-ink/60")}>{label(item)}</button>)}</div>;
}
function HairLoading() { return <PageFrame size="narrow"><Panel className="grid min-h-48 place-items-center"><div role="status" className="flex items-center gap-3 text-sm"><Loader2 className="h-5 w-5 animate-spin" />Loading hair history</div></Panel></PageFrame>; }
function EmptyHair({ title, detail, action }: { title: string; detail: string; action?: React.ReactNode }) { return <PageFrame size="narrow"><Panel><h1 className="text-2xl font-bold">{title}</h1><p className="mt-2 text-sm text-ink/55">{detail}</p>{action ? <div className="mt-4">{action}</div> : null}</Panel></PageFrame>; }

export function filterFrames(frames: HairFrame[], range: Range) {
  if (range === "all" || !frames.length) return frames;
  const latest = new Date(`${frames[frames.length - 1].date}T12:00:00`);
  const day = latest.getDate();
  latest.setDate(1);
  latest.setMonth(latest.getMonth() - (range === "6m" ? 6 : 12));
  const lastDay = new Date(latest.getFullYear(), latest.getMonth() + 1, 0).getDate();
  latest.setDate(Math.min(day, lastDay));
  return frames.filter((frame) => frame.date >= localDate(latest));
}
export function exportPayload(frames: HairFrame[], range: Range, speed: number): ExportPayload {
  return { start_date: range === "all" ? null : frames[0]?.date ?? null, end_date: range === "all" ? null : frames[frames.length - 1]?.date ?? null, seconds_per_selfie: 1 / speed };
}
function playbackRate(encodedSeconds: number | undefined, speed: number) { return Math.max(0.25, Math.min(4, (encodedSeconds ?? 1) * speed)); }
export function daysBetween(start: string, end: string) {
  return Math.max(0, Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86400000));
}
function localDate(value = new Date()) { return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`; }
function useToday() {
  const [today, setToday] = useState(localDate);
  useEffect(() => {
    const update = () => setToday(localDate());
    const timer = window.setInterval(update, 60000);
    window.addEventListener("focus", update);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", update); };
  }, []);
  return today;
}
function formatDate(value: string) { return new Date(`${value}T12:00:00`).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" }); }
function compactDate(value: string) { return new Date(`${value}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }); }
function reasonLabel(value: string) { return ({ low_hair_confidence: "Hair could not be identified clearly", uncertain_hair_boundary: "The hair edge is unclear", hair_touches_frame_edge: "Hair is cropped by the photo", alignment_crops_hair: "Hair is cropped by the alignment", alignment_distorts_hair: "Alignment changes the hair shape", implausible_hair_area: "The mask size is unusual", alignment_not_ready: "Waiting for face alignment", invalid_face_landmarks: "The face could not be measured", head_pose: "Head angle is too different", low_photo_quality: "Photo quality is too low", hair_analysis_failed: "Hair analysis failed" } as Record<string, string>)[value] ?? value.replace(/_/g, " "); }
