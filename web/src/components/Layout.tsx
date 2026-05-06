import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Check,
  ChevronDown,
  Clock3,
  FolderOpen,
  Images,
  PlaySquare,
  ScanFace,
  Settings2,
  SlidersHorizontal,
  XCircle,
} from "lucide-react";
import type { Project } from "@/api/client";
import { api, type JobStatus } from "@/api/client";
import { Badge, Button, ProgressBar, cn } from "@/components/ui";

export type PageKey = "setup" | "grid" | "included" | "outliers" | "stats" | "render" | "history";

const navItems: Array<{ key: PageKey; label: string; icon: typeof FolderOpen }> = [
  { key: "setup", label: "Setup", icon: FolderOpen },
  { key: "grid", label: "Photos", icon: Images },
  { key: "outliers", label: "Review", icon: ScanFace },
  { key: "stats", label: "Details", icon: BarChart3 },
  { key: "render", label: "Create video", icon: SlidersHorizontal },
  { key: "history", label: "History", icon: Clock3 },
];

export function Layout({
  projects,
  currentProject,
  currentPage,
  onPageChange,
  onProjectChange,
  children,
}: {
  projects: Project[];
  currentProject: Project | null;
  currentPage: PageKey;
  onPageChange: (page: PageKey) => void;
  onProjectChange: (id: number) => void;
  children: React.ReactNode;
}) {
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: api.jobs, refetchInterval: 1200 });
  const currentJob = pickVisibleJob(jobsQuery.data ?? []);

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[17rem_1fr]">
      <aside className="bg-ink text-paper lg:min-h-screen">
        <div className="flex items-center justify-between border-b border-paper/10 px-4 py-4 lg:block lg:px-5 lg:py-6">
          <div>
            <div className="flex items-center gap-2 text-lg font-black">
              <PlaySquare className="h-5 w-5 text-coral" />
              SelfieTL
            </div>
            <div className="mt-1 text-xs font-semibold text-paper/45">Local face-anchored timelapse</div>
          </div>
          {currentProject ? <Badge tone="good">{currentProject.active_count} included</Badge> : null}
        </div>

        <div className="border-b border-paper/10 p-4 lg:p-5">
          <div className="text-xs font-bold uppercase tracking-[0.08em] text-paper/55">Project</div>
          <ProjectDropdown projects={projects} currentProject={currentProject} onProjectChange={onProjectChange} />
        </div>

        <nav className="flex gap-1 overflow-x-auto p-2 lg:block lg:p-3">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                onClick={() => onPageChange(item.key)}
                className={cn(
                  "flex min-h-11 shrink-0 items-center gap-3 rounded-md px-3 text-sm font-bold transition lg:w-full",
                  isNavActive(currentPage, item.key) ? "bg-paper text-ink" : "text-paper/70 hover:bg-paper/8 hover:text-paper",
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </button>
            );
          })}
        </nav>
      </aside>

      <main className="min-w-0">
        <header className="border-b border-ink/10 bg-paper/88 px-4 py-4 backdrop-blur md:px-6">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Settings2 className="h-4 w-4 text-teal" />
                <h1 className="truncate text-2xl font-black text-ink">{currentProject?.name ?? "Project setup"}</h1>
              </div>
              <p className="mt-1 truncate text-sm font-medium text-ink/55">{currentProject?.source_folder ?? "Create a local project to begin"}</p>
            </div>
            <div className="flex flex-wrap gap-2 text-sm font-black">
              <StatusPill active={currentPage === "grid"} disabled={!currentProject} onClick={() => onPageChange("grid")}>
                {currentProject?.photo_count ?? 0} photos
              </StatusPill>
              <StatusPill tone="good" active={currentPage === "included"} disabled={!currentProject} onClick={() => onPageChange("included")}>
                {currentProject?.active_count ?? 0} included
              </StatusPill>
              <StatusPill
                tone={currentProject?.skipped_count ? "warn" : "default"}
                active={currentPage === "outliers"}
                disabled={!currentProject}
                onClick={() => onPageChange("outliers")}
              >
                {currentProject?.skipped_count ?? 0} to review
              </StatusPill>
            </div>
          </div>
          <GlobalProgress
            job={currentJob}
            onCancel={currentJob && ["queued", "running"].includes(currentJob.status) ? () => api.cancelJob(currentJob.id) : undefined}
          />
        </header>
        <div className="px-4 py-5 md:px-6">{children}</div>
      </main>
    </div>
  );
}

function pickVisibleJob(jobs: JobStatus[]) {
  return jobs.find((job) => ["queued", "running"].includes(job.status)) ?? null;
}

function isNavActive(currentPage: PageKey, navPage: PageKey) {
  if (navPage === "grid") return currentPage === "grid" || currentPage === "included";
  return currentPage === navPage;
}

function GlobalProgress({ job, onCancel }: { job: JobStatus | null; onCancel?: () => void }) {
  if (!job) return null;
  const percent = Math.round((job.progress ?? 0) * 100);
  const title = job.status === "running" || job.status === "queued" ? plainJobName(job.name, job.stage) : job.status === "failed" ? "Something needs attention" : "Stopped";
  return (
    <div className="mt-4 rounded-md border border-teal/25 bg-white p-3 shadow-line">
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <div className="text-sm font-black text-ink">{title}</div>
          <div className="mt-1 truncate text-xs font-semibold text-ink/55">{job.message ?? "Working"}</div>
        </div>
        <div className="shrink-0 text-sm font-black text-ink">
          {job.progress_total > 0 ? `${job.progress_done.toLocaleString()} / ${job.progress_total.toLocaleString()}` : `${percent}%`}
        </div>
        {onCancel ? (
          <Button type="button" variant="danger" size="sm" onClick={onCancel}>
            <XCircle className="h-4 w-4" />
            Cancel
          </Button>
        ) : null}
      </div>
      <div className="mt-3">
        <ProgressBar value={job.progress ?? 0} />
      </div>
    </div>
  );
}

