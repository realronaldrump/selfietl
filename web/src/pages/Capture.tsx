import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Camera,
  CheckCircle2,
  ImagePlus,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { api, type JobStatus } from "@/api/client";
import { Badge, Button, Panel, ProgressBar, cn } from "@/components/ui";
import { useJobEvents } from "@/hooks/useJobEvents";

type CaptureStep = "pick" | "preview" | "uploading" | "result";

export function Capture({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<CaptureStep>("pick");
  const [selected, setSelected] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const libraryInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!selected) {
      setPreviewUrl((current) => {
        if (current) URL.revokeObjectURL(current);
        return null;
      });
      return;
    }
    const url = URL.createObjectURL(selected);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [selected]);

  const onTerminal = useCallback(
    (job: JobStatus) => {
      queryClient.invalidateQueries({ queryKey: ["today"] });
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      if (job.status === "failed" || job.status === "cancelled") {
        setError(job.error ?? job.message ?? "Capture failed");
      }
    },
    [queryClient],
  );
  const job = useJobEvents(jobId, onTerminal);
  const result = job?.result as null | {
    skipped?: boolean;
    skip_reason?: string | null;
    quality_score?: number | null;
    aligned?: boolean;
    duplicate_of?: string | null;
  };

  const captureMutation = useMutation({
    mutationFn: async (file: File) => api.capture(file, new Date().toISOString()),
    onMutate: () => setStep("uploading"),
    onSuccess: (started) => {
      setError(null);
      setJobId(started.job_id);
      setStep("result");
    },
    onError: (err: Error) => {
      setError(err.message);
      setStep("preview");
    },
  });

  function pickFromCamera() {
    setError(null);
    cameraInputRef.current?.click();
  }
  function pickFromLibrary() {
    setError(null);
    libraryInputRef.current?.click();
  }
  function reset() {
    setSelected(null);
    setJobId(null);
    setError(null);
    setStep("pick");
  }

  const filename = useMemo(() => selected?.name ?? "selfie.jpg", [selected]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Button size="sm" variant="ghost" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
          Back
        </Button>
        <div className="text-xs font-bold uppercase tracking-[0.18em] text-ink/55">
          {stepLabel(step)}
        </div>
        <div className="w-12" />
      </div>

      <input
        ref={cameraInputRef}
        type="file"
        accept="image/*"
        capture="user"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            setSelected(file);
            setStep("preview");
          }
          event.target.value = "";
        }}
      />
      <input
        ref={libraryInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) {
            setSelected(file);
            setStep("preview");
          }
          event.target.value = "";
        }}
      />

      {step === "pick" ? <PickStep onCamera={pickFromCamera} onLibrary={pickFromLibrary} /> : null}

      {step === "preview" && selected && previewUrl ? (
        <PreviewStep
          previewUrl={previewUrl}
          filename={filename}
          fileSize={selected.size}
          uploading={captureMutation.isPending}
          onConfirm={() => captureMutation.mutate(selected)}
          onRetake={pickFromCamera}
          onChoose={pickFromLibrary}
          error={error}
        />
      ) : null}

      {(step === "uploading" || step === "result") && previewUrl ? (
        <ResultStep
          previewUrl={previewUrl}
          job={job}
          result={result ?? null}
          onRetake={() => {
            reset();
            pickFromCamera();
          }}
          onDone={onDone}
          error={error}
        />
      ) : null}
    </div>
  );
}

function PickStep({ onCamera, onLibrary }: { onCamera: () => void; onLibrary: () => void }) {
  return (
    <div className="space-y-3">
      <Panel>
        <h2 className="text-xl font-black text-ink">Take today's selfie</h2>
        <p className="mt-1 text-sm font-semibold leading-6 text-ink/65">
          Use the camera button to open the iPhone camera with the front lens already selected. After you capture,
          you'll get one chance to confirm before it joins the timelapse.
        </p>
      </Panel>
      <Panel className="overflow-hidden p-0">
        <CameraTipsHero />
      </Panel>
      <div className="grid grid-cols-1 gap-3">
        <Button onClick={onCamera}>
          <Camera className="h-5 w-5" />
          Open camera
        </Button>
        <Button variant="secondary" onClick={onLibrary}>
          <ImagePlus className="h-5 w-5" />
          Choose from library
        </Button>
      </div>
      <Panel>
        <h3 className="text-xs font-black uppercase tracking-[0.18em] text-ink/55">Quality tips</h3>
        <ul className="mt-3 space-y-2 text-sm font-semibold text-ink/70">
          <li className="flex items-start gap-2">
            <CheckCircle2 className="mt-0.5 h-4 w-4 text-teal" />
            Look straight at the camera, eyes open, neutral expression.
          </li>
          <li className="flex items-start gap-2">
            <CheckCircle2 className="mt-0.5 h-4 w-4 text-teal" />
            Bright, even light. Same room/time daily keeps colors steady.
          </li>
          <li className="flex items-start gap-2">
            <CheckCircle2 className="mt-0.5 h-4 w-4 text-teal" />
            Keep your face centered with shoulders square to the camera.
          </li>
        </ul>
      </Panel>
    </div>
  );
}

