import { useEffect, useRef, useState } from "react";
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
} from "lucide-react";
import type { Project } from "@/api/client";
import { Badge, Button, Metric, cn } from "@/components/ui";

export type PageKey = "setup" | "grid" | "outliers" | "stats" | "render" | "history";

const navItems: Array<{ key: PageKey; label: string; icon: typeof FolderOpen }> = [
  { key: "setup", label: "Setup", icon: FolderOpen },
  { key: "grid", label: "Photos", icon: Images },
  { key: "outliers", label: "Outliers", icon: ScanFace },
  { key: "stats", label: "Stats", icon: BarChart3 },
  { key: "render", label: "Render", icon: SlidersHorizontal },
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
          {currentProject ? <Badge tone="good">{currentProject.active_count} active</Badge> : null}
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
                  currentPage === item.key ? "bg-paper text-ink" : "text-paper/70 hover:bg-paper/8 hover:text-paper",
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
            <div className="grid grid-cols-3 gap-2 sm:min-w-[27rem]">
              <Metric label="Photos" value={currentProject?.photo_count ?? 0} />
              <Metric label="Active" value={currentProject?.active_count ?? 0} tone="good" />
              <Metric label="Skipped" value={currentProject?.skipped_count ?? 0} tone={currentProject?.skipped_count ? "warn" : "default"} />
            </div>
          </div>
        </header>
        <div className="px-4 py-5 md:px-6">{children}</div>
      </main>
    </div>
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
            {currentProject ? `${currentProject.photo_count} photos · ${currentProject.active_count} active` : "Create one from Setup"}
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
      <p className="mt-2 text-sm font-medium text-ink/60">Create a project with a source folder before running scan, detection, alignment, and render jobs.</p>
      <Button className="mt-5" onClick={onSetup}>
        Open setup
      </Button>
    </div>
  );
}
