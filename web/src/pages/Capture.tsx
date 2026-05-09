import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Calendar,
  Camera,
  CheckCircle2,
  ImagePlus,
  Loader2,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { api, type CapturePreviewItem, type JobStatus } from "@/api/client";
import { Badge, Button, Input, Label, PageFrame, Panel, ProgressBar, cn } from "@/components/ui";
import { useJobEvents } from "@/hooks/useJobEvents";

type CaptureStep = "pick" | "preview" | "uploading" | "result";

type UploadItem = {
  id: string;
  index: number;
  file: File;
  previewUrl: string;
  filename: string;
  fileSize: number;
  capturedAtLocal: string;
  originalCapturedAtLocal: string | null;
  capturedAtSource: string | null;
  cameraMake: string | null;
  cameraModel: string | null;
  width: number | null;
  height: number | null;
  warnings: string[];
  metadataLoading: boolean;
  supported: boolean;
  error: string | null;
  adjusted: boolean;
};

type BatchPhotoResult = {
  index?: number;
  filename?: string;
  hash?: string;
  captured_at?: string;
  skipped?: boolean;
  skip_reason?: string | null;
  quality_score?: number | null;
  aligned?: boolean;
  duplicate_of?: string | null;
  duplicate_reason?: string | null;
  replaced_count?: number | null;
  warnings?: string[];
  error?: string;
};

type BatchResult = {
  photos?: BatchPhotoResult[];
  total?: number;
  succeeded?: number;
  failed?: number;
  duplicates?: number;
  skipped?: boolean;
  skip_reason?: string | null;
  quality_score?: number | null;
  aligned?: boolean;
  duplicate_of?: string | null;
  duplicate_reason?: string | null;
  replaced_count?: number | null;
};

