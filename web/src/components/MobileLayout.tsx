import { CalendarDays, Camera, Film, Home, MoreHorizontal } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api, type JobStatus } from "@/api/client";
import { Button, ProgressBar, cn } from "@/components/ui";

export type MobileTab = "today" | "timeline" | "video" | "more";
export type MobileNavTarget = MobileTab | "capture";

export function MobileLayout({
  active,
  onChange,
  children,
}: {
  active: MobileTab | "capture" | "settings";
  onChange: (tab: MobileNavTarget) => void;
  children: React.ReactNode;
}) {
  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs,
    // Poll quickly while something is running, but back off when idle so we are
    // not hammering the API (and the phone's radio) every 1.5s all day long.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((job) => job.status === "queued" || job.status === "running") ? 1500 : 10_000,
  });
  const visibleJob = jobsQuery.data?.find((job) => ["queued", "running"].includes(job.status)) ?? null;

  return (
    <div className="min-h-screen bg-bone pb-[calc(5rem+env(safe-area-inset-bottom))]">
      <main className="mx-auto max-w-screen-sm px-4 pt-[max(env(safe-area-inset-top),1rem)]">
        {visibleJob ? <ActivityBar job={visibleJob} /> : null}
        <div className="pb-6">{children}</div>
      </main>
      <nav
        className="fixed inset-x-0 bottom-0 z-30 border-t border-ink/10 bg-paper/95 backdrop-blur"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        <div className="mx-auto grid max-w-screen-sm grid-cols-5">
          <TabButton active={isTabActive(active, "today")} icon={<Home className="h-5 w-5" />} label="Today" onClick={() => onChange("today")} />
          <TabButton
            active={isTabActive(active, "timeline")}
            icon={<CalendarDays className="h-5 w-5" />}
            label="Timeline"
            onClick={() => onChange("timeline")}
          />
          <CaptureTabButton active={active === "capture"} onClick={() => onChange("capture")} />
          <TabButton active={isTabActive(active, "video")} icon={<Film className="h-5 w-5" />} label="Video" onClick={() => onChange("video")} />
          <TabButton
            active={isTabActive(active, "more")}
            icon={<MoreHorizontal className="h-5 w-5" />}
            label="More"
            onClick={() => onChange("more")}
          />
        </div>
      </nav>
    </div>
  );
}

function isTabActive(active: string, tab: MobileTab) {
  if (active === tab) return true;
  if (tab === "today" && active === "capture") return true;
  if (tab === "video" && active === "settings") return true;
  return false;
}

function TabButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      aria-label={label}
      className={cn(
        "flex min-h-14 flex-col items-center justify-center gap-0.5 text-[0.62rem] font-black uppercase tracking-[0.16em] transition",
        active ? "text-ink" : "text-ink/45 hover:text-ink/65",
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function CaptureTabButton({ active, onClick }: { active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Open capture menu"
      className="flex min-h-14 flex-col items-center justify-center"
    >
      <span
        className={cn(
          "grid h-12 w-12 -translate-y-3 place-items-center rounded-full border-4 border-paper bg-coral text-paper shadow-lg transition",
          active ? "scale-105 ring-4 ring-coral/25" : "hover:scale-105",
        )}
      >
        <Camera className="h-5 w-5" />
      </span>
    </button>
  );
}

function ActivityBar({ job }: { job: JobStatus }) {
  const percent = Math.round((job.progress ?? 0) * 100);
  return (
    <div className="-mx-4 mb-3 border-b border-ink/10 bg-paper/95 px-4 pb-3 pt-3 backdrop-blur">
      <div className="flex items-center justify-between text-xs font-black uppercase tracking-[0.12em] text-ink/55">
        <span>{humanJob(job.name, job.stage)}</span>
        <div className="flex items-center gap-2">
          <span>{percent}%</span>
          {["queued", "running"].includes(job.status) ? (
            <Button size="sm" variant="ghost" className="!min-h-8 !px-2 text-[0.62rem]" onClick={() => api.cancelJob(job.id)}>
              Cancel
            </Button>
          ) : null}
        </div>
      </div>
      <div className="mt-2">
        <ProgressBar value={job.progress ?? 0} />
      </div>
    </div>
  );
}

export function MoreLink({
  icon,
  label,
  description,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-start gap-3 rounded-md border border-ink/10 bg-paper p-3 text-left shadow-line transition hover:border-teal/40"
    >
      <div className="grid h-10 w-10 place-items-center rounded-md bg-ink text-paper">{icon}</div>
      <div>
        <div className="text-sm font-black text-ink">{label}</div>
        <div className="mt-0.5 text-xs font-semibold text-ink/55">{description}</div>
      </div>
      <MoreHorizontal className="ml-auto mt-1 h-4 w-4 text-ink/30" />
    </button>
  );
}

function humanJob(name: string, stage: string | null) {
  const labels: Record<string, string> = {
    capture: "Saving today's selfie",
    detect: "Finding your face",
    canonical: "Updating face anchor",
    align: "Aligning frames",
    render: "Rendering video",
    render_frames: "Rendering video",
    ffmpeg: "Encoding MP4",
    auto_render: "Building tonight's video",
  };
  const key = stage ?? name.split(":")[0];
  return labels[key] ?? "Working";
}
