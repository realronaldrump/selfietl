import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { EmptyProject, Layout, type PageKey } from "@/components/Layout";
import { Grid } from "@/pages/Grid";
import { History } from "@/pages/History";
import { Outliers } from "@/pages/Outliers";
import { Render } from "@/pages/Render";
import { Setup } from "@/pages/Setup";
import { Stats } from "@/pages/Stats";

const PROJECT_KEY = "selfietl.projectId";

export default function App() {
  const [page, setPage] = useState<PageKey>("setup");
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
    if (page === "setup") {
      return <Setup currentProject={currentProject} onProjectCreated={(id) => setSelectedProjectId(id)} />;
    }
    if (!currentProject) return <EmptyProject onSetup={() => setPage("setup")} />;
    if (page === "grid") return <Grid project={currentProject} />;
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
    >
      {content}
    </Layout>
  );
}
