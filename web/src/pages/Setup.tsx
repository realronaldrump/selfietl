import { useCallback, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FolderOpen, FolderPlus, MousePointer2, Play, Radar, ScanFace } from "lucide-react";
import { api, type JobStatus, type Project } from "@/api/client";
import { JobStatus as JobStatusPanel } from "@/components/JobStatus";
import { Badge, Button, Input, Label, Panel } from "@/components/ui";
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
  const defaultSourceQuery = useQuery({ queryKey: ["default-source"], queryFn: api.defaultSource });

  const onTerminal = useCallback(
    (_job: JobStatus) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      if (currentProject) {
        queryClient.invalidateQueries({ queryKey: ["photos", currentProject.id] });
      }
    },
    [currentProject, queryClient],
  );
  const job = useJobEvents(jobId, onTerminal);

  useEffect(() => {
    if (!sourceFolder && defaultSourceQuery.data?.path) {
      setSourceFolder(defaultSourceQuery.data.path);
    }
  }, [defaultSourceQuery.data?.path, sourceFolder]);

  const createMutation = useMutation({
    mutationFn: api.createProject,
    onSuccess: async (project) => {
      setError(null);
      onProjectCreated(project.id);
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      const started = await api.scan(project.id);
      setJobId(started.job_id);
    },
    onError: (err) => setError(err instanceof Error ? err.message : String(err)),
  });

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