function CameraTipsHero() {
  return (
    <div className="relative aspect-[4/5] w-full bg-ink">
      <div className="absolute inset-0 grid place-items-center">
        <svg viewBox="0 0 100 125" className="h-full w-full">
          <defs>
            <linearGradient id="capture-bg" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#202522" />
              <stop offset="100%" stopColor="#111412" />
            </linearGradient>
          </defs>
          <rect width="100" height="125" fill="url(#capture-bg)" />
          <ellipse cx="50" cy="60" rx="22" ry="29" fill="rgba(196,79,49,0.12)" stroke="rgba(196,79,49,0.65)" strokeWidth="0.6" strokeDasharray="2 2" />
          <line x1="14" y1="60" x2="86" y2="60" stroke="rgba(244,240,231,0.25)" strokeDasharray="2 4" strokeWidth="0.5" />
          <line x1="50" y1="20" x2="50" y2="100" stroke="rgba(244,240,231,0.25)" strokeDasharray="2 4" strokeWidth="0.5" />
          <circle cx="42" cy="55" r="2.4" fill="rgba(244,240,231,0.85)" />
          <circle cx="58" cy="55" r="2.4" fill="rgba(244,240,231,0.85)" />
          <path d="M40 72 Q50 76 60 72" fill="none" stroke="rgba(244,240,231,0.85)" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
      </div>
      <div className="absolute inset-x-4 bottom-4 rounded-md border border-paper/15 bg-ink/75 p-3 text-paper backdrop-blur">
        <div className="text-[0.62rem] font-black uppercase tracking-[0.16em] text-paper/65">Frame guide</div>
        <div className="mt-1 text-sm font-black">
          Center your eyes on the horizontal line, roughly halfway up the photo.
        </div>
      </div>
    </div>
  );
}

function PreviewStep({
  previewUrl,
  filename,
  fileSize,
  uploading,
  onConfirm,
  onRetake,
  onChoose,
  error,
}: {
  previewUrl: string;
  filename: string;
  fileSize: number;
  uploading: boolean;
  onConfirm: () => void;
  onRetake: () => void;
  onChoose: () => void;
  error: string | null;
}) {
  return (
    <div className="space-y-3">
      <Panel className="overflow-hidden p-0">
        <div className="relative aspect-square w-full bg-ink">
          <img src={previewUrl} alt="Preview" className="h-full w-full object-cover" />
          <FrameOverlay />
        </div>
      </Panel>
      <div className="space-y-1 text-sm font-semibold text-ink/65">
        <div className="truncate font-mono text-xs text-ink/45">{filename}</div>
        <div className="text-xs">{(fileSize / 1024).toFixed(0)} KB</div>
      </div>
      <div className="grid grid-cols-1 gap-2">
        <Button onClick={onConfirm} disabled={uploading}>
          {uploading ? <Loader2 className="h-5 w-5 animate-spin" /> : <CheckCircle2 className="h-5 w-5" />}
          {uploading ? "Uploading..." : "Use this photo"}
        </Button>
        <div className="grid grid-cols-2 gap-2">
          <Button variant="secondary" disabled={uploading} onClick={onRetake}>
            <RefreshCw className="h-4 w-4" />
            Retake
          </Button>
          <Button variant="secondary" disabled={uploading} onClick={onChoose}>
            <ImagePlus className="h-4 w-4" />
            Library
          </Button>
        </div>
      </div>
      {error ? (
        <Panel className="border border-coral/35 bg-coral/10 text-sm font-semibold text-coral">
          <AlertTriangle className="mr-2 inline h-4 w-4" />
          {error}
        </Panel>
      ) : null}
    </div>
  );
}