function plainJobName(name: string, stage: string | null) {
  const raw = stage ?? name.split(":")[0];
  const labels: Record<string, string> = {
    scan: "Reading your photo folder",
    detect: "Finding and measuring faces",
    canonical: "Choosing the steady face anchor",
    align: "Locking each face into place",
    render: "Creating the video",
    prepare_video: "Preparing video frames",
    render_frames: "Creating video frames",
    ffmpeg: "Saving the movie file",
    cancel: "Stopping",
  };
  return labels[raw] ?? "Working";
}

function StatusPill({
  children,
  tone = "default",
  active = false,
  disabled = false,
  onClick,
}: {
  children: React.ReactNode;
  tone?: "default" | "good" | "warn";
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
}) {
  const tones = {
    default: "border-ink/10 bg-white text-ink hover:border-ink/25",
    good: "border-teal/25 bg-teal/10 text-teal hover:border-teal/45",
    warn: "border-coral/25 bg-coral/10 text-coral hover:border-coral/45",
  };
  const className = cn(
    "inline-flex min-h-9 items-center rounded-md border px-3 transition",
    tones[tone],
    active && "ring-2 ring-ink/10",
    onClick && "cursor-pointer hover:-translate-y-0.5 hover:shadow-line focus:outline-none focus:ring-2 focus:ring-teal/30",
    disabled && "cursor-default opacity-55 hover:translate-y-0",
  );
  if (!onClick) return <span className={className}>{children}</span>;
  return (
    <button type="button" className={className} disabled={disabled} onClick={onClick}>
      {children}
    </button>
  );
}

function ProjectDropdown({
  projects,
  currentProject,
  onProjectChange,
}: {
  projects: Project[];
  currentProject: Project | null;
  onProjectChange: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const hasProjects = projects.length > 0;

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function choose(project: Project) {
    onProjectChange(project.id);
    setOpen(false);
  }

  return (
    <div ref={rootRef} className="relative mt-2">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex min-h-14 w-full items-center justify-between gap-3 rounded-md border px-3 text-left shadow-line transition",
          "border-paper/20 bg-paper text-ink hover:border-coral/65 focus:outline-none focus:ring-2 focus:ring-coral/45",
          open && "border-coral/75 ring-2 ring-coral/30",
        )}
      >
        <span className="min-w-0">
          <span className={cn("block truncate text-sm font-black", hasProjects ? "text-ink" : "text-ink/75")}>
            {currentProject?.name ?? "No projects yet"}
          </span>
          <span className="mt-0.5 block truncate text-xs font-semibold text-ink/55">
            {currentProject ? `${currentProject.photo_count} photos · ${currentProject.active_count} included` : "Create one from Setup"}
          </span>
        </span>
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-ink text-paper">
          <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} />
        </span>
      </button>

      {open ? (
        <div
          role="listbox"
          className="absolute left-0 right-0 z-30 mt-2 max-h-72 overflow-auto rounded-md border border-paper/15 bg-graphite p-1 shadow-2xl"
        >
          {hasProjects ? (
            projects.map((project) => {
              const selected = project.id === currentProject?.id;
              return (
                <button
                  key={project.id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => choose(project)}
                  className={cn(
                    "flex min-h-12 w-full items-center gap-3 rounded px-3 text-left transition",
                    selected ? "bg-paper text-ink" : "text-paper hover:bg-paper/10",
                  )}
                >
                  <span
                    className={cn(
                      "grid h-7 w-7 shrink-0 place-items-center rounded border",
                      selected ? "border-ink/15 bg-ink text-paper" : "border-paper/15 bg-paper/5 text-paper/70",
                    )}
                  >
                    {selected ? <Check className="h-4 w-4" /> : <FolderOpen className="h-4 w-4" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-black">{project.name ?? `Project ${project.id}`}</span>
                    <span className={cn("mt-0.5 block truncate text-xs font-semibold", selected ? "text-ink/55" : "text-paper/45")}>
                      {project.photo_count} photos · {project.source_folder}
                    </span>
                  </span>
                </button>
              );
            })
          ) : (
            <div className="rounded bg-paper/8 px-3 py-4 text-sm font-semibold text-paper">
              No projects yet
              <div className="mt-1 text-xs text-paper/55">Use Setup to create a project from a local folder.</div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

export function EmptyProject({ onSetup }: { onSetup: () => void }) {
  return (
    <div className="mx-auto mt-16 max-w-xl rounded-lg bg-paper p-6 text-center shadow-line">
      <FolderOpen className="mx-auto h-10 w-10 text-teal" />
      <h2 className="mt-4 text-2xl font-black text-ink">No project selected</h2>
      <p className="mt-2 text-sm font-medium text-ink/60">Open Setup and drop photos into the inbox. The app will prepare them and then create a video.</p>
      <Button className="mt-5" onClick={onSetup}>
        Open setup
      </Button>
    </div>
  );
}
