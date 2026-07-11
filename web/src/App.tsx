import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { api } from "@/api/client";
import { EmptyProject, Layout, type PageKey } from "@/components/Layout";
import { MobileLayout, type MobileTab } from "@/components/MobileLayout";
import type { MoreTarget } from "@/pages/More";
import type { TodayPageAction } from "@/pages/Today";

// Pages are code-split so the initial mobile bundle stays small. The heavier
// admin views (Stats pulls in recharts, Render, etc.) only download on demand.
const AutoRenderSettings = lazy(() => import("@/pages/AutoRenderSettings").then((m) => ({ default: m.AutoRenderSettings })));
const Capture = lazy(() => import("@/pages/Capture").then((m) => ({ default: m.Capture })));
const Grid = lazy(() => import("@/pages/Grid").then((m) => ({ default: m.Grid })));
const History = lazy(() => import("@/pages/History").then((m) => ({ default: m.History })));
const More = lazy(() => import("@/pages/More").then((m) => ({ default: m.More })));
const Outliers = lazy(() => import("@/pages/Outliers").then((m) => ({ default: m.Outliers })));
const Render = lazy(() => import("@/pages/Render").then((m) => ({ default: m.Render })));
const Setup = lazy(() => import("@/pages/Setup").then((m) => ({ default: m.Setup })));
const Stats = lazy(() => import("@/pages/Stats").then((m) => ({ default: m.Stats })));
const Timeline = lazy(() => import("@/pages/Timeline").then((m) => ({ default: m.Timeline })));
const Progress = lazy(() => import("@/pages/Progress").then((m) => ({ default: m.Progress })));
const FaceChange = lazy(() => import("@/pages/FaceChange").then((m) => ({ default: m.FaceChange })));
const Hair = lazy(() => import("@/pages/Hair").then((m) => ({ default: m.Hair })));
const Today = lazy(() => import("@/pages/Today").then((m) => ({ default: m.Today })));
const Video = lazy(() => import("@/pages/Video").then((m) => ({ default: m.Video })));

function PageLoading() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center" role="status" aria-label="Loading">
      <Loader2 className="h-6 w-6 animate-spin text-ink/40" />
    </div>
  );
}

const PROJECT_KEY = "selfietl.projectId";
const LAYOUT_KEY = "selfietl.layout";

type LayoutMode = "auto" | "mobile" | "desktop";

type MobilePage = MobileTab | "capture" | "settings" | "review" | "render" | "stats" | "history" | "grid" | "setup";

export default function App() {
  const [layoutMode, setLayoutMode] = useState<LayoutMode>(() => {
    const stored = localStorage.getItem(LAYOUT_KEY) as LayoutMode | null;
    return stored ?? "auto";
  });
  const [isWide, setIsWide] = useState(() => window.matchMedia("(min-width: 1024px)").matches);

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)");
    const handler = (event: MediaQueryListEvent) => setIsWide(event.matches);
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }, []);

  useEffect(() => {
    localStorage.setItem(LAYOUT_KEY, layoutMode);
  }, [layoutMode]);

  const useMobile = layoutMode === "mobile" || (layoutMode === "auto" && !isWide);

  if (useMobile) return <MobileApp onSwitchToDesktop={() => setLayoutMode("desktop")} />;
  return <DesktopApp onSwitchToMobile={() => setLayoutMode("mobile")} />;
}

function MobileApp({ onSwitchToDesktop }: { onSwitchToDesktop: () => void }) {
  const [page, setPage] = useState<MobilePage>(() => initialPageFromUrl("today"));

  function handleTodayAction(action: TodayPageAction) {
    if (action === "capture") return setPage("capture");
    if (action === "video") return setPage("video");
    if (action === "timeline") return setPage("timeline");
    if (action === "shape") return setPage("timeline");
    if (action === "settings") return setPage("settings");
    if (action === "review") return setPage("review");
  }

  function handleMoreNavigate(target: MoreTarget) {
    if (target === "auto-render-settings") return setPage("settings");
    return setPage(target);
  }

  function content() {
    switch (page) {
      case "today":
        return <Today onAction={handleTodayAction} />;
      case "timeline":
        return <Progress />;
      case "video":
        return <Video onSettings={() => setPage("settings")} />;
      case "more":
        return <More onNavigate={handleMoreNavigate} onSwitchToDesktop={onSwitchToDesktop} />;
      case "capture":
        return <Capture onBack={() => setPage("today")} onDone={() => setPage("today")} />;
      case "settings":
        return <AutoRenderSettings onBack={() => setPage("video")} />;
      case "review":
        return <ReviewBridge onBack={() => setPage("today")} />;
      case "render":
        return <RenderBridge onBack={() => setPage("video")} />;
      case "stats":
        return <StatsBridge onBack={() => setPage("more")} />;
      case "history":
        return <HistoryBridge onBack={() => setPage("video")} />;
      case "grid":
        return <GridBridge onBack={() => setPage("more")} />;
      case "setup":
        return <SetupBridge onBack={() => setPage("more")} />;
    }
  }

  const activeTab: MobileTab | "capture" | "settings" =
    page === "today" || page === "timeline" || page === "video" || page === "more"
      ? page
      : page === "capture"
        ? "capture"
        : page === "settings"
          ? "settings"
          : page === "review" || page === "render" || page === "stats" || page === "history" || page === "grid" || page === "setup"
            ? "more"
            : "today";

  return (
    <MobileLayout active={activeTab} onChange={(tab) => setPage(tab)}>
      <Suspense fallback={<PageLoading />}>{content()}</Suspense>
    </MobileLayout>
  );
}