function ResultStep({
  previewUrl,
  job,
  result,
  onRetake,
  onDone,
  error,
}: {
  previewUrl: string;
  job: JobStatus | null;
  result: { skipped?: boolean; skip_reason?: string | null; quality_score?: number | null; aligned?: boolean; duplicate_of?: string | null } | null;
  onRetake: () => void;
  onDone: () => void;
  error: string | null;
}) {
  const isRunning = !job || ["queued", "running"].includes(job.status);
  const isDone = job?.status === "done";
  const isProblem = job?.status === "failed" || job?.status === "cancelled";
  const skipped = isDone && result?.skipped;

  return (
    <div className="space-y-3">
      <Panel className="overflow-hidden p-0">
        <div className="relative aspect-square w-full bg-ink">
          <img src={previewUrl} alt="Preview" className={cn("h-full w-full object-cover", isRunning && "opacity-70")} />
          {isRunning ? (
            <div className="absolute inset-0 flex items-center justify-center bg-ink/45 text-paper">
              <div className="flex items-center gap-3 rounded-md bg-ink/80 px-3 py-2 text-sm font-black backdrop-blur">
                <Loader2 className="h-5 w-5 animate-spin" />
                {humanizeStage(job?.stage) || "Working"}
              </div>
            </div>
          ) : null}
        </div>
      </Panel>
      <Panel>
        {isRunning ? (
          <div>
            <div className="text-sm font-black text-ink">Adding to your timelapse</div>
            <p className="mt-1 text-xs font-semibold leading-5 text-ink/55">{job?.message ?? "Hashing the photo and finding your face"}</p>
            <div className="mt-3">
              <ProgressBar value={job?.progress ?? 0.1} />
            </div>
            <div className="mt-2 text-[0.7rem] font-bold uppercase tracking-[0.12em] text-ink/45">
              {humanizeStage(job?.stage) ?? "Step 1 of 4"}
            </div>
          </div>
        ) : null}
        {isDone && !skipped ? (
          <div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-teal" />
              <h3 className="text-lg font-black text-ink">Locked in for today</h3>
            </div>
            <p className="mt-1 text-sm font-semibold text-ink/65">
              The auto-render will fold this frame into the next video.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {result?.quality_score != null ? <Badge tone="good">Quality {result.quality_score.toFixed(2)}</Badge> : null}
              {result?.aligned ? <Badge>Aligned</Badge> : <Badge tone="warn">Will align overnight</Badge>}
              {result?.duplicate_of ? <Badge tone="warn">Duplicate of older photo</Badge> : null}
            </div>
            <div className="mt-4 grid grid-cols-1 gap-2">
              <Button onClick={onDone}>Done</Button>
              <Button variant="secondary" onClick={onRetake}>
                <RefreshCw className="h-4 w-4" />
                Retake instead
              </Button>
            </div>
          </div>
        ) : null}
        {isDone && skipped ? (
          <div>
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-coral" />
              <h3 className="text-lg font-black text-ink">Saved, but flagged</h3>
            </div>
            <p className="mt-1 text-sm font-semibold leading-5 text-ink/65">
              {humanSkipReason(result?.skip_reason)}. The auto-render will skip this one. Try a retake or open Review to keep it anyway.
            </p>
            <div className="mt-4 grid grid-cols-1 gap-2">
              <Button onClick={onRetake}>
                <RefreshCw className="h-4 w-4" />
                Retake
              </Button>
              <Button variant="secondary" onClick={onDone}>
                Keep as-is for now
              </Button>
            </div>
          </div>
        ) : null}
        {isProblem ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-coral" />
              <h3 className="text-lg font-black text-ink">Capture failed</h3>
            </div>
            <p className="text-sm font-semibold leading-5 text-ink/65">
              {error ?? job?.error ?? "Something went wrong while saving the photo."}
            </p>
            <div className="grid grid-cols-1 gap-2">
              <Button onClick={onRetake}>Try again</Button>
              <Button variant="secondary" onClick={onDone}>
                Back to today
              </Button>
            </div>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

function FrameOverlay() {
  return (
    <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
      <ellipse cx="50" cy="48" rx="22" ry="28" fill="none" stroke="rgba(255,255,255,0.55)" strokeWidth="0.6" strokeDasharray="2 2" />
      <line x1="20" y1="48" x2="80" y2="48" stroke="rgba(255,255,255,0.35)" strokeDasharray="2 4" strokeWidth="0.4" />
      <line x1="50" y1="18" x2="50" y2="78" stroke="rgba(255,255,255,0.35)" strokeDasharray="2 4" strokeWidth="0.4" />
    </svg>
  );
}

function stepLabel(step: CaptureStep) {
  switch (step) {
    case "pick":
      return "Step 1 · Capture";
    case "preview":
      return "Step 2 · Confirm";
    case "uploading":
      return "Step 3 · Uploading";
    case "result":
      return "Step 4 · Result";
  }
}

function humanizeStage(stage: string | null | undefined) {
  if (!stage) return null;
  const labels: Record<string, string> = {
    capture: "Adding photo to your inbox",
    detect: "Finding your face",
    canonical: "Updating face anchor",
    align: "Aligning to the anchor",
    render: "Rendering video",
  };
  return labels[stage] ?? stage;
}

function humanSkipReason(reason: string | null | undefined) {
  if (!reason) return "Quality check did not pass";
  const labels: Record<string, string> = {
    no_face_detected: "We could not see a face",
    landmarks_unavailable: "We saw a face but no detailed map",
    low_quality: "The frame did not pass the quality check",
    landmark_outlier: "Frame is far from the average face",
    user_skipped: "Manually marked as not included",
  };
  return labels[reason] ?? reason;
}
