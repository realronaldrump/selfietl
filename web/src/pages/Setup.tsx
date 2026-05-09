import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, FolderOpen, FolderPlus, MousePointer2, Play, Radar, RotateCcw, ScanFace } from "lucide-react";
import { api, type InboxStatus, type JobStatus, type Project } from "@/api/client";
import { JobStatus as JobStatusPanel } from "@/components/JobStatus";
import { Badge, Button, Input, Label, Metric, PageFrame, Panel } from "@/components/ui";
import { useJobEvents } from "@/hooks/useJobEvents";

export function Setup({
  currentProject,
  onProjectCreated,
  onRender,
}: {
  currentProject: Project | null;
  onProjectCreated: (id: number) => void;
  onRender: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("Selfie timelapse");
  const [sourceFolder, setSourceFolder] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoFlow, setAutoFlow] = useState<"idle" | "creating" | "scanning" | "detecting" | "canonical">("idle");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const autoStartedRef = useRef(false);
  const scanStartedForRef = useRef<string | null>(null);
  const detectionStartedForRef = useRef<number | null>(null);
  const canonicalStartedForRef = useRef<number | null>(null);
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
      } else if (job.status === "done" && autoFlow === "detecting" && projectId) {
        setAutoFlow("canonical");
        const started = await api.recompute(projectId);
        setJobId(started.job_id);
      } else if (["done", "failed", "cancelled"].includes(job.status)) {
        setAutoFlow("idle");
      }
    },
    [autoFlow, currentProject, inboxStatusQuery.data?.project_id, queryClient],
  );
  const job = useJobEvents(jobId, onTerminal);
  const activeJob = Boolean(job && ["queued", "running"].includes(job.status));
  const inboxReady = Boolean(
    inboxStatusQuery.data &&
      currentProject?.canonical_landmarks_path &&
      !inboxStatusQuery.data.needs_scan &&
      !inboxStatusQuery.data.needs_detection,
  );

  useEffect(() => {
    if (!sourceFolder && currentProject?.source_folder) {
      setSourceFolder(currentProject.source_folder);
    } else if (!sourceFolder && defaultSourceQuery.data?.path) {
      setSourceFolder(defaultSourceQuery.data.path);
    }
  }, [currentProject?.source_folder, defaultSourceQuery.data?.path, sourceFolder]);

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
    if (!status || !projectId || activeJob || status.needs_scan || status.needs_detection || currentProject?.canonical_landmarks_path || canonicalStartedForRef.current === projectId) {
      return;
    }
    canonicalStartedForRef.current = projectId;
    setAutoFlow("canonical");
    api
      .recompute(projectId)
      .then((started) => setJobId(started.job_id))
      .catch((err) => {
        setAutoFlow("idle");
        setError(err instanceof Error ? err.message : String(err));
      });
  }, [activeJob, currentProject, inboxStatusQuery.data]);

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
      setAutoFlow(kind === "scan" ? "scanning" : kind === "detect" ? "detecting" : "canonical");
      const started = await startJob(currentProject.id);
      setJobId(started.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function processInbox() {
    const status = inboxStatusQuery.data;
    const projectId = currentProject?.id ?? status?.project_id;
    setError(null);
    if (!status) return;
    setSourceFolder(status.path);
    if (!projectId) {
      setAutoFlow("creating");
      createMutation.mutate({ name: "Inbox timelapse", source_folder: status.path });
      return;
    }
    onProjectCreated(projectId);
    if (status.needs_scan) {
      setAutoFlow("scanning");
      const started = await api.scan(projectId);
      setJobId(started.job_id);
    } else if (status.needs_detection) {
      setAutoFlow("detecting");
      const started = await api.detect(projectId);
      setJobId(started.job_id);
    } else if (!currentProject?.canonical_landmarks_path) {
      setAutoFlow("canonical");
      const started = await api.recompute(projectId);
      setJobId(started.job_id);
    }
  }

  async function resetEverything() {
    if (!confirmReset) {
      setConfirmReset(true);
      return;
    }
    setError(null);
    try {
      const result = await api.resetAppData();
      setJobId(null);
      setAutoFlow("idle");
      autoStartedRef.current = false;
      scanStartedForRef.current = null;
      detectionStartedForRef.current = null;
      canonicalStartedForRef.current = null;
      setConfirmReset(false);
      setSourceFolder(result.inbox_path);
      await queryClient.invalidateQueries();
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
    <PageFrame size="wide" className="grid gap-4 space-y-0 xl:grid-cols-[minmax(0,1fr)_24rem]">
      <Panel>
        <div className="flex items-center gap-2">
          <FolderOpen className="h-5 w-5 text-teal" />
          <h2 className="text-xl font-black text-ink">Start here</h2>
        </div>
        <InboxImportStatus status={inboxStatusQuery.data} autoFlow={autoFlow} />
        <div className="mt-5 flex flex-wrap gap-2">
          <Button
            disabled={createMutation.isPending || activeJob || !inboxStatusQuery.data?.supported_files}
            onClick={inboxReady ? onRender : processInbox}
          >
            <Play className="h-4 w-4" />
            {primaryActionLabel(inboxStatusQuery.data, currentProject, inboxReady)}
          </Button>
          <Button type="button" variant="secondary" onClick={openSourceFolder}>
            <FolderOpen className="h-4 w-4" />
            Open inbox
          </Button>
          {activeJob && jobId ? (
            <Button type="button" variant="danger" onClick={() => api.cancelJob(jobId)}>
              Cancel current step
            </Button>
          ) : null}
        </div>

        <button
          type="button"
          className="mt-5 flex min-h-11 items-center gap-2 rounded-md px-2 text-sm font-black text-ink/65 hover:bg-ink/5"
          onClick={() => setShowAdvanced((value) => !value)}
        >
          <ChevronDown className={`h-4 w-4 transition-transform ${showAdvanced ? "rotate-180" : ""}`} />
          Advanced controls
        </button>

        {showAdvanced ? (
          <div className="mt-3 rounded-lg border border-ink/10 bg-white p-4">
            <div className="grid gap-4 md:grid-cols-2">
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
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
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
              <Button
                size="sm"
                disabled={createMutation.isPending || !name.trim() || !sourceFolder.trim()}
                onClick={() => createMutation.mutate({ name: name.trim(), source_folder: sourceFolder.trim() })}
              >
                Create project
              </Button>
            </div>
            <div className="mt-4 flex flex-wrap gap-2 border-t border-ink/10 pt-4">
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
            <div className="mt-4 border-t border-ink/10 pt-4">
              <Button variant={confirmReset ? "danger" : "secondary"} onClick={resetEverything}>
                <RotateCcw className="h-4 w-4" />
                {confirmReset ? "Confirm reset app data" : "Reset app data"}
              </Button>
              <p className="mt-2 text-xs font-semibold leading-5 text-ink/50">
                Reset stops current work, clears the catalog, cached landmarks, aligned copies, and exports. It does not delete files in the inbox or modify original photos.
              </p>
            </div>
          </div>
        ) : null}
        {error ? <div className="mt-4 rounded-md bg-coral/10 p-3 text-sm font-semibold text-coral">{error}</div> : null}
      </Panel>

      <Panel>
        <div className="flex items-center justify-between gap-3">
          <h3 className="font-black text-ink">What happens</h3>
          {currentProject?.canonical_landmarks_path ? <Badge tone="good">ready</Badge> : <Badge>preparing</Badge>}
        </div>
        <div className="mt-4 space-y-3">
          <WorkflowStep index="1" title="Read the dates" body="EXIF DateTimeOriginal first. If AgeLapse removed it, filenames like 2021-03-02_14-12-40.jpg are used automatically." />
          <WorkflowStep index="2" title="Find the face" body="Each photo gets face landmarks and a quality check. Bad matches go to Review instead of breaking the movie." />
          <WorkflowStep index="3" title="Lock the eyes" body="The app builds an average face and aligns every photo to that steady anchor." />
          <WorkflowStep index="4" title="Create video" body="Create video generates the final MP4 from aligned photos and smooth in-between frames." />
        </div>
        <PipelineVisual />
      </Panel>

      <div className="xl:col-span-2">
        <JobStatusPanel job={job} onCancel={jobId ? () => api.cancelJob(jobId) : undefined} />
      </div>
    </PageFrame>
  );
}

function WorkflowStep({ index, title, body }: { index: string; title: string; body: string }) {
  return (
    <div className="grid grid-cols-[2rem_1fr] gap-3 rounded-md bg-white p-3 shadow-line">
      <div className="grid h-8 w-8 place-items-center rounded-md bg-ink text-sm font-black text-paper">{index}</div>
      <div>
        <div className="text-sm font-black text-ink">{title}</div>
        <div className="mt-1 break-words text-xs font-semibold leading-5 text-ink/55">{body}</div>
      </div>
    </div>
  );
}

function PipelineVisual() {
  return (
    <div className="mt-5 rounded-lg bg-white p-3 shadow-line">
      <div className="text-xs font-bold uppercase tracking-[0.08em] text-ink/45">Visual guide</div>
      <div className="mt-3 grid grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-2">
        <MiniFrame label="Original" faceOffset="translate-x-[-10px] rotate-[-7deg]" />
        <Arrow />
        <MiniFrame label="Aligned" faceOffset="" />
        <Arrow />
        <MiniFrame label="Video" faceOffset="" pulse />
      </div>
    </div>
  );
}

function Arrow() {
  return <div className="h-0.5 w-5 rounded-full bg-coral/70" />;
}

function MiniFrame({ label, faceOffset, pulse = false }: { label: string; faceOffset: string; pulse?: boolean }) {
  return (
    <div>
      <div className="relative aspect-[4/5] overflow-hidden rounded-md bg-bone shadow-line">
        <div className={`absolute left-1/2 top-[34%] h-12 w-10 -translate-x-1/2 rounded-full border-2 border-teal/70 bg-teal/10 ${faceOffset} ${pulse ? "animate-pulse" : ""}`}>
          <span className="absolute left-2 top-4 h-1.5 w-1.5 rounded-full bg-ink" />
          <span className="absolute right-2 top-4 h-1.5 w-1.5 rounded-full bg-ink" />
          <span className="absolute bottom-3 left-1/2 h-1 w-4 -translate-x-1/2 rounded-full bg-coral/80" />
        </div>
        <div className="absolute left-1/2 top-[46%] h-px w-12 -translate-x-1/2 bg-coral/40" />
      </div>
      <div className="mt-2 text-center text-[0.68rem] font-black text-ink/60">{label}</div>
    </div>
  );
}

function InboxImportStatus({
  status,
  autoFlow,
}: {
  status: InboxStatus | undefined;
  autoFlow: "idle" | "creating" | "scanning" | "detecting" | "canonical";
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
          : autoFlow === "canonical"
            ? "Locking the average face"
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

function primaryActionLabel(status: InboxStatus | undefined, currentProject: Project | null, inboxReady: boolean) {
  if (!status || status.supported_files === 0) return "Waiting for photos";
  if (inboxReady) return "Create video";
  if (!currentProject && !status.project_id) return "Import inbox";
  if (status.needs_scan) return "Catalog new photos";
  if (status.needs_detection) return "Detect faces";
  if (!currentProject?.canonical_landmarks_path) return "Prepare alignment";
  return "Ready to create video";
}
