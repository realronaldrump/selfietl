import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, apiUrl, type Project } from "@/api/client";
import { Badge, Metric, PageFrame, Panel, cn } from "@/components/ui";

type StatsPayload = {
  timeline: Array<{ date: string; quality: number | null; skipped: boolean }>;
  pose: Array<{ date: string; yaw: number | null; pitch: number | null; roll: number | null }>;
  eye_open: number[];
  photos_by_month: Array<{ month: string; count: number }>;
  total: number;
  skipped: number;
};

export function Stats({ project }: { project: Project }) {
  const statsQuery = useQuery({
    queryKey: ["stats", project.id],
    queryFn: () => api.stats(project.id) as Promise<StatsPayload>,
  });
  const stats = statsQuery.data;
  const view = buildStatsView(stats, project);

  if (statsQuery.isLoading) {
    return (
      <PageFrame size="wide">
        <Panel className="min-h-[22rem] animate-pulse">
          <div className="h-5 w-32 rounded bg-ink/10" />
          <div className="mt-5 grid gap-3 md:grid-cols-4">
            {Array.from({ length: 4 }, (_, idx) => (
              <div key={idx} className="h-24 rounded-md bg-ink/8" />
            ))}
          </div>
          <div className="mt-5 h-48 rounded-md bg-ink/8" />
        </Panel>
      </PageFrame>
    );
  }

  if (statsQuery.isError) {
    return (
      <PageFrame size="narrow">
        <Panel className="border border-coral/20 bg-coral/10">
          <h1 className="text-xl font-black text-ink">Stats unavailable</h1>
          <p className="mt-2 text-sm font-semibold text-ink/60">{statsQuery.error.message}</p>
        </Panel>
      </PageFrame>
    );
  }

  if (!stats || stats.total === 0) {
    return (
      <PageFrame size="narrow">
        <Panel>
          <h1 className="text-2xl font-black tracking-tight text-ink">Stats</h1>
          <p className="mt-2 text-sm font-semibold text-ink/55">
            Capture or import a few selfies and this page will chart quality, coverage, pose drift, and capture cadence over time.
          </p>
        </Panel>
      </PageFrame>
    );
  }

  return (
    <PageFrame size="wide" className="space-y-5">
      <Panel className="overflow-hidden border border-ink/10 bg-[linear-gradient(135deg,#fff_0%,#f5f1e7_52%,#e8f2ef_100%)] p-0">
        <div className="grid gap-5 p-4 lg:grid-cols-[1fr_20rem] lg:p-5">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="good">{view.dateRange}</Badge>
              <Badge>{view.included.toLocaleString()} included</Badge>
              {view.skipped > 0 ? <Badge tone="warn">{view.skipped.toLocaleString()} review</Badge> : null}
            </div>
            <h1 className="mt-3 text-3xl font-black tracking-tight text-ink md:text-4xl">Selfie stats</h1>
            <p className="mt-2 max-w-2xl text-sm font-semibold leading-6 text-ink/60">
              Capture cadence, face quality, and alignment signals across {view.total.toLocaleString()} cataloged selfies.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-2 text-sm">
            <HeroNumber label="Current streak" value={`${view.currentStreakLength}d`} detail={view.currentStreakLabel} />
            <HeroNumber label="Longest streak" value={view.longestStreak ? `${view.longestStreak.length}d` : "-"} detail={view.longestStreak ? streakDateRange(view.longestStreak) : "No streaks yet"} />
            <HeroNumber label="Avg / month" value={view.avgPerMonth.toFixed(1)} />
            <HeroNumber label="Pose drift" value={formatDegrees(view.avgPoseDrift)} />
          </div>
        </div>
        <div className="h-2 bg-[linear-gradient(90deg,#1F7A75_0%,#C59A2D_48%,#C94F31_100%)]" />
      </Panel>

      <div className="grid gap-3 md:grid-cols-4">
        <Metric label="Cataloged" value={view.total.toLocaleString()} />
        <Metric label="Included" value={`${view.activeRatio}%`} tone="good" />
        <Metric label="Avg quality" value={formatDecimal(view.avgQuality)} tone={view.avgQuality != null && view.avgQuality >= 0.7 ? "good" : "default"} />
        <Metric label="Review rate" value={`${view.reviewRatio}%`} tone={view.reviewRatio > 10 ? "warn" : "default"} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.35fr_0.65fr]">
        <ChartPanel
          title="Quality over time"
          subtitle="Scaled to your observed quality range so small dips are visible. Dashed line marks the average."
        >
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={view.timeline}>
              <CartesianGrid stroke="#1114121c" vertical={false} />
              <XAxis dataKey="label" minTickGap={32} tick={{ fontSize: 11, fill: "#11141299" }} />
              <YAxis
                domain={view.qualityDomain}
                ticks={view.qualityTicks}
                tick={{ fontSize: 11, fill: "#11141299" }}
                tickFormatter={formatAxisDecimal}
                width={42}
              />
              <Tooltip content={<QualityTooltip />} />
              {view.avgQuality != null ? <ReferenceLine y={view.avgQuality} stroke="#C59A2D" strokeDasharray="4 4" strokeOpacity={0.7} /> : null}
              <Area type="monotone" dataKey="quality" fill="#1F7A7520" stroke="none" connectNulls />
              <Line type="monotone" dataKey="quality" stroke="#1F7A75" strokeWidth={2.4} dot={false} connectNulls />
              <Scatter dataKey="flaggedQuality" fill="#C94F31" />
            </ComposedChart>
          </ResponsiveContainer>
        </ChartPanel>

        <div className="grid gap-4">
          <StreaksPanel
            currentStreakLength={view.currentStreakLength}
            currentStreakLabel={view.currentStreakLabel}
            bestStreaks={view.bestStreaks}
          />
          <CoveragePanel
            coverageLabel={view.coverageLabel}
            months={view.months}
            peakMonthCount={view.peakMonthCount}
            firstDateLabel={view.firstDateLabel}
            lastDateLabel={view.lastDateLabel}
          />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartPanel
          title="Photos by month"
          subtitle="Cadence view for spotting import spikes, gaps, and recent capture momentum."
        >
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={view.months}>
              <CartesianGrid stroke="#1114121c" vertical={false} />
              <XAxis dataKey="label" minTickGap={20} tick={{ fontSize: 11, fill: "#11141299" }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#11141299" }} width={34} />
              <Tooltip content={<MonthTooltip />} />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {view.months.map((month) => (
                  <Cell key={month.month} fill={month.count === view.peakMonthCount ? "#C59A2D" : "#202522"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartPanel>

        <ChartPanel
          title="Pose drift"
          subtitle="Yaw, pitch, and roll over time. Tighter lines usually produce steadier videos."
        >
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={view.pose}>
              <CartesianGrid stroke="#1114121c" vertical={false} />
              <XAxis dataKey="label" minTickGap={32} tick={{ fontSize: 11, fill: "#11141299" }} />
              <YAxis tick={{ fontSize: 11, fill: "#11141299" }} width={34} tickFormatter={(value) => `${value}deg`} />
              <Tooltip content={<PoseTooltip />} />
              <ReferenceLine y={0} stroke="#111412" strokeOpacity={0.18} />
              <Line type="monotone" dataKey="yaw" name="Yaw" stroke="#1F7A75" strokeWidth={2} dot={false} connectNulls />
              <Line type="monotone" dataKey="pitch" name="Pitch" stroke="#C59A2D" strokeWidth={2} dot={false} connectNulls />
              <Line type="monotone" dataKey="roll" name="Roll" stroke="#C94F31" strokeWidth={2} dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
          <div className="mt-3 flex flex-wrap gap-2 text-xs font-black uppercase tracking-[0.12em]">
            <LegendChip color="bg-teal" label="Yaw" />
            <LegendChip color="bg-amber" label="Pitch" />
            <LegendChip color="bg-coral" label="Roll" />
          </div>
        </ChartPanel>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
        <ChartPanel
          title="Eye-open distribution"
          subtitle="Histogram of detected eye-open ratio across measured selfies."
        >
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={histogram(stats.eye_open)}>
              <CartesianGrid stroke="#1114121c" vertical={false} />
              <XAxis dataKey="bucket" tick={{ fontSize: 11, fill: "#11141299" }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#11141299" }} width={34} />
              <Tooltip cursor={{ fill: "#1114120c" }} />
              <Bar dataKey="count" fill="#C59A2D" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartPanel>

        <ChartPanel
          title="Rolling capture volume"
          subtitle="A cumulative readout of how the archive has grown."
        >
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={view.timeline}>
              <defs>
                <linearGradient id="captureGrowth" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="#1F7A75" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#1F7A75" stopOpacity={0.04} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#1114121c" vertical={false} />
              <XAxis dataKey="label" minTickGap={32} tick={{ fontSize: 11, fill: "#11141299" }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#11141299" }} width={40} />
              <Tooltip content={<GrowthTooltip />} />
              <Area type="monotone" dataKey="cumulative" stroke="#1F7A75" strokeWidth={2.4} fill="url(#captureGrowth)" />
            </AreaChart>
          </ResponsiveContainer>
        </ChartPanel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="font-black text-ink">Average face</h3>
              <p className="mt-1 text-sm font-semibold text-ink/55">Composite face anchor from included captures.</p>
            </div>
            <Badge tone="good">anchor</Badge>
          </div>
          <img className="mt-3 aspect-video w-full rounded-md bg-ink object-contain" src={apiUrl(`/projects/${project.id}/avg-face`)} alt="" />
        </Panel>
        <Panel>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="font-black text-ink">Landmark drift heatmap</h3>
              <p className="mt-1 text-sm font-semibold text-ink/55">Hotter zones show where landmarks vary most.</p>
            </div>
            <Badge>drift</Badge>
          </div>
          <img className="mt-3 aspect-video w-full rounded-md bg-ink object-contain" src={apiUrl(`/projects/${project.id}/heatmap`)} alt="" />
        </Panel>
      </div>
    </PageFrame>
  );
}

function ChartPanel({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <Panel>
      <div className="mb-3">
        <h3 className="font-black text-ink">{title}</h3>
        <p className="mt-1 text-sm font-semibold text-ink/55">{subtitle}</p>
      </div>
      {children}
    </Panel>
  );
}

function histogram(values: number[]) {
  const buckets = Array.from({ length: 8 }, (_, idx) => ({ bucket: `${(idx * 0.05).toFixed(2)}+`, count: 0 }));
  for (const value of values) {
    const idx = Math.max(0, Math.min(buckets.length - 1, Math.floor(value / 0.05)));
    buckets[idx].count += 1;
  }
  return buckets;
}

function buildStatsView(stats: StatsPayload | undefined, project: Project) {
  const timelineRaw = stats?.timeline ?? [];
  const poseRaw = stats?.pose ?? [];
  const total = stats?.total ?? project.photo_count;
  const skipped = stats?.skipped ?? project.skipped_count;
  const included = Math.max(0, total - skipped);
  let cumulative = 0;
  const qualityValues = timelineRaw.map((item) => item.quality).filter(isNumber);
  const timeline = timelineRaw.map((item) => {
    cumulative += 1;
    return {
      ...item,
      label: compactDate(item.date),
      quality: item.skipped ? null : item.quality,
      flaggedQuality: item.skipped ? item.quality ?? 0.05 : null,
      fullDate: formatDate(item.date),
      cumulative,
    };
  });
  const pose = poseRaw.map((item) => ({
    ...item,
    label: compactDate(item.date),
    fullDate: formatDate(item.date),
  }));
  const months = (stats?.photos_by_month ?? []).map((item) => ({
    ...item,
    label: monthLabel(item.month),
  }));
  const firstDate = timelineRaw[0]?.date ?? null;
  const lastDate = timelineRaw[timelineRaw.length - 1]?.date ?? null;
  const spanDays = firstDate && lastDate ? Math.max(1, daysBetween(firstDate, lastDate) + 1) : 0;
  const today = isoDate(new Date());
  const yesterday = shiftIsoDay(today, -1);
  const streakDays = timelineRaw.map((item) => toIsoDay(item.date)).filter((day) => day < today);
  const streaks = buildStreaks(streakDays);
  const bestStreaks = [...streaks]
    .sort((a, b) => b.length - a.length || b.end.localeCompare(a.end))
    .slice(0, 3);
  const latestStreak = streaks[streaks.length - 1] ?? null;
  const currentStreak = latestStreak?.end === yesterday ? latestStreak : null;
  const qualityScale = qualityAxisScale(qualityValues);
  const avgPoseDrift = average(
    poseRaw.map((item) => {
      const values = [item.yaw, item.pitch, item.roll].filter(isNumber);
      return values.length ? average(values.map((value) => Math.abs(value))) : null;
    }).filter(isNumber),
  );

  return {
    total,
    skipped,
    included,
    timeline,
    pose,
    months,
    dateRange: firstDate && lastDate ? `${compactDate(firstDate)} - ${compactDate(lastDate)}` : "No dates",
    firstDateLabel: firstDate ? compactDate(firstDate) : "-",
    lastDateLabel: lastDate ? compactDate(lastDate) : "-",
    coverageLabel: spanDays > 365 ? `${(spanDays / 365).toFixed(1)} years` : `${spanDays} days`,
    activeRatio: total ? Math.round((included / total) * 100) : 0,
    reviewRatio: total ? Math.round((skipped / total) * 100) : 0,
    avgQuality: average(qualityValues),
    qualityDomain: qualityScale.domain,
    qualityTicks: qualityScale.ticks,
    avgPerMonth: months.length ? total / months.length : 0,
    peakMonthCount: months.reduce((max, item) => Math.max(max, item.count), 0),
    currentStreakLength: currentStreak?.length ?? 0,
    currentStreakLabel: currentStreak
      ? `${streakDateRange(currentStreak)} through yesterday`
      : latestStreak
        ? `Last completed-day selfie ${formatFullDate(latestStreak.end)}`
        : "No completed-day selfies yet",
    longestStreak: bestStreaks[0] ?? null,
    bestStreaks,
    avgPoseDrift,
  };
}

function StreaksPanel({
  currentStreakLength,
  currentStreakLabel,
  bestStreaks,
}: {
  currentStreakLength: number;
  currentStreakLabel: string;
  bestStreaks: Streak[];
}) {
  return (
    <Panel>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-black uppercase tracking-[0.12em] text-ink/55">Streaks</h2>
          <div className="mt-2 text-3xl font-black text-ink">{currentStreakLength} days</div>
          <p className="mt-1 text-sm font-semibold text-ink/55">Current streak: {currentStreakLabel}</p>
          <p className="mt-1 text-xs font-bold uppercase tracking-[0.12em] text-ink/40">Today counts tomorrow</p>
        </div>
        <Badge tone={currentStreakLength > 0 ? "good" : "warn"}>{currentStreakLength > 0 ? "active" : "paused"}</Badge>
      </div>

      <div className="mt-4 space-y-2">
        {bestStreaks.length ? (
          bestStreaks.map((streak, index) => <StreakRow key={`${streak.start}-${streak.end}`} rank={index + 1} streak={streak} />)
        ) : (
          <div className="rounded-md bg-white p-3 text-sm font-semibold text-ink/55">No streaks yet.</div>
        )}
      </div>
    </Panel>
  );
}

function StreakRow({ rank, streak }: { rank: number; streak: Streak }) {
  return (
    <div className="grid grid-cols-[2.5rem_4rem_1fr] items-center gap-2 rounded-md bg-white p-2 shadow-line">
      <div className="text-xs font-black uppercase tracking-[0.12em] text-ink/45">#{rank}</div>
      <div className="text-lg font-black text-ink">{streak.length}d</div>
      <div className="min-w-0 text-xs font-bold text-ink/60">{streakDateRange(streak)}</div>
    </div>
  );
}

function CoveragePanel({
  coverageLabel,
  months,
  peakMonthCount,
  firstDateLabel,
  lastDateLabel,
}: {
  coverageLabel: string;
  months: Array<{ month: string; label: string; count: number }>;
  peakMonthCount: number;
  firstDateLabel: string;
  lastDateLabel: string;
}) {
  return (
    <Panel className="grid content-between gap-4">
      <div>
        <h2 className="text-sm font-black uppercase tracking-[0.12em] text-ink/55">Coverage</h2>
        <div className="mt-2 text-3xl font-black text-ink">{coverageLabel}</div>
        <p className="mt-1 text-sm font-semibold text-ink/55">from first to latest captured selfie</p>
      </div>
      <CalendarStrip months={months} peak={peakMonthCount} />
      <div className="grid grid-cols-3 gap-2">
        <MiniStat label="First" value={firstDateLabel} />
        <MiniStat label="Latest" value={lastDateLabel} />
        <MiniStat label="Months" value={String(months.length)} />
      </div>
    </Panel>
  );
}

function CalendarStrip({ months, peak }: { months: Array<{ month: string; label: string; count: number }>; peak: number }) {
  const visible = months.slice(-24);
  return (
    <div className="grid grid-cols-12 gap-1">
      {visible.map((month) => {
        const intensity = peak ? month.count / peak : 0;
        return (
          <div
            key={month.month}
            className="h-12 rounded-sm border border-ink/10"
            title={`${month.label}: ${month.count}`}
            style={{ backgroundColor: `rgba(31, 122, 117, ${0.12 + intensity * 0.78})` }}
          />
        );
      })}
    </div>
  );
}

function HeroNumber({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="rounded-md border border-ink/10 bg-white/75 p-3 shadow-line">
      <div className="text-[0.62rem] font-black uppercase tracking-[0.16em] text-ink/45">{label}</div>
      <div className="mt-2 text-2xl font-black text-ink">{value}</div>
      {detail ? <div className="mt-1 truncate text-[0.68rem] font-bold text-ink/50">{detail}</div> : null}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-white p-2">
      <div className="text-[0.6rem] font-black uppercase tracking-[0.14em] text-ink/45">{label}</div>
      <div className="mt-1 truncate text-sm font-black text-ink">{value}</div>
    </div>
  );
}

function LegendChip({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-ink/55">
      <span className={cn("h-2 w-2 rounded-full", color)} />
      {label}
    </span>
  );
}

function QualityTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload;
  return <ChartTooltip title={item.fullDate} lines={[`Quality ${formatDecimal(item.quality ?? item.flaggedQuality)}`, item.skipped ? "Flagged for review" : "Included"]} />;
}

function PoseTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload;
  return (
    <ChartTooltip
      title={item.fullDate}
      lines={[
        `Yaw ${formatDegrees(item.yaw)}`,
        `Pitch ${formatDegrees(item.pitch)}`,
        `Roll ${formatDegrees(item.roll)}`,
      ]}
    />
  );
}

function MonthTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload;
  return <ChartTooltip title={item.label} lines={[`${item.count.toLocaleString()} photos`]} />;
}

function GrowthTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload;
  return <ChartTooltip title={item.fullDate} lines={[`${item.cumulative.toLocaleString()} total photos`]} />;
}

function ChartTooltip({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="rounded-md border border-ink/10 bg-white px-3 py-2 text-xs font-bold text-ink shadow-line">
      <div className="mb-1 text-ink/50">{title}</div>
      {lines.map((line) => (
        <div key={line}>{line}</div>
      ))}
    </div>
  );
}

type TooltipProps = {
  active?: boolean;
  payload?: Array<{ payload: Record<string, any> }>;
};

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function average(values: number[]) {
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function formatDecimal(value: number | null) {
  return value == null ? "-" : value.toFixed(2);
}

function formatAxisDecimal(value: number) {
  const rounded = Number(value.toFixed(2));
  if (Number.isInteger(rounded)) return String(rounded);
  return rounded.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function formatDegrees(value: number | null) {
  return value == null ? "-" : `${Math.round(value)}deg`;
}

function compactDate(value: string) {
  const date = localDateFromIsoDay(toIsoDay(value));
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatDate(value: string) {
  const date = localDateFromIsoDay(toIsoDay(value));
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function formatFullDate(day: string) {
  const date = localDateFromIsoDay(toIsoDay(day));
  if (Number.isNaN(date.getTime())) return day;
  return date.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" });
}

function monthLabel(value: string) {
  const [year, month] = value.split("-").map(Number);
  if (!year || !month) return value;
  return new Date(year, month - 1, 1).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

function daysBetween(start: string, end: string) {
  const startParts = toIsoDay(start).split("-").map(Number);
  const endParts = toIsoDay(end).split("-").map(Number);
  if (startParts.length !== 3 || endParts.length !== 3 || startParts.some(Number.isNaN) || endParts.some(Number.isNaN)) return 0;
  const startTime = Date.UTC(startParts[0], startParts[1] - 1, startParts[2]);
  const endTime = Date.UTC(endParts[0], endParts[1] - 1, endParts[2]);
  return Math.round((endTime - startTime) / 86_400_000);
}

type Streak = {
  start: string;
  end: string;
  length: number;
};

function buildStreaks(dates: string[]) {
  const days = Array.from(new Set(dates.map(toIsoDay).filter(Boolean))).sort();
  const streaks: Streak[] = [];
  let start: string | null = null;
  let end: string | null = null;
  for (const day of days) {
    if (!start || !end || daysBetween(end, day) !== 1) {
      if (start && end) streaks.push({ start, end, length: daysBetween(start, end) + 1 });
      start = day;
    }
    end = day;
  }
  if (start && end) streaks.push({ start, end, length: daysBetween(start, end) + 1 });
  return streaks;
}

function streakDateRange(streak: Streak) {
  if (streak.start === streak.end) return formatFullDate(streak.start);
  return `${formatFullDate(streak.start)} - ${formatFullDate(streak.end)}`;
}

function qualityAxisScale(values: number[]) {
  if (!values.length) return { domain: [0, 1] as [number, number], ticks: [0, 0.25, 0.5, 0.75, 1] };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 0.04);
  let lower = Math.max(0, min - span * 0.18);
  let upper = Math.min(1, max + span * 0.08);
  if (upper - lower < 0.08) {
    const middle = (min + max) / 2;
    lower = Math.max(0, middle - 0.04);
    upper = Math.min(1, middle + 0.04);
    if (upper === 1) lower = Math.max(0, 0.92);
    if (lower === 0) upper = Math.min(1, 0.08);
  }
  lower = Math.max(0, Number(lower.toFixed(2)));
  upper = Math.min(1, Number(upper.toFixed(2)));
  const step = (upper - lower) / 4;
  const ticks = Array.from({ length: 5 }, (_, index) => Number((lower + step * index).toFixed(2)));
  return { domain: [lower, upper] as [number, number], ticks };
}

function toIsoDay(value: string) {
  return value.slice(0, 10);
}

function localDateFromIsoDay(day: string) {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date);
}

function shiftIsoDay(day: string, offset: number) {
  const [year, month, date] = day.split("-").map(Number);
  if (!year || !month || !date) return day;
  return new Date(Date.UTC(year, month - 1, date + offset)).toISOString().slice(0, 10);
}

function isoDate(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
