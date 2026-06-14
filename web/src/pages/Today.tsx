import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CalendarDays,
  Camera,
  CheckCircle2,
  Clock,
  Film,
  Flame,
  Play,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { api, apiUrl, renderPlaybackUrl, renderPosterUrl, type TodayResponse } from "@/api/client";
import { Badge, Button, PageFrame, Panel, cn } from "@/components/ui";

export type TodayPageAction = "capture" | "video" | "timeline" | "settings" | "review";

export function Today({ onAction }: { onAction: (action: TodayPageAction) => void }) {
  const todayQuery = useQuery({
    queryKey: ["today"],
    queryFn: api.today,
    refetchInterval: 30_000,
  });
  const autoQuery = useQuery({ queryKey: ["auto-render"], queryFn: api.autoRender, refetchInterval: 60_000 });
  const today = todayQuery.data;
  const auto = autoQuery.data;

  const greeting = useMemo(() => greetingForNow(), []);
  const todayLabel = useMemo(() => formatLongDate(new Date()), []);
  const nextRunLabel = useMemo(() => (auto ? formatNextRun(auto.next_run_at) : null), [auto]);
  const photo = today?.today_photo ?? null;
  const dayCount = today?.total_days ?? 0;
  const streak = today?.streak ?? 0;
  const longest = today?.longest_streak ?? 0;
  const hasToday = today?.has_today ?? false;
  const captureLabel = hasToday ? "Retake today" : "Take today's selfie";

  return (
    <PageFrame size="phone">
      <header>
        <div className="text-xs font-bold uppercase tracking-[0.18em] text-ink/55">{greeting}</div>
        <h1 className="mt-1 text-3xl font-black tracking-tight text-ink">{todayLabel}</h1>
        <p className="mt-1 text-sm font-semibold text-ink/55">
          {hasToday ? "Today is locked in. The video updates overnight." : "One selfie keeps the streak alive."}
        </p>
      </header>

      <Panel className="overflow-hidden p-0">
        <div className={cn("relative aspect-square w-full", hasToday ? "bg-ink" : "bg-paper")}>
          {photo ? (
            <img
              src={apiUrl(photo.aligned_url || photo.image_url)}
              alt="Today's selfie"
              decoding="async"
              className="h-full w-full object-cover"
              onError={(event) => {
                const target = event.currentTarget;
                const fallback = apiUrl(photo.image_url);
                if (photo.image_url && target.src !== fallback) {
                  target.src = fallback;
                }
              }}
            />
          ) : (
            <EmptyHeroIllustration />
          )}
          {photo ? (
            <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-ink/85 via-ink/30 to-transparent" />
          ) : null}
          <div className="absolute inset-x-4 bottom-4 flex flex-col gap-3">
            {photo ? (
              <div className="flex items-end justify-between gap-3">
                <div>
                  <div className="text-xs font-black uppercase tracking-[0.18em] text-paper/70">Today</div>
                  <div className="text-lg font-black text-paper">{formatTime(photo.captured_at)}</div>
                </div>
                <div className="flex items-center gap-2">
                  {photo.skipped ? (
                    <Badge tone="bad">Needs review</Badge>
                  ) : (
                    <Badge tone="good">Locked in</Badge>
                  )}
                  {photo.quality_score != null ? (
                    <Badge>Score {photo.quality_score.toFixed(2)}</Badge>
                  ) : null}
                </div>
              </div>
            ) : null}
            <Button
              type="button"
              size="md"
              variant={hasToday ? "secondary" : "primary"}
              className="w-full"
              onClick={() => onAction("capture")}
            >
              <Camera className="h-5 w-5" />
              {captureLabel}
            </Button>
          </div>
        </div>
      </Panel>

      {photo?.skipped ? (
        <Panel className="border border-coral/35 bg-coral/10">
          <div className="flex items-start gap-3">
            <RefreshCw className="mt-0.5 h-5 w-5 text-coral" />
            <div>
              <div className="text-sm font-black text-coral">{humanSkipReason(photo.skip_reason)}</div>
              <p className="mt-1 text-xs font-semibold leading-5 text-ink/60">
                The auto-render will skip this frame. Retake to keep your streak smooth, or open Review to keep it anyway.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" onClick={() => onAction("capture")}>
                  Retake
                </Button>
                <Button size="sm" variant="secondary" onClick={() => onAction("review")}>
                  Open review
                </Button>
              </div>
            </div>
          </div>
        </Panel>
      ) : null}

      <div className="grid grid-cols-3 gap-3">
        <StreakStat icon={<Flame className="h-4 w-4 text-coral" />} label="Current" value={streak} suffix={streak === 1 ? "day" : "days"} />
        <StreakStat icon={<Sparkles className="h-4 w-4 text-amber" />} label="Best" value={longest} suffix={longest === 1 ? "day" : "days"} />
        <StreakStat icon={<CalendarDays className="h-4 w-4 text-teal" />} label="Total" value={dayCount} suffix={dayCount === 1 ? "day" : "days"} />
      </div>

      <Panel className="overflow-hidden p-0">
        <div className="flex items-center justify-between gap-3 border-b border-ink/10 px-4 py-3">
          <div className="flex items-center gap-2">
            <Film className="h-4 w-4 text-teal" />
            <h2 className="text-sm font-black uppercase tracking-[0.12em] text-ink">Latest video</h2>
          </div>
          <Button size="sm" variant="ghost" onClick={() => onAction("video")}>
            All videos
          </Button>
        </div>
        <div className="px-4 py-4">
          {today?.latest_render?.video_url ? (
            <div className="flex justify-center overflow-hidden rounded-md bg-ink">
              <video
                className="block h-auto max-h-[78vh] max-w-full bg-ink"
                src={renderPlaybackUrl(today.latest_render.id)}
                poster={renderPosterUrl(today.latest_render.id)}
                controls
                playsInline
                preload="none"
              />
            </div>
          ) : (
            <NoVideoState onTrigger={() => onAction("video")} />
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs font-semibold text-ink/55">
            <Clock className="h-3.5 w-3.5" />
            {today?.latest_render?.finished_at ? (
              <span>Last rendered {formatRelative(today.latest_render.finished_at)}</span>
            ) : (
              <span>No video yet</span>
            )}
          </div>
        </div>
      </Panel>

      <Panel>
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-amber" />
          <h2 className="text-sm font-black uppercase tracking-[0.12em] text-ink">Auto-render</h2>
        </div>
        <p className="mt-2 text-sm font-semibold text-ink/65">
          {auto?.enabled ? `Checks nightly at ${formatTimeLabel(auto.time)} and renders when inputs change.` : "Auto-render is paused."}
        </p>
        <div className="mt-3 flex flex-wrap gap-2 text-xs font-bold text-ink/55">
          {auto?.enabled ? <Badge tone="good">on</Badge> : <Badge tone="warn">paused</Badge>}
          {nextRunLabel ? <Badge>Next: {nextRunLabel}</Badge> : null}
          {auto?.has_pending_changes ? <Badge tone="warn">Changes queued</Badge> : null}
          {auto?.last_render?.status === "done" ? <Badge tone="good">Last build OK</Badge> : null}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => onAction("settings")}>
            Schedule
          </Button>
          <Button size="sm" onClick={() => onAction("video")}>
            <Play className="h-4 w-4" />
            Render now
          </Button>
        </div>
      </Panel>

      <BackgroundChecklist today={today} />
    </PageFrame>
  );
}

function NoVideoState({ onTrigger }: { onTrigger: () => void }) {
  return (
    <div className="rounded-md border border-dashed border-ink/15 bg-bone/60 p-6 text-center">
      <Film className="mx-auto h-8 w-8 text-ink/35" />
      <div className="mt-3 text-sm font-black text-ink">No video yet</div>
      <p className="mt-1 text-xs font-semibold leading-5 text-ink/55">
        After your first selfie the auto-render will create the very first timelapse.
      </p>
      <Button size="sm" variant="secondary" className="mt-4" onClick={onTrigger}>
        Open video tab
      </Button>
    </div>
  );
}

function StreakStat({
  icon,
  label,
  value,
  suffix,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  suffix: string;
}) {
  return (
    <div className="rounded-lg border border-ink/10 bg-paper p-3 shadow-line">
      <div className="flex items-center gap-1.5 text-[0.62rem] font-black uppercase tracking-[0.16em] text-ink/55">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-2xl font-black text-ink">{value.toLocaleString()}</div>
      <div className="text-[0.7rem] font-semibold text-ink/55">{suffix}</div>
    </div>
  );
}

function BackgroundChecklist({ today }: { today: TodayResponse | undefined }) {
  if (!today) return null;
  const items: { ok: boolean; label: string; detail?: string }[] = [
    { ok: Boolean(today.project), label: "Project ready", detail: today.project?.name ?? "Will be created on first capture" },
    { ok: today.canonical_ready, label: "Face anchor locked", detail: today.canonical_ready ? "Auto re-checked nightly" : "Builds after the first detect" },
    { ok: today.total_days > 0, label: `${today.total_days} day${today.total_days === 1 ? "" : "s"} cataloged` },
  ];
  return (
    <Panel>
      <div className="text-sm font-black uppercase tracking-[0.12em] text-ink">Pipeline</div>
      <ul className="mt-3 space-y-2">
        {items.map((item) => (
          <li key={item.label} className="flex items-start gap-2 text-sm font-semibold text-ink/70">
            <CheckCircle2 className={cn("mt-0.5 h-4 w-4", item.ok ? "text-teal" : "text-ink/25")} />
            <div>
              <div className={cn("font-black", item.ok ? "text-ink" : "text-ink/55")}>{item.label}</div>
              {item.detail ? <div className="text-xs font-semibold text-ink/45">{item.detail}</div> : null}
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function EmptyHeroIllustration() {
  return (
    <div className="grid h-full w-full place-items-center px-8 text-center">
      <div>
        <div className="mx-auto grid h-20 w-20 place-items-center rounded-full bg-ink/5">
          <Camera className="h-9 w-9 text-ink/55" />
        </div>
        <div className="mt-4 text-lg font-black text-ink">Today is open</div>
        <p className="mt-1 text-sm font-semibold leading-5 text-ink/55">
          Tap the capture button below to add the first frame for {formatShortDate(new Date())}.
        </p>
      </div>
    </div>
  );
}

function greetingForNow() {
  const hour = new Date().getHours();
  if (hour < 5) return "Late night";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  if (hour < 22) return "Good evening";
  return "Good night";
}

function formatLongDate(date: Date) {
  return date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

function formatShortDate(date: Date) {
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatTime(value: string) {
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function formatTimeLabel(value: string) {
  const [hourRaw, minuteRaw] = value.split(":");
  const hour = Number(hourRaw);
  const minute = Number(minuteRaw ?? 0);
  if (Number.isNaN(hour)) return value;
  const date = new Date();
  date.setHours(hour, Number.isNaN(minute) ? 0 : minute, 0, 0);
  return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function formatRelative(value: string) {
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value;
  const diffMs = Date.now() - date.getTime();
  const minutes = Math.round(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatNextRun(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  const isTomorrow = date.toDateString() === tomorrow.toDateString();
  const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (sameDay) return `today ${time}`;
  if (isTomorrow) return `tomorrow ${time}`;
  return `${date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} ${time}`;
}

function humanSkipReason(reason: string | null) {
  if (!reason) return "Needs review";
  const labels: Record<string, string> = {
    no_face_detected: "We could not find your face",
    landmarks_unavailable: "We saw a face but no detail map",
    low_quality: "The frame did not pass quality checks",
    landmark_outlier: "Frame is far from the average face",
    user_skipped: "You excluded this",
    replaced_by_newer_capture: "A newer take replaced this one",
  };
  return labels[reason] ?? "Needs review";
}
