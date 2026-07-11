import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowLeftRight,
  Check,
  CircleGauge,
  Download,
  GitCompareArrows,
  Info,
  ScanFace,
  Sparkles,
} from "lucide-react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  apiUrl,
  type FaceShapeComparePeriod,
  type FaceShapeComparison,
  type FaceShapePeriod,
  type FaceShapePoint,
  type FaceShapeTrend,
  type Project,
} from "@/api/client";
import { Badge, Button, PageFrame, Panel, cn } from "@/components/ui";

type Range = "6m" | "1y" | "all";
type CompareMode = "wipe" | "side" | "outline";
type ActiveMarker = "a" | "b";

export function FaceChange({ project }: { project?: Project | null }) {
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects, enabled: project === undefined });
  const activeProject = project === undefined ? projectsQuery.data?.[0] ?? null : project;
  const queryClient = useQueryClient();
  const recomputeAttempt = useRef<string | null>(null);
  const [range, setRange] = useState<Range>("all");
  const [activeMarker, setActiveMarker] = useState<ActiveMarker>("b");
  const [aDate, setADate] = useState<string | null>(null);
  const [bDate, setBDate] = useState<string | null>(null);

  const trendQuery = useQuery({
    queryKey: ["face-shape", activeProject?.id],
    queryFn: () => api.faceShape(activeProject!.id),
    enabled: Boolean(activeProject),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "stale" || status === "not_ready" ? 2500 : false;
    },
  });
  const recomputeMutation = useMutation({
    mutationFn: () => api.recomputeFaceShape(activeProject!.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["face-shape", activeProject?.id] }),
  });

  useEffect(() => {
    const trend = trendQuery.data;
    if (!activeProject || !trend || !["not_ready", "stale"].includes(trend.status)) return;
    const key = `${activeProject.id}:${trend.analysis_revision ?? trend.status}`;
    if (recomputeAttempt.current === key || recomputeMutation.isPending) return;
    recomputeAttempt.current = key;
    recomputeMutation.mutate();
  }, [activeProject, recomputeMutation, trendQuery.data]);

  const selectable = useMemo(
    () => (trendQuery.data?.points ?? []).filter((point) => point.trend_index != null && point.representative),
    [trendQuery.data?.points],
  );

  useEffect(() => {
    if (!selectable.length) return;
    const latest = selectable[selectable.length - 1];
    setBDate((current) => current && selectable.some((point) => point.date === current) ? current : latest.date);
    setADate((current) => {
      if (current && selectable.some((point) => point.date === current)) return current;
      const target = shiftDate(latest.date, -180);
      return nearestPoint(selectable, target)?.date ?? selectable[0].date;
    });
  }, [selectable]);

  const filteredPoints = useMemo(() => filterRange(trendQuery.data?.points ?? [], range), [range, trendQuery.data?.points]);
  const selectedA = selectable.find((point) => point.date === aDate) ?? null;
  const selectedB = selectable.find((point) => point.date === bDate) ?? null;
  const compareQuery = useQuery({
    queryKey: ["face-shape-compare", activeProject?.id, periodForPoint(selectedA), periodForPoint(selectedB)],
    queryFn: () => api.compareFaceShape(activeProject!.id, { a: periodForPoint(selectedA)!, b: periodForPoint(selectedB)! }),
    enabled: Boolean(activeProject && selectedA && selectedB),
  });

  if (!activeProject) return <NoProject />;
  if (trendQuery.isLoading) return <ShapeLoading />;
  if (trendQuery.isError) return <ShapeError message={trendQuery.error.message} onRetry={() => trendQuery.refetch()} />;

  const trend = trendQuery.data;
  if (!trend || trend.status === "not_ready") {
    return <ShapePreparing pending={recomputeMutation.isPending} error={recomputeMutation.error?.message} />;
  }
  if (trend.status === "insufficient") {
    return <InsufficientShape count={trend.coverage.measured_photos ?? trend.coverage.landmark_photos ?? 0} required={trend.coverage.required ?? 6} />;
  }

  const summary = trend.summary;
  return (
    <PageFrame size="wide" className="space-y-5">
      <section className="overflow-hidden rounded-xl border border-ink/10 bg-ink text-paper shadow-line">
        <div className="relative grid gap-6 overflow-hidden p-5 md:grid-cols-[1fr_auto] md:items-end md:p-7">
          <div className="pointer-events-none absolute -right-16 -top-20 h-64 w-64 rounded-full border-[2.5rem] border-teal/20" />
          <div className="relative">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="good">Personal baseline · 0</Badge>
              <Badge>{capitalize(summary?.confidence ?? "unavailable")} reliability</Badge>
              {trend.status === "stale" ? <Badge tone="warn">Updating</Badge> : null}
            </div>
            <div className="mt-5 flex items-baseline gap-3">
              <span className="font-mono text-6xl font-black tracking-[-0.08em] md:text-7xl">{formatIndex(summary?.latest_index)}</span>
              <span className="text-sm font-black uppercase tracking-[0.16em] text-paper/55">shape index</span>
            </div>
            <h1 className="mt-4 text-3xl font-black tracking-tight md:text-4xl">Your face, changing slowly.</h1>
            <p className="mt-2 max-w-2xl text-sm font-semibold leading-6 text-paper/60">
              {summarySentence(summary?.direction_90d, summary?.change_90d)} This is a visual pattern in your selfies—not a scale reading.
            </p>
          </div>
          <div className="relative grid grid-cols-2 gap-2 md:w-72">
            <HeroMetric label="90-day change" value={formatSigned(summary?.change_90d)} />
            <HeroMetric label="Clear days" value={String(trend.coverage.eligible_days ?? 0)} />
          </div>
        </div>
        <div className="h-2 bg-[linear-gradient(90deg,#1F7A75_0%,#E7E0CF_50%,#C94F31_100%)]" />
      </section>

      <Panel className="overflow-hidden p-0">
        <div className="flex flex-col gap-3 border-b border-ink/10 px-4 py-4 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-teal" />
              <h2 className="font-black text-ink">Face-shape trend</h2>
            </div>
            <p className="mt-1 text-sm font-semibold text-ink/55">Faint dots are selfie days. The dark line shows the sustained pattern; shading shows its likely range.</p>
          </div>
          <div className="grid grid-cols-3 rounded-md border border-ink/10 bg-bone p-1">
            {(["6m", "1y", "all"] as Range[]).map((item) => (
              <button
                type="button"
                key={item}
                onClick={() => setRange(item)}
                className={cn("min-h-11 rounded px-3 text-xs font-black uppercase tracking-[0.12em]", range === item ? "bg-ink text-paper" : "text-ink/55")}
              >
                {item === "all" ? "All" : item.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

        <div className="px-1 pb-2 pt-4 md:px-4">
          <div className="h-[19rem] w-full md:h-[23rem]" aria-label="Interactive face-shape trend chart">
            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={304} initialDimension={{ width: 800, height: 304 }}>
              <ComposedChart
                data={filteredPoints.map((point) => ({ ...point, range: point.lower != null && point.upper != null ? [point.lower, point.upper] : null }))}
                margin={{ top: 14, right: 12, bottom: 8, left: 0 }}
                onClick={(state) => {
                  if (state?.activeLabel == null) return;
                  const point = nearestPoint(selectable, String(state.activeLabel));
                  if (!point) return;
                  if (activeMarker === "a") setADate(point.date);
                  else setBDate(point.date);
                }}
              >
                <CartesianGrid stroke="#11141218" vertical={false} />
                <XAxis dataKey="date" minTickGap={34} tickFormatter={compactDate} tick={{ fontSize: 11, fill: "#11141288" }} />
                <YAxis width={42} tick={{ fontSize: 11, fill: "#11141288" }} tickFormatter={(value) => Number(value).toFixed(1)} />
                <Tooltip content={<ShapeTooltip />} />
                <ReferenceLine y={0} stroke="#C59A2D" strokeDasharray="5 5" label={{ value: "baseline", fill: "#8B6D23", fontSize: 10 }} />
                {aDate ? <ReferenceLine x={aDate} stroke="#1F7A75" strokeWidth={2} label={{ value: "A", fill: "#1F7A75", fontWeight: 900 }} /> : null}
                {bDate ? <ReferenceLine x={bDate} stroke="#C94F31" strokeWidth={2} label={{ value: "B", fill: "#C94F31", fontWeight: 900 }} /> : null}
                <Area type="monotone" dataKey="range" fill="#1F7A7522" stroke="none" connectNulls={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="trend_index" stroke="#202522" strokeWidth={3} dot={false} connectNulls={false} isAnimationActive={false} />
                <Scatter dataKey="raw_index" fill="#1F7A75" fillOpacity={0.34} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="grid gap-3 border-t border-ink/10 bg-bone/60 p-4 md:grid-cols-[1fr_1fr_auto] md:items-end">
          <MarkerSelect marker="A" tone="teal" active={activeMarker === "a"} value={aDate} points={selectable} onActivate={() => setActiveMarker("a")} onChange={setADate} />
          <MarkerSelect marker="B" tone="coral" active={activeMarker === "b"} value={bDate} points={selectable} onActivate={() => setActiveMarker("b")} onChange={setBDate} />
          <Button
            variant="secondary"
            className="min-h-11"
            onClick={() => {
              setADate(bDate);
              setBDate(aDate);
            }}
          >
            <ArrowLeftRight className="h-4 w-4" />
            Swap
          </Button>
        </div>
      </Panel>

      {trend.events.length ? <EventStrip events={trend.events} /> : null}

      {trend.insights?.length ? <InsightsPanel insights={trend.insights} /> : null}

      <CompareStage comparison={compareQuery.data} loading={compareQuery.isLoading} error={compareQuery.error?.message} />

      <div className="grid gap-4 lg:grid-cols-[1.15fr_0.85fr]">
        <CalibrationPanel projectId={activeProject.id} trend={trend} />
        <MethodPanel projectId={activeProject.id} trend={trend} />
      </div>
    </PageFrame>
  );
}

function CompareStage({ comparison, loading, error }: { comparison?: FaceShapeComparison; loading: boolean; error?: string }) {
  const [mode, setMode] = useState<CompareMode>("wipe");
  const [wipe, setWipe] = useState(50);
  return (
    <Panel className="overflow-hidden p-0">
      <div className="flex flex-col gap-3 border-b border-ink/10 px-4 py-4 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <GitCompareArrows className="h-4 w-4 text-coral" />
            <h2 className="font-black text-ink">A / B comparison</h2>
          </div>
          <p className="mt-1 text-sm font-semibold text-ink/55">Each side represents the median shape of its selected window.</p>
        </div>
        <div className="grid grid-cols-3 rounded-md border border-ink/10 bg-bone p-1">
          {(["wipe", "side", "outline"] as CompareMode[]).map((item) => (
            <button key={item} type="button" onClick={() => setMode(item)} className={cn("min-h-11 rounded px-3 text-xs font-black capitalize", mode === item ? "bg-ink text-paper" : "text-ink/55")}>
              {item}
            </button>
          ))}
        </div>
      </div>
      {loading ? <div className="h-[28rem] animate-pulse bg-ink/8" /> : error ? <div className="p-6 text-sm font-semibold text-coral">{error}</div> : comparison ? (
        <div>
          <div className="grid bg-ink lg:grid-cols-[minmax(0,1fr)_21rem]">
            <div className="relative min-h-[24rem] overflow-hidden md:min-h-[34rem]">
              {mode === "side" ? <SideBySide comparison={comparison} /> : mode === "outline" ? <ContourStage a={comparison.a} b={comparison.b} /> : <WipeStage comparison={comparison} wipe={wipe} />}
              {mode === "wipe" ? (
                <label className="absolute inset-x-4 bottom-4 rounded-md bg-paper/95 p-3 shadow-lg backdrop-blur">
                  <span className="sr-only">Move before and after divider</span>
                  <input aria-label="Move before and after divider" type="range" min="0" max="100" value={wipe} onChange={(event) => setWipe(Number(event.target.value))} className="w-full accent-coral" />
                </label>
              ) : null}
            </div>
            <div className="bg-paper p-5">
              <Badge tone={comparison.confidence === "high" ? "good" : comparison.confidence === "low" ? "warn" : "default"}>{capitalize(comparison.confidence)} reliability</Badge>
              <div className="mt-4 text-4xl font-black tracking-tight text-ink">{formatSigned(comparison.delta)}</div>
              <div className="mt-1 text-xs font-black uppercase tracking-[0.16em] text-ink/45">index change · A to B</div>
              <p className="mt-4 text-lg font-black leading-7 text-ink">{comparisonSentence(comparison)}</p>
              {!comparison.same_capture_profile ? (
                <div className="mt-4 flex gap-2 rounded-md border border-amber/30 bg-amber/10 p-3 text-xs font-semibold leading-5 text-ink/65">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber" />
                  Capture source changed between these periods, so the comparison is less certain.
                </div>
              ) : null}
              <div className="mt-5 space-y-2">
                {comparison.contributions.slice(0, 3).map((item) => (
                  <div key={item.feature} className="border-b border-ink/8 pb-2 text-sm">
                    <span className="font-semibold text-ink/60">{capitalize(item.region)}</span>
                    <span className="mt-0.5 block font-black text-ink">{item.observation ?? "Looks different"}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="grid gap-2 border-t border-ink/10 bg-bone p-4 sm:grid-cols-2">
            <PeriodLabel marker="A" period={comparison.a} tone="teal" />
            <PeriodLabel marker="B" period={comparison.b} tone="coral" />
          </div>
        </div>
      ) : <div className="p-6 text-sm font-semibold text-ink/55">Choose two periods to compare.</div>}
    </Panel>
  );
}

function WipeStage({ comparison, wipe }: { comparison: FaceShapeComparison; wipe: number }) {
  return (
    <div className="absolute inset-0 bg-ink">
      <FaceImage period={comparison.a} label="A" />
      <div className="absolute inset-0" style={{ clipPath: `inset(0 0 0 ${wipe}%)` }}><FaceImage period={comparison.b} label="B" /></div>
      <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-paper shadow-[0_0_0_1px_rgba(0,0,0,.25)]" style={{ left: `${wipe}%` }} />
      <ContourOverlay a={comparison.a} b={comparison.b} />
    </div>
  );
}

function SideBySide({ comparison }: { comparison: FaceShapeComparison }) {
  return <div className="absolute inset-0 grid grid-cols-2"><div className="relative border-r border-paper/30"><FaceImage period={comparison.a} label="A" /></div><div className="relative"><FaceImage period={comparison.b} label="B" /></div></div>;
}

function FaceImage({ period, label }: { period: FaceShapeComparePeriod; label: string }) {
  return (
    <>
      <img src={apiUrl(period.representative.aligned_url)} alt={`${label} representative aligned selfie`} className="absolute inset-0 h-full w-full object-cover" onError={(event) => { event.currentTarget.src = apiUrl(period.representative.image_url); }} />
      <span className="absolute left-3 top-3 grid h-10 w-10 place-items-center rounded-full bg-paper text-sm font-black text-ink shadow-lg">{label}</span>
    </>
  );
}

function ContourStage({ a, b }: { a: FaceShapeComparePeriod; b: FaceShapeComparePeriod }) {
  return <div className="absolute inset-0 grid place-items-center bg-[radial-gradient(circle_at_center,#2c3430_0%,#111412_70%)] p-8"><svg viewBox="0 0 100 100" className="h-full w-full max-w-xl" role="img" aria-label="Overlaid face contours for periods A and B"><path d={contourPath(a.contour, a.contour, b.contour)} fill="#1F7A7520" stroke="#57B8B0" strokeWidth="1.2" /><path d={contourPath(b.contour, a.contour, b.contour)} fill="#C94F3118" stroke="#F08062" strokeWidth="1.2" strokeDasharray="2 1" /></svg></div>;
}

function ContourOverlay({ a, b }: { a: FaceShapeComparePeriod; b: FaceShapeComparePeriod }) {
  return <svg viewBox="0 0 100 100" className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true"><path d={contourPath(a.contour, a.contour, b.contour)} fill="none" stroke="#5AD1C6" strokeWidth="0.55" /><path d={contourPath(b.contour, a.contour, b.contour)} fill="none" stroke="#FF8C70" strokeWidth="0.55" strokeDasharray="1.2 0.8" /></svg>;
}

function CalibrationPanel({ projectId, trend }: { projectId: number; trend: FaceShapeTrend }) {
  const queryClient = useQueryClient();
  const calibration = trend.calibration;
  const [open, setOpen] = useState(false);
  const [lighter, setLighter] = useState<FaceShapePeriod>({ start: calibration?.lighter?.start ?? "", end: calibration?.lighter?.end ?? "" });
  const [fuller, setFuller] = useState<FaceShapePeriod>({ start: calibration?.fuller?.start ?? "", end: calibration?.fuller?.end ?? "" });
  const mutation = useMutation({
    mutationFn: (payload: { lighter?: FaceShapePeriod | null; fuller?: FaceShapePeriod | null }) => api.updateFaceShapeProfile(projectId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["face-shape", projectId] });
      setOpen(false);
    },
  });
  return (
    <Panel>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-amber" /><h2 className="font-black text-ink">Personal calibration</h2></div>
          <p className="mt-2 text-sm font-semibold leading-6 text-ink/55">Mark one known lighter period and one known fuller period. No weight value is stored.</p>
        </div>
        <Badge tone={calibration?.status === "calibrated" ? "good" : "default"}>{calibration?.status === "calibrated" ? "Calibrated" : "Automatic"}</Badge>
      </div>
      {!open ? <Button variant="secondary" className="mt-4" onClick={() => setOpen(true)}>{calibration?.status === "calibrated" ? "Edit anchor periods" : "Choose anchor periods"}</Button> : (
        <div className="mt-4 space-y-4 border-t border-ink/10 pt-4">
          <PeriodInputs label="Known lighter" value={lighter} onChange={setLighter} />
          <PeriodInputs label="Known fuller" value={fuller} onChange={setFuller} />
          {mutation.error ? <div className="rounded-md border border-coral/25 bg-coral/10 p-3 text-xs font-semibold text-coral">{mutation.error.message}</div> : null}
          <div className="flex flex-wrap gap-2">
            <Button disabled={!lighter.start || !lighter.end || !fuller.start || !fuller.end || mutation.isPending} onClick={() => mutation.mutate({ lighter, fuller })}><Check className="h-4 w-4" />Save calibration</Button>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            {calibration?.status === "calibrated" ? <Button variant="ghost" onClick={() => mutation.mutate({ lighter: null, fuller: null })}>Use automatic model</Button> : null}
          </div>
        </div>
      )}
    </Panel>
  );
}

function InsightsPanel({ insights }: { insights: NonNullable<FaceShapeTrend["insights"]> }) {
  return (
    <Panel>
      <div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-amber" /><h2 className="font-black text-ink">What stands out</h2></div>
      <div className="mt-3 grid gap-2 md:grid-cols-3">
        {insights.map((insight) => (
          <div key={`${insight.kind}-${insight.title}`} className="rounded-md border border-ink/10 bg-bone p-3">
            <h3 className="text-sm font-black text-ink">{insight.title}</h3>
            <p className="mt-1 text-sm font-semibold leading-5 text-ink/55">{insight.detail}</p>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function MethodPanel({ projectId, trend }: { projectId: number; trend: FaceShapeTrend }) {
  return (
    <Panel>
      <details>
        <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 font-black text-ink">
          <span className="flex items-center gap-2"><Info className="h-4 w-4 text-teal" />How this was calculated</span>
          <CircleGauge className="h-4 w-4 text-ink/35" />
        </summary>
        <div className="mt-3 space-y-3 text-sm font-semibold leading-6 text-ink/60">
          <p>Stable eye landmarks align each frame. Multiple cheek, temple, jaw, chin, length, roundness, and symmetry measurements are combined after reducing pose, expression, camera, and low-quality-frame effects.</p>
          <p>Selfie bursts count as one day. The sustained pattern uses nearby days and favors clearer, straighter photos; the shaded range reflects uncertainty.</p>
          <p>The baseline is frozen across {trend.baseline?.observation_count ?? 0} eligible selfies from {formatDateRange(trend.baseline?.start, trend.baseline?.end)}.</p>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <EvidenceStat label="Used" value={trend.coverage.eligible_photos ?? 0} />
            <EvidenceStat label="Excluded" value={trend.coverage.excluded_photos ?? 0} />
          </div>
          <p className="rounded-md bg-amber/10 p-3 text-xs leading-5 text-ink/65">Hydration, aging, facial hair, medication, lighting, and camera perspective can also change appearance. Treat this as supporting visual evidence, not a diagnosis.</p>
          <div className="border-t border-ink/10 pt-3">
            <div className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.12em] text-ink/45"><Download className="h-4 w-4" />Export</div>
            <div className="mt-2 flex flex-wrap gap-2">
              <a href={apiUrl(`/api/projects/${projectId}/face-shape/export?format=csv`)} download className="inline-flex min-h-11 items-center rounded-md bg-ink px-3 text-sm font-bold text-paper hover:bg-graphite">Spreadsheet</a>
              <a href={apiUrl(`/api/projects/${projectId}/face-shape/export?format=json`)} download className="inline-flex min-h-11 items-center rounded-md bg-bone px-3 text-sm font-bold text-ink hover:bg-ink/10">Full analysis</a>
            </div>
          </div>
        </div>
      </details>
    </Panel>
  );
}

function PeriodInputs({ label, value, onChange }: { label: string; value: FaceShapePeriod; onChange: (value: FaceShapePeriod) => void }) {
  return <fieldset><legend className="text-xs font-black uppercase tracking-[0.14em] text-ink/55">{label}</legend><div className="mt-2 grid grid-cols-2 gap-2"><input aria-label={`${label} start`} type="date" value={value.start} onChange={(event) => onChange({ ...value, start: event.target.value })} className="min-h-11 rounded-md border border-ink/15 bg-paper px-3 text-sm font-bold text-ink" /><input aria-label={`${label} end`} type="date" value={value.end} onChange={(event) => onChange({ ...value, end: event.target.value })} className="min-h-11 rounded-md border border-ink/15 bg-paper px-3 text-sm font-bold text-ink" /></div></fieldset>;
}

function MarkerSelect({ marker, tone, active, value, points, onActivate, onChange }: { marker: string; tone: "teal" | "coral"; active: boolean; value: string | null; points: FaceShapePoint[]; onActivate: () => void; onChange: (value: string) => void }) {
  return <label className={cn("rounded-md border bg-paper p-3", active ? tone === "teal" ? "border-teal ring-2 ring-teal/15" : "border-coral ring-2 ring-coral/15" : "border-ink/10")}><span className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.14em] text-ink/55"><button type="button" onClick={onActivate} aria-pressed={active} className={cn("grid h-11 w-11 place-items-center rounded-full text-paper", tone === "teal" ? "bg-teal" : "bg-coral")}>{marker}</button>{active ? "Chart selects this marker" : "Select marker"}</span><input aria-label={`Period ${marker}`} type="date" min={points[0]?.date} max={points[points.length - 1]?.date} value={value ?? ""} onFocus={onActivate} onChange={(event) => { const point = nearestPoint(points, event.target.value); if (point) onChange(point.date); }} className="mt-2 min-h-11 w-full rounded-md border border-ink/10 bg-bone px-3 text-sm font-black text-ink" /><span className="mt-1 block text-xs font-semibold text-ink/45">{formatIndex(points.find((point) => point.date === value)?.trend_index)} index · snaps to a clear selfie</span></label>;
}

function EventStrip({ events }: { events: Array<{ date: string; type: string; label: string }> }) {
  return <Panel className="border border-amber/25 bg-amber/10"><div className="flex items-start gap-3"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber" /><div><h2 className="text-sm font-black text-ink">Comparison boundaries</h2><div className="mt-1 space-y-1 text-xs font-semibold text-ink/60">{events.map((event, index) => <p key={`${event.date}-${index}`}><strong>{formatFullDate(event.date)}:</strong> {event.label}. The trend is intentionally broken here.</p>)}</div></div></div></Panel>;
}

function PeriodLabel({ marker, period, tone }: { marker: string; period: FaceShapeComparePeriod; tone: "teal" | "coral" }) {
  return <div className="flex items-center gap-3 rounded-md border border-ink/10 bg-paper p-3"><span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-full font-black text-paper", tone === "teal" ? "bg-teal" : "bg-coral")}>{marker}</span><div className="min-w-0"><div className="truncate text-sm font-black text-ink">{formatDateRange(period.start, period.end)}</div><div className="mt-0.5 text-xs font-semibold text-ink/55">{period.count} selfie{period.count === 1 ? "" : "s"} · Index {formatIndex(period.index)}</div></div></div>;
}

function HeroMetric({ label, value }: { label: string; value: string }) { return <div className="rounded-md border border-paper/10 bg-paper/8 p-3"><div className="text-[0.62rem] font-black uppercase tracking-[0.14em] text-paper/45">{label}</div><div className="mt-1 font-mono text-2xl font-black text-paper">{value}</div></div>; }
function EvidenceStat({ label, value }: { label: string; value: number }) { return <div className="rounded-md border border-ink/10 bg-bone p-3"><div className="font-black uppercase tracking-[0.12em] text-ink/45">{label}</div><div className="mt-1 text-xl font-black text-ink">{value.toLocaleString()}</div></div>; }

function ShapeTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ payload: FaceShapePoint }>; label?: string }) {
  const point = payload?.[0]?.payload;
  if (!active || !point || point.is_break) return null;
  return <div className="rounded-md border border-ink/10 bg-paper p-3 shadow-xl"><div className="text-xs font-black text-ink">{formatFullDate(label ?? point.date)}</div><div className="mt-1 font-mono text-lg font-black text-ink">{formatIndex(point.trend_index)}</div><div className="text-xs font-semibold text-ink/55">{capitalize(point.confidence ?? "low")} reliability · {point.sample_count ?? 0} samples</div></div>;
}

function NoProject() { return <PageFrame size="narrow"><Panel><ScanFace className="h-7 w-7 text-teal" /><h1 className="mt-3 text-2xl font-black text-ink">Face Change</h1><p className="mt-2 text-sm font-semibold text-ink/55">Create a project and add selfies before building a personal face-shape trend.</p></Panel></PageFrame>; }
function ShapeLoading() { return <PageFrame size="wide"><div className="space-y-4"><div className="h-48 animate-pulse rounded-xl bg-ink/10" /><div className="h-96 animate-pulse rounded-xl bg-ink/8" /></div></PageFrame>; }
function ShapePreparing({ pending, error }: { pending: boolean; error?: string }) { return <PageFrame size="narrow"><Panel className="text-center"><ScanFace className="mx-auto h-10 w-10 text-teal" /><h1 className="mt-4 text-2xl font-black text-ink">Building your face-shape baseline</h1><p className="mt-2 text-sm font-semibold leading-6 text-ink/55">Existing landmarks are being measured locally. Your photos are not uploaded or changed.</p>{pending ? <div className="mt-4 text-xs font-black uppercase tracking-[0.14em] text-teal">Analysis started</div> : null}{error ? <div className="mt-4 text-xs font-semibold text-coral">{error}</div> : null}</Panel></PageFrame>; }
function ShapeError({ message, onRetry }: { message: string; onRetry: () => void }) { return <PageFrame size="narrow"><Panel className="border border-coral/25 bg-coral/10"><h1 className="text-xl font-black text-ink">Face Change unavailable</h1><p className="mt-2 text-sm font-semibold text-ink/60">{message}</p><Button className="mt-4" onClick={onRetry}>Try again</Button></Panel></PageFrame>; }
function InsufficientShape({ count, required }: { count: number; required: number }) { return <PageFrame size="narrow"><Panel><ScanFace className="h-8 w-8 text-teal" /><h1 className="mt-3 text-2xl font-black text-ink">A few more clear selfies</h1><p className="mt-2 text-sm font-semibold leading-6 text-ink/55">A reliable personal baseline needs at least {required} clear, front-facing selfies. {count} are ready now.</p></Panel></PageFrame>; }

function filterRange(points: FaceShapePoint[], range: Range) {
  if (range === "all" || !points.length) return points;
  const dates = points.filter((point) => !point.is_break).map((point) => new Date(`${point.date}T12:00:00`).getTime());
  const latest = Math.max(...dates);
  const days = range === "6m" ? 183 : 365;
  const start = latest - days * 86_400_000;
  return points.filter((point) => new Date(`${point.date}T12:00:00`).getTime() >= start);
}

function nearestPoint(points: FaceShapePoint[], target: string) { const targetTime = new Date(`${target}T12:00:00`).getTime(); return points.reduce<FaceShapePoint | null>((best, point) => !best || Math.abs(new Date(`${point.date}T12:00:00`).getTime() - targetTime) < Math.abs(new Date(`${best.date}T12:00:00`).getTime() - targetTime) ? point : best, null); }
function periodForPoint(point: FaceShapePoint | null): FaceShapePeriod | null { return point ? { start: point.window_start ?? point.date, end: point.window_end ?? point.date } : null; }
function shiftDate(value: string, days: number) { const result = new Date(`${value}T12:00:00`); result.setDate(result.getDate() + days); return result.toISOString().slice(0, 10); }
function formatIndex(value: number | null | undefined) { if (value == null) return "—"; return `${value > 0 ? "+" : ""}${value.toFixed(1)}`; }
function formatSigned(value: number | null | undefined) { return formatIndex(value); }
function compactDate(value: string) { const parsed = new Date(`${value}T12:00:00`); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(undefined, { month: "short", year: "2-digit" }); }
function formatFullDate(value: string) { const parsed = new Date(`${value}T12:00:00`); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); }
function formatDateRange(start?: string | null, end?: string | null) { if (!start || !end) return "unknown dates"; return start === end ? formatFullDate(start) : `${formatFullDate(start)} – ${formatFullDate(end)}`; }
function capitalize(value: string) { return value ? value[0].toUpperCase() + value.slice(1) : value; }
function summarySentence(direction?: string, change?: number | null) { if (direction === "fuller") return `The recent trend is ${formatSigned(change)} fuller-like than roughly 90 days ago.`; if (direction === "leaner") return `The recent trend is ${formatSigned(change)} leaner-like than roughly 90 days ago.`; if (direction === "steady") return "There is no clear sustained change over roughly 90 days."; return "The recent direction is still forming."; }
function comparisonSentence(comparison: FaceShapeComparison) { if (comparison.conclusion === "no_clear_change") return "No clear shape change rises above the normal variation in these periods."; return `Period B appears ${Math.abs(comparison.delta).toFixed(1)} index units ${comparison.conclusion}-like compared with period A.`; }
function contourPath(points: Array<[number, number]>, a: Array<[number, number]>, b: Array<[number, number]>) { const all = [...a, ...b]; const xs = all.map((point) => point[0]); const ys = all.map((point) => point[1]); const minX = Math.min(...xs); const maxX = Math.max(...xs); const minY = Math.min(...ys); const maxY = Math.max(...ys); const spanX = Math.max(maxX - minX, 0.001); const spanY = Math.max(maxY - minY, 0.001); return points.map((point, index) => `${index ? "L" : "M"}${10 + ((point[0] - minX) / spanX) * 80},${8 + ((point[1] - minY) / spanY) * 84}`).join(" ") + " Z"; }