export function Capture({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<CaptureStep>("pick");
  const [items, setItems] = useState<UploadItem[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const libraryInputRef = useRef<HTMLInputElement>(null);
  const itemsRef = useRef<UploadItem[]>([]);
  const previewRequestRef = useRef(0);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(() => {
    return () => {
      revokeUploadItems(itemsRef.current);
    };
  }, []);

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
  const result = job?.result as BatchResult | null;

  const readyToUpload = useMemo(
    () => items.length > 0 && items.every((item) => !item.metadataLoading && item.supported && item.capturedAtLocal),
    [items],
  );

  const captureMutation = useMutation({
    mutationFn: async (currentItems: UploadItem[]) =>
      api.captureBatch(
        currentItems.map((item) => ({
          file: item.file,
          capturedAt: item.capturedAtLocal ? datetimeLocalToIso(item.capturedAtLocal) : null,
        })),
      ),
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

  function handleSelectedFiles(fileList: FileList | null) {
    const files = Array.from(fileList ?? []);
    if (!files.length) return;
    const nextItems = files.map((file, index) => createUploadItem(file, index));
    previewRequestRef.current += 1;
    const requestId = previewRequestRef.current;
    setItems((current) => {
      revokeUploadItems(current);
      return nextItems;
    });
    setJobId(null);
    setError(null);
    setStep("preview");
    void loadPreviewMetadata(nextItems, requestId);
  }

  async function loadPreviewMetadata(uploadItems: UploadItem[], requestId: number) {
    try {
      const response = await api.previewCapture(uploadItems.map((item) => item.file));
      if (previewRequestRef.current !== requestId) return;
      const byIndex = new Map(response.items.map((item) => [item.index, item]));
      setItems((current) =>
        current.map((item) => applyPreviewItem(item, byIndex.get(item.index))),
      );
    } catch (err) {
      if (previewRequestRef.current !== requestId) return;
      const message = err instanceof Error ? err.message : "Could not read photo metadata";
      setError(message);
      setItems((current) =>
        current.map((item) => ({
          ...item,
          capturedAtLocal: item.capturedAtLocal || datetimeToLocalInput(new Date()),
          metadataLoading: false,
          error: item.error ?? message,
        })),
      );
    }
  }

  function updateCapturedAt(id: string, value: string) {
    setItems((current) =>
      current.map((item) =>
        item.id === id
          ? {
              ...item,
              capturedAtLocal: value,
              adjusted: value !== item.originalCapturedAtLocal,
            }
          : item,
      ),
    );
  }

  function removeItem(id: string) {
    setItems((current) => {
      const next = current.filter((item) => item.id !== id);
      const removed = current.find((item) => item.id === id);
      if (removed) URL.revokeObjectURL(removed.previewUrl);
      return next;
    });
    if (items.length === 1) {
      setStep("pick");
      setError(null);
    }
  }

  function reset() {
    previewRequestRef.current += 1;
    setItems((current) => {
      revokeUploadItems(current);
      return [];
    });
    setJobId(null);
    setError(null);
    setStep("pick");
  }

  return (
    <PageFrame size="phone">
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
          handleSelectedFiles(event.target.files);
          event.target.value = "";
        }}
      />
      <input
        ref={libraryInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(event) => {
          handleSelectedFiles(event.target.files);
          event.target.value = "";
        }}
      />

      {step === "pick" ? <PickStep onCamera={pickFromCamera} onLibrary={pickFromLibrary} /> : null}

      {step === "preview" ? (
        <PreviewStep
          items={items}
          uploading={captureMutation.isPending}
          canUpload={readyToUpload}
          onConfirm={() => captureMutation.mutate(items)}
          onRetake={pickFromCamera}
          onChoose={pickFromLibrary}
          onRemove={removeItem}
          onDateChange={updateCapturedAt}
          error={error}
        />
      ) : null}

      {(step === "uploading" || step === "result") && items.length ? (
        <ResultStep
          items={items}
          job={job}
          result={normalizeResult(result, items)}
          onRetake={() => {
            reset();
            pickFromCamera();
          }}
          onChoose={() => {
            reset();
            pickFromLibrary();
          }}
          onDone={onDone}
          error={error}
        />
      ) : null}
    </PageFrame>
  );
}

function PickStep({ onCamera, onLibrary }: { onCamera: () => void; onLibrary: () => void }) {
  return (
    <div className="space-y-3">
      <Panel>
        <h2 className="text-xl font-black text-ink">Capture or import selfies</h2>
        <p className="mt-1 text-sm font-semibold leading-6 text-ink/65">
          Use the camera for today's frame, or choose one or many older photos from the library.
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
          Choose photos
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
  items,
  uploading,
  canUpload,
  onConfirm,
  onRetake,
  onChoose,
  onRemove,
  onDateChange,
  error,
}: {
  items: UploadItem[];
  uploading: boolean;
  canUpload: boolean;
  onConfirm: () => void;
  onRetake: () => void;
  onChoose: () => void;
  onRemove: (id: string) => void;
  onDateChange: (id: string, value: string) => void;
  error: string | null;
}) {
  const adjusted = items.filter((item) => item.adjusted).length;
  const loading = items.some((item) => item.metadataLoading);
  const blocked = items.some((item) => !item.supported);

  return (
    <div className="space-y-3">
      <Panel>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-black text-ink">Review assigned dates</h2>
            <p className="mt-1 text-sm font-semibold leading-5 text-ink/60">
              Each photo will land on the calendar date shown below.
            </p>
          </div>
          <Badge tone={blocked ? "bad" : loading ? "warn" : "good"}>{items.length} selected</Badge>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {loading ? <Badge tone="warn">Reading metadata</Badge> : <Badge tone="good">Metadata ready</Badge>}
          {adjusted ? <Badge>{adjusted} adjusted</Badge> : null}
          {blocked ? <Badge tone="bad">Remove unsupported files</Badge> : null}
        </div>
      </Panel>

      <div className="space-y-3">
        {items.map((item) => (
          <UploadReviewCard
            key={item.id}
            item={item}
            disabled={uploading}
            onRemove={() => onRemove(item.id)}
            onDateChange={(value) => onDateChange(item.id, value)}
          />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-2">
        <Button onClick={onConfirm} disabled={uploading || !canUpload}>
          {uploading ? <Loader2 className="h-5 w-5 animate-spin" /> : <CheckCircle2 className="h-5 w-5" />}
          {uploading ? "Uploading..." : items.length === 1 ? "Use this photo" : `Upload ${items.length} photos`}
        </Button>
        <div className="grid grid-cols-2 gap-2">
          <Button variant="secondary" disabled={uploading} onClick={onRetake}>
            <RefreshCw className="h-4 w-4" />
            Camera
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

function UploadReviewCard({
  item,
  disabled,
  onRemove,
  onDateChange,
}: {
  item: UploadItem;
  disabled: boolean;
  onRemove: () => void;
  onDateChange: (value: string) => void;
}) {
  return (
    <Panel className={cn("p-3", !item.supported && "border border-coral/35 bg-coral/10")}>
      <div className="grid grid-cols-[5.5rem_1fr] gap-3">
        <div className="relative aspect-square overflow-hidden rounded-md bg-ink">
          <img src={item.previewUrl} alt="" className="h-full w-full object-cover" />
          {item.metadataLoading ? (
            <div className="absolute inset-0 grid place-items-center bg-ink/45 text-paper">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : null}
        </div>
        <div className="min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate font-mono text-xs font-bold text-ink/55">{item.filename}</div>
              <div className="mt-1 flex flex-wrap gap-1.5">
                <Badge tone={item.supported ? "default" : "bad"}>{formatFileSize(item.fileSize)}</Badge>
                {item.width && item.height ? <Badge>{item.width}x{item.height}</Badge> : null}
                {item.capturedAtSource ? <Badge tone={sourceTone(item.capturedAtSource)}>{sourceLabel(item.capturedAtSource)}</Badge> : null}
                {item.adjusted ? <Badge tone="warn">Adjusted</Badge> : null}
              </div>
            </div>
            <Button size="icon" variant="ghost" onClick={onRemove} disabled={disabled} aria-label={`Remove ${item.filename}`}>
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>

          <div className="mt-3 min-w-0 space-y-1">
            <Label>Assigned date and time</Label>
            <div className="relative min-w-0">
              <Calendar className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink/35" />
              <Input
                type="datetime-local"
                step={1}
                value={item.capturedAtLocal}
                onChange={(event) => onDateChange(event.target.value)}
                disabled={disabled || item.metadataLoading}
                className="min-w-0 max-w-full pl-9 pr-2 text-[0.8125rem] sm:text-sm"
              />
            </div>
          </div>

          {item.cameraMake || item.cameraModel ? (
            <div className="mt-2 truncate text-xs font-semibold text-ink/50">
              {[item.cameraMake, item.cameraModel].filter(Boolean).join(" ")}
            </div>
          ) : null}
          {item.error ? (
            <div className="mt-2 text-xs font-bold leading-5 text-coral">{item.error}</div>
          ) : item.warnings.length ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {item.warnings.slice(0, 3).map((warning) => (
                <Badge key={warning} tone="warn">
                  {humanWarning(warning)}
                </Badge>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}

function ResultStep({
  items,
  job,
  result,
  onRetake,
  onChoose,
  onDone,
  error,
}: {
  items: UploadItem[];
  job: JobStatus | null;
  result: Required<Pick<BatchResult, "photos">> & { total: number; succeeded: number; failed: number; duplicates: number };
  onRetake: () => void;
  onChoose: () => void;
  onDone: () => void;
  error: string | null;
}) {
  const isRunning = !job || ["queued", "running"].includes(job.status);
  const isDone = job?.status === "done";
  const isProblem = job?.status === "failed" || job?.status === "cancelled";
  const duplicates = result.duplicates ?? result.photos.filter((photo) => photo.duplicate_of && !photo.error).length;
  const saved = Math.max(0, result.succeeded - duplicates);
  const flagged = result.photos.filter((photo) => photo.skipped && !photo.error && !photo.duplicate_of).length;
  const failed = result.failed;

  return (
    <div className="space-y-3">
      <Panel className="overflow-hidden p-0">
        <div className={cn("relative grid aspect-square w-full bg-ink", items.length === 1 ? "grid-cols-1" : "grid-cols-2")}>
          {items.slice(0, 4).map((item) => (
            <img key={item.id} src={item.previewUrl} alt="" className={cn("h-full w-full object-cover", isRunning && "opacity-70")} />
          ))}
          {items.length === 1 ? <FrameOverlay /> : null}
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
            <div className="text-sm font-black text-ink">{items.length === 1 ? "Adding to your timelapse" : "Adding photos to your timelapse"}</div>
            <p className="mt-1 text-xs font-semibold leading-5 text-ink/55">{job?.message ?? "Hashing photos and finding faces"}</p>
            <div className="mt-3">
              <ProgressBar value={job?.progress ?? 0.1} />
            </div>
            <div className="mt-2 text-[0.7rem] font-bold uppercase tracking-[0.12em] text-ink/45">
              {humanizeStage(job?.stage) ?? "Preparing"}
            </div>
          </div>
        ) : null}
        {isDone ? (
          <div>
            <div className="flex items-center gap-2">
              {failed ? <AlertTriangle className="h-5 w-5 text-coral" /> : <CheckCircle2 className="h-5 w-5 text-teal" />}
              <h3 className="text-lg font-black text-ink">{failed ? "Upload finished with issues" : "Photos added"}</h3>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge tone="good">{saved} saved</Badge>
              {duplicates ? <Badge tone="warn">{duplicates} duplicate{duplicates === 1 ? "" : "s"}</Badge> : null}
              {flagged ? <Badge tone="warn">{flagged} flagged</Badge> : null}
              {failed ? <Badge tone="bad">{failed} failed</Badge> : null}
            </div>
            <div className="mt-4 space-y-2">
              {result.photos.map((photo, index) => (
                <ResultRow key={`${photo.hash ?? photo.filename ?? "photo"}-${index}`} photo={photo} />
              ))}
            </div>
            <div className="mt-4 grid grid-cols-1 gap-2">
              <Button onClick={onDone}>Done</Button>
              <div className="grid grid-cols-2 gap-2">
                <Button variant="secondary" onClick={onChoose}>
                  <ImagePlus className="h-4 w-4" />
                  Library
                </Button>
                <Button variant="secondary" onClick={onRetake}>
                  <RefreshCw className="h-4 w-4" />
                  Camera
                </Button>
              </div>
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
              <Button onClick={onChoose}>Try again</Button>
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

function ResultRow({ photo }: { photo: BatchPhotoResult }) {
  const hasError = Boolean(photo.error);
  const isDuplicate = Boolean(photo.duplicate_of);
  return (
    <div className="rounded-md border border-ink/10 bg-white p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-mono text-xs font-bold text-ink/55">{photo.filename ?? photo.hash ?? "photo"}</div>
          <div className="mt-1 text-sm font-black text-ink">{photo.captured_at ? formatAssignedDate(photo.captured_at) : hasError || isDuplicate ? "Not saved" : "Saved"}</div>
        </div>
        {hasError ? <Badge tone="bad">Failed</Badge> : isDuplicate ? <Badge tone="warn">Duplicate</Badge> : photo.skipped ? <Badge tone="warn">{humanSkipReason(photo.skip_reason)}</Badge> : <Badge tone="good">Included</Badge>}
      </div>
      {!hasError ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {photo.quality_score != null ? <Badge tone="good">Quality {photo.quality_score.toFixed(2)}</Badge> : null}
          {isDuplicate ? <Badge tone="warn">Already in timeline</Badge> : photo.aligned ? <Badge>Aligned</Badge> : <Badge tone="warn">Will align overnight</Badge>}
          {photo.replaced_count ? <Badge tone="good">Replaced earlier take</Badge> : null}
        </div>
      ) : (
        <div className="mt-2 text-xs font-bold leading-5 text-coral">{photo.error}</div>
      )}
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

function createUploadItem(file: File, index: number): UploadItem {
  return {
    id: `${Date.now()}-${index}-${file.name}-${file.size}`,
    index,
    file,
    previewUrl: URL.createObjectURL(file),
    filename: file.name || `photo-${index + 1}.jpg`,
    fileSize: file.size,
    capturedAtLocal: "",
    originalCapturedAtLocal: null,
    capturedAtSource: null,
    cameraMake: null,
    cameraModel: null,
    width: null,
    height: null,
    warnings: [],
    metadataLoading: true,
    supported: true,
    error: null,
    adjusted: false,
  };
}

function applyPreviewItem(item: UploadItem, preview: CapturePreviewItem | undefined): UploadItem {
  if (!preview) {
    return {
      ...item,
      capturedAtLocal: item.capturedAtLocal || datetimeToLocalInput(new Date()),
      originalCapturedAtLocal: item.originalCapturedAtLocal,
      metadataLoading: false,
      error: item.error ?? "Metadata preview did not return this photo",
    };
  }
  const capturedAtLocal = preview.captured_at ? isoLikeToLocalInput(preview.captured_at) : item.capturedAtLocal || datetimeToLocalInput(new Date());
  return {
    ...item,
    filename: preview.filename || item.filename,
    fileSize: preview.file_size,
    capturedAtLocal,
    originalCapturedAtLocal: capturedAtLocal,
    capturedAtSource: preview.captured_at_source,
    cameraMake: preview.camera_make,
    cameraModel: preview.camera_model,
    width: preview.width,
    height: preview.height,
    warnings: preview.warnings ?? [],
    metadataLoading: false,
    supported: preview.supported,
    error: preview.error,
    adjusted: false,
  };
}

function normalizeResult(result: BatchResult | null, items: UploadItem[]): Required<Pick<BatchResult, "photos">> & { total: number; succeeded: number; failed: number; duplicates: number } {
  if (result?.photos) {
    return {
      photos: result.photos,
      total: result.total ?? result.photos.length,
      succeeded: result.succeeded ?? result.photos.filter((photo) => !photo.error).length,
      failed: result.failed ?? result.photos.filter((photo) => photo.error).length,
      duplicates: result.duplicates ?? result.photos.filter((photo) => photo.duplicate_of && !photo.error).length,
    };
  }
  if (!result) {
    return { photos: [], total: items.length, succeeded: 0, failed: 0, duplicates: 0 };
  }
  return {
    photos: [
      {
        filename: items[0]?.filename,
        skipped: result.skipped,
        skip_reason: result.skip_reason,
        quality_score: result.quality_score,
        aligned: result.aligned,
        duplicate_of: result.duplicate_of,
        duplicate_reason: result.duplicate_reason,
        replaced_count: result.replaced_count,
      },
    ],
    total: 1,
    succeeded: 1,
    failed: 0,
    duplicates: result.duplicate_of ? 1 : 0,
  };
}

function revokeUploadItems(items: UploadItem[]) {
  items.forEach((item) => URL.revokeObjectURL(item.previewUrl));
}

function stepLabel(step: CaptureStep) {
  switch (step) {
    case "pick":
      return "Step 1 · Capture";
    case "preview":
      return "Step 2 · Review";
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
  if (!reason) return "Flagged";
  const labels: Record<string, string> = {
    no_face_detected: "No face",
    landmarks_unavailable: "No face map",
    low_quality: "Low quality",
    landmark_outlier: "Outlier",
    user_skipped: "Not included",
    replaced_by_newer_capture: "Replaced",
    duplicate_upload: "Duplicate",
  };
  return labels[reason] ?? reason;
}

function humanWarning(warning: string) {
  const labels: Record<string, string> = {
    missing_datetime_original: "No original date",
    datetime_from_filename: "Date from filename",
    datetime_from_file_modified_time: "Date fallback",
    datetime_from_exif_datetime: "Date from EXIF",
    filename_datetime_differs_from_exif: "Filename differs",
    exif_datetime_ignored_for_filename: "EXIF date ignored",
    captured_at_user_override: "Date adjusted",
    duplicate_exact_file: "Duplicate file",
    duplicate_same_photo: "Duplicate photo",
    duplicate_same_metadata: "Duplicate metadata",
    preview_failed: "Preview failed",
  };
  return labels[warning] ?? warning.split("_").join(" ");
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    exif_datetime_original: "EXIF original",
    exif_datetime: "EXIF date",
    filename: "Filename",
    file_modified_time: "File time",
    user_override: "Adjusted",
  };
  return labels[source] ?? source.split("_").join(" ");
}

function sourceTone(source: string): "default" | "good" | "warn" | "bad" {
  if (source === "exif_datetime_original") return "good";
  if (source === "filename" || source === "exif_datetime") return "default";
  if (source === "file_modified_time") return "warn";
  return "default";
}

function formatFileSize(bytes: number) {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function isoLikeToLocalInput(value: string) {
  const normalized = value.replace(" ", "T");
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return normalized.slice(0, 16);
  return datetimeToLocalInput(date);
}

function datetimeToLocalInput(date: Date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  const h = String(date.getHours()).padStart(2, "0");
  const min = String(date.getMinutes()).padStart(2, "0");
  const sec = String(date.getSeconds()).padStart(2, "0");
  return `${y}-${m}-${d}T${h}:${min}:${sec}`;
}

function datetimeLocalToIso(value: string) {
  return value.length === 16 ? `${value}:00` : value;
}

function formatAssignedDate(value: string) {
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