function ReviewBridge({ onBack }: { onBack: () => void }) {
  return <ProjectScopedPage title="Review" onBack={onBack}>{(project) => <Outliers project={project} />}</ProjectScopedPage>;
}
function RenderBridge({ onBack }: { onBack: () => void }) {
  return <ProjectScopedPage title="Custom render" onBack={onBack}>{(project) => <Render project={project} />}</ProjectScopedPage>;
}
function StatsBridge({ onBack }: { onBack: () => void }) {
  return <ProjectScopedPage title="Stats" onBack={onBack}>{(project) => <Stats project={project} />}</ProjectScopedPage>;
}
function HistoryBridge({ onBack }: { onBack: () => void }) {
  return <ProjectScopedPage title="History" onBack={onBack}>{(project) => <History project={project} />}</ProjectScopedPage>;
}
function GridBridge({ onBack }: { onBack: () => void }) {
  return <ProjectScopedPage title="All photos" onBack={onBack}>{(project) => <Grid project={project} />}</ProjectScopedPage>;
}
function SetupBridge({ onBack }: { onBack: () => void }) {
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const project = projectsQuery.data?.[0] ?? null;
  return (
    <div className="space-y-3">
      <BackHeader title="Setup" onBack={onBack} />
      <Setup currentProject={project} onProjectCreated={() => {}} onRender={() => onBack()} />
    </div>
  );
}

function ProjectScopedPage({
  title,
  onBack,
  children,
}: {
  title: string;
  onBack: () => void;
  children: (project: NonNullable<Awaited<ReturnType<typeof api.projects>>>[number]) => React.ReactNode;
}) {
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const project = projectsQuery.data?.[0] ?? null;
  return (
    <div className="space-y-3">
      <BackHeader title={title} onBack={onBack} />
      {project ? (
        children(project)
      ) : (
        <EmptyProject onSetup={onBack} />
      )}
    </div>
  );
}

function BackHeader({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <div className="flex items-center justify-between">
      <button
        type="button"
        className="min-h-10 rounded-md px-2 text-xs font-black uppercase tracking-[0.16em] text-ink/55 hover:bg-ink/5"
        onClick={onBack}
      >
        ← Back
      </button>
      <div className="text-xs font-bold uppercase tracking-[0.18em] text-ink/55">{title}</div>
      <div className="w-10" />
    </div>
  );
}

function DesktopApp({ onSwitchToMobile }: { onSwitchToMobile: () => void }) {
  const [page, setPage] = useState<PageKey>(() => initialPageFromUrl("today") as PageKey);
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(() => {
    const stored = localStorage.getItem(PROJECT_KEY);
    return stored ? Number(stored) : null;
  });
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const projects = projectsQuery.data ?? [];

  useEffect(() => {
    if (!selectedProjectId && projects.length > 0) {
      setSelectedProjectId(projects[0].id);
    }
  }, [projects, selectedProjectId]);

  useEffect(() => {
    if (selectedProjectId) localStorage.setItem(PROJECT_KEY, String(selectedProjectId));
  }, [selectedProjectId]);

  const currentProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? projects[0] ?? null,
    [projects, selectedProjectId],
  );

  const content = (() => {
    if (page === "today") {
      return (
        <Today
          onAction={(action) => {
            if (action === "capture") setPage("capture" as PageKey);
            else if (action === "video") setPage("video" as PageKey);
            else if (action === "timeline") setPage("timeline" as PageKey);
            else if (action === "shape") setPage("shape");
            else if (action === "settings") setPage("settings" as PageKey);
            else if (action === "review") setPage("outliers");
          }}
        />
      );
    }
    if (page === ("capture" as PageKey)) return <Capture onBack={() => setPage("today")} onDone={() => setPage("today")} />;
    if (page === ("timeline" as PageKey)) return <Timeline />;
    if (page === "shape") return currentProject ? <FaceChange project={currentProject} /> : <EmptyProject onSetup={() => setPage("setup")} />;
    if (page === "hair") return currentProject ? <Hair project={currentProject} /> : <EmptyProject onSetup={() => setPage("setup")} />;
    if (page === ("video" as PageKey)) return <Video onSettings={() => setPage("settings" as PageKey)} />;
    if (page === ("settings" as PageKey)) return <AutoRenderSettings onBack={() => setPage("video" as PageKey)} />;
    if (page === "setup") {
      return <Setup currentProject={currentProject} onProjectCreated={(id) => setSelectedProjectId(id)} onRender={() => setPage("render")} />;
    }
    if (!currentProject) return <EmptyProject onSetup={() => setPage("setup")} />;
    if (page === "grid") return <Grid project={currentProject} mode="all" />;
    if (page === "included") return <Grid project={currentProject} mode="included" />;
    if (page === "outliers") return <Outliers project={currentProject} />;
    if (page === "stats") return <Stats project={currentProject} />;
    if (page === "render") return <Render project={currentProject} />;
    return <History project={currentProject} />;
  })();

  return (
    <Layout
      projects={projects}
      currentProject={currentProject}
      currentPage={page}
      onPageChange={setPage}
      onProjectChange={setSelectedProjectId}
      onSwitchToMobile={onSwitchToMobile}
    >
      <Suspense fallback={<PageLoading />}>{content}</Suspense>
    </Layout>
  );
}

function initialPageFromUrl(fallback: MobilePage): MobilePage {
  const action = new URLSearchParams(window.location.search).get("action");
  if (action === "capture") return "capture";
  if (action === "video") return "video";
  if (action === "timeline") return "timeline";
  if (action === "shape") return "timeline";
  if (action === "hair") return "timeline";
  return fallback;
}
