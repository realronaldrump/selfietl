import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FolderOpen, FolderPlus, MousePointer2, Play, Radar, ScanFace } from "lucide-react";
import { api, type InboxStatus, type JobStatus, type Project } from "@/api/client";
import { JobStatus as JobStatusPanel } from "@/components/JobStatus";
import { Badge, Button, Input, Label, Metric, Panel } from "@/components/ui";
import { useJobEvents } from "@/hooks/useJobEvents";

export function Setup({
  currentProject,
  onProjectCreated,
}: {
  currentProject: Project | null;
  onProjectCreated: (id: number) => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("Selfie timelapse");
  const [sourceFolder, setSourceFolder] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoFlow, setAutoFlow] = useState<"idle" | "creating" | "scanning" | "detecting">("idle");
  const autoStartedRef = useRef(false);
  const scanStartedForRef = useRef<string | null>(null);
  const detectionStartedForRef = useRef<number | null>(null);
  const defaultSourceQuery = useQuery({ queryKey: ["default-source"], queryFn: api.defaultSource });
  const inboxStatusQuery = useQuery({
    queryKey: ["inbox-status"],
    queryFn: api.inboxStatus,
    refetchInterval: 3000,
  });

  const createMutation = useMutation({
    mutationFn: api.createProject,
    onSuccess: async (project) => {
      setError(null);
      onProjectCreated(project.id);
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      await queryClient.invalidateQueries({ queryKey: ["inbox-status"] });
      const started = await api.scan(project.id);
      scanStartedForRef.current = `${project.id}:${inboxStatusQuery.data?.supported_files ?? 0}`;
      setAutoFlow("scanning");
      setJobId(started.job_id);
    },
    onError: (err) => setError(err instanceof Error ? err.message : String(err)),
  });

  const onTerminal = useCallback(
    async (job: JobStatus) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["inbox-status"] });
      if (currentProject) {
        queryClient.invalidateQueries({ queryKey: ["photos", currentProject.id] });
      }
      const projectId = currentProject?.id ?? inboxStatusQuery.data?.project_id;
      if (job.status === "done" && autoFlow === "scanning" && projectId) {
        setAutoFlow("detecting");
        const started = await api.detect(projectId);
        setJobId(started.job_id);
      } else if (["done", "failed", "cancelled"].includes(job.status)) {
        setAutoFlow("idle");
      }
    },
    [autoFlow, currentProject, inboxStatusQuery.data?.project_id, queryClient],
  );
  const job = useJobEvents(jobId, onTerminal);

  useEffect(() => {
    if (!sourceFolder && defaultSourceQuery.data?.path) {
      setSourceFolder(defaultSourceQuery.data.path);
    }
  }, [defaultSourceQuery.data?.path, sourceFolder]);

  useEffect(() => {
    const status = inboxStatusQuery.data;
    if (!status || status.supported_files === 0 || autoStartedRef.current || createMutation.isPending) {
      return;
    }
    if (!currentProject && !status.project_id) {
      autoStartedRef.current = true;
      setAutoFlow("creating");
      createMutation.mutate({ name: "Inbox timelapse", source_folder: status.path });
    }
  }, [createMutation, currentProject, inboxStatusQuery.data]);

  useEffect(() => {
    const status = inboxStatusQuery.data;
    const projectId = currentProject?.id ?? status?.project_id;
    const scanKey = projectId && status ? `${projectId}:${status.supported_files}` : null;
    const activeJob = job && ["queued", "running"].includes(job.status);
    if (!status || !projectId || !scanKey || status.supported_files === 0 || activeJob || scanStartedForRef.current === scanKey) {
      return;
    }
    const isInboxProject = currentProject ? currentProject.source_folder === status.path : true;
    if (isInboxProject && status.needs_scan) {
      scanStartedForRef.current = scanKey;
      setAutoFlow("scanning");
      api
        .scan(projectId)
        .then((started) => setJobId(started.job_id))
        .catch((err) => {
          setAutoFlow("idle");
          setError(err instanceof Error ? err.message : String(err));
        });
    }
  }, [currentProject, inboxStatusQuery.data, job]);

  useEffect(() => {
    const status = inboxStatusQuery.data;
    const projectId = currentProject?.id ?? status?.project_id;
    const activeJob = job && ["queued", "running"].includes(job.status);
    const isInboxProject = currentProject && status ? currentProject.source_folder === status.path : Boolean(status?.project_id);
    if (!status || !projectId || activeJob || status.needs_scan || !status.needs_detection || !isInboxProject || detectionStartedForRef.current === projectId) {
      return;
    }
    detectionStartedForRef.current = projectId;
    setAutoFlow("detecting");
    api
      .detect(projectId)
      .then((started) => setJobId(started.job_id))
      .catch((err) => {
        setAutoFlow("idle");
        setError(err instanceof Error ? err.message : String(err));
      });
  }, [currentProject, inboxStatusQuery.data, job]);

  async function start(kind: "scan" | "detect" | "recompute") {
    if (!currentProject) return;
    setError(null);
    try {
      const startJob = kind === "scan" ? api.scan : kind === "detect" ? api.detect : api.recompute;
      const started = await startJob(currentProject.id);
      setJobId(started.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function chooseFolder() {
    setError(null);
    try {
      const picked = await api.pickFolder();
      setSourceFolder(picked.path);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function openSourceFolder() {
    setError(null);
    try {
      const response = await api.revealFolder(sourceFolder || defaultSourceQuery.data?.path);
      setSourceFolder(response.path);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
      <Panel>
        <div className="flex items-center gap-2">
          <FolderOpen className="h-5 w-5 text-teal" />
          <h2 className="text-xl font-black text-ink">Project setup</h2>
        </div>
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <div>
            <Label>Project name</Label>
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </div>
          <div>
            <Label>Source folder</Label>
            <Input
              value={sourceFolder}
              placeholder="/Users/davis/Pictures/Selfies"
              onChange={(event) => setSourceFolder(event.target.value)}
            />
            <div className="mt-2 flex flex-wrap gap-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={!defaultSourceQuery.data?.path}
                onClick={() => defaultSourceQuery.data?.path && setSourceFolder(defaultSourceQuery.data.path)}
              >
                <FolderPlus className="h-4 w-4" />
                Use app inbox
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={chooseFolder}>
                <MousePointer2 className="h-4 w-4" />
                Choose folder
              </Button>
              <Button type="button" variant="secondary" size="sm" onClick={openSourceFolder}>
                <FolderOpen className="h-4 w-4" />
                Open folder
              </Button>
            </div>
            <p className="mt-2 text-xs font-semibold leading-5 text-ink/55">
              The app inbox is created automatically at <span className="font-mono">{defaultSourceQuery.data?.path ?? "~/.selfietl/inbox"}</span>.
            </p>
          </div>
        </div>
        <InboxImportStatus status={inboxStatusQuery.data} autoFlow={autoFlow} />
        <div className="mt-5 flex flex-wrap gap-2">
          <Button
            disabled={createMutation.isPending || !name.trim() || !sourceFolder.trim()}
            onClick={() => createMutation.mutate({ name: name.trim(), source_folder: sourceFolder.trim() })}
          >
            <Play className="h-4 w-4" />
            Create and scan
          </Button>
          {currentProject ? (
            <>
              <Button variant="secondary" onClick={() => start("scan")}>
                <Radar className="h-4 w-4" />
                Scan folder
              </Button>
              <Button variant="secondary" onClick={() => start("detect")}>
                <ScanFace className="h-4 w-4" />
                Detect faces
              </Button>
              <Button variant="secondary" onClick={() => start("recompute")}>
                <CheckCircle2 className="h-4 w-4" />
                Recompute face
              </Button>
            </>
          ) : null}
        </div>
        {error ? <div className="mt-4 rounded-md bg-coral/10 p-3 text-sm font-semibold text-coral">{error}</div> : null}
      </Panel>

      <Panel>
        <div className="flex items-center justify-between gap-3">
          <h3 className="font-black text-ink">Current project</h3>
          {currentProject?.canonical_landmarks_path ? <Badge tone="good">canonical ready</Badge> : <Badge>setup</Badge>}
        </div>
        <dl className="mt-4 space-y-3 text-sm">
          <div>
            <dt className="font-bold text-ink/55">Name</dt>
            <dd className="mt-1 break-words font-semibold text-ink">{currentProject?.name ?? "None"}</dd>
          </div>
          <div>
            <dt className="font-bold text-ink/55">Folder</dt>
            <dd className="mt-1 break-all font-mono text-xs text-ink">{currentProject?.source_folder ?? "-"}</dd>
          </div>
        </dl>
      </Panel>

      <div className="xl:col-span-2">
        <JobStatusPanel job={job} onCancel={jobId ? () => api.cancelJob(jobId) : undefined} />
      </div>
    </div>
  );
}

function InboxImportStatus({
  status,
  autoFlow,
}: {
  status: InboxStatus | undefined;
  autoFlow: "idle" | "creating" | "scanning" | "detecting";
}) {
  if (!status || status.supported_files === 0) {
    return (
      <div className="mt-5 rounded-md border border-ink/10 bg-white p-3 text-sm font-semibold text-ink/55">
        Drop photos into the app inbox and this page will notice them automatically.
      </div>
    );
  }

  const stateText =
    autoFlow === "creating"
      ? "Creating inbox project"
      : autoFlow === "scanning"
        ? "Cataloging inbox photos"
        : autoFlow === "detecting"
          ? "Detecting faces"
          : status.needs_scan
            ? "Inbox has uncataloged files"
            : status.needs_detection
              ? "Cataloged photos are ready for face detection"
              : "Inbox is up to date";

  return (
    <div className="mt-5 rounded-lg border border-teal/25 bg-teal/10 p-3">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="text-sm font-black text-ink">{stateText}</div>
          <div className="mt-1 break-all font-mono text-xs font-semibold text-ink/55">{status.path}</div>
        </div>
        <Badge tone={status.needs_scan || status.needs_detection ? "warn" : "good"}>{autoFlow === "idle" ? "watching" : autoFlow}</Badge>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <Metric label="Files" value={status.supported_files} />
        <Metric label="Cataloged" value={status.cataloged_files} tone={status.cataloged_files ? "good" : "default"} />
        <Metric label="Detected" value={status.detected_files} tone={status.detected_files ? "good" : "default"} />
      </div>
    </div>
  );
}
