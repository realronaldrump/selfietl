import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, SkipBack, SkipForward } from "lucide-react";
import { api, fetchJson, type Photo, type Project } from "@/api/client";
import { Badge, Button, Panel, ProgressBar, cn } from "@/components/ui";

type GridMode = "all" | "included";

export function Grid({ project, mode = "all" }: { project: Project; mode?: GridMode }) {
  const queryClient = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Photo | null>(null);
  const limit = 80;
  const skippedFilter = mode === "included" ? false : undefined;
  const photosQuery = useQuery({
    queryKey: ["photos", project.id, offset, mode],
    queryFn: () => api.photos(project.id, { offset, limit, skipped: skippedFilter }),
  });
  const patchMutation = useMutation({
    mutationFn: ({ hash, skipped }: { hash: string; skipped: boolean }) =>
      api.patchPhoto(hash, { skipped, user_override: !skipped }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["photos", project.id] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const data = photosQuery.data;
  const pages = data ? Math.ceil(data.total / limit) : 1;
  const currentPage = Math.floor(offset / limit) + 1;
  const pageTitle = mode === "included" ? "Included photos" : "All photos";
  const pageDescription =
    mode === "included"
      ? "Only photos currently used for the face anchor and final video. Mark anything questionable as not included."
      : "Every cataloged photo, sorted by the capture date the app found from EXIF or the AgeLapse filename.";

  useEffect(() => {
    setOffset(0);
  }, [project.id, mode]);

  return (
    <div className="space-y-4">
      <Panel className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-xl font-black text-ink">{pageTitle}</h2>
          <p className="mt-1 text-sm font-medium text-ink/55">{pageDescription}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
            <SkipBack className="h-4 w-4" />
            Prev
          </Button>
          <Badge>
            {currentPage} / {pages || 1}
          </Badge>
          <Button
            variant="secondary"
            size="sm"
            disabled={!data || offset + limit >= data.total}
            onClick={() => setOffset(offset + limit)}
          >
            Next
            <SkipForward className="h-4 w-4" />
          </Button>
        </div>
      </Panel>

      {photosQuery.isLoading ? <Panel>Loading photos...</Panel> : null}
      {data && data.items.length === 0 ? <Panel>{mode === "included" ? "No included photos yet." : "No photos cataloged yet."}</Panel> : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6 2xl:grid-cols-8">
        {data?.items.map((photo) => (
          <PhotoTile
            key={photo.hash}
            photo={photo}
            onOpen={() => setSelected(photo)}
            onToggle={() => patchMutation.mutate({ hash: photo.hash, skipped: !photo.skipped })}
          />
        ))}
      </div>
      {selected ? <PhotoModal photo={selected} onClose={() => setSelected(null)} /> : null}
    </div>
  );
}

function PhotoTile({ photo, onOpen, onToggle }: { photo: Photo; onOpen: () => void; onToggle: () => void }) {
  const quality = photo.quality_score ?? 0;
  const actionLabel = photo.skipped ? "Include" : "Not include";
  return (
    <div className={cn("overflow-hidden rounded-lg bg-paper shadow-line", photo.skipped && "opacity-55")}>
      <button className="group relative block aspect-square w-full bg-ink/8" onClick={onOpen}>
        <img src={photo.thumb_url} alt="" className="h-full w-full object-cover" loading="lazy" />
        <div className="absolute inset-0 hidden items-center justify-center bg-ink/45 group-hover:flex">
          <Eye className="h-8 w-8 text-paper" />
        </div>
      </button>
      <div className="space-y-2 p-2">
        <div className="flex items-center justify-between gap-2">
          <Badge tone={photo.skipped ? "bad" : quality >= 0.75 ? "good" : quality >= 0.6 ? "warn" : "bad"}>
            {photo.quality_score == null ? "new" : quality.toFixed(2)}
          </Badge>
          <button className="min-h-10 rounded px-2 text-xs font-black text-teal hover:bg-teal/10" onClick={onToggle}>
            {actionLabel}
          </button>
        </div>
        <ProgressBar value={quality} />
        <div className="truncate text-xs font-semibold text-ink/55">{formatDate(photo.captured_at)}</div>
      </div>
    </div>
  );
}

function PhotoModal({ photo, onClose }: { photo: Photo; onClose: () => void }) {
  const landmarksQuery = useQuery({
    queryKey: ["landmarks", photo.hash],
    queryFn: () => fetchJson<{ points: number[][] }>(`/api/photos/${photo.hash}/landmarks`),
    retry: false,
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/75 p-4" onClick={onClose}>
      <div className="max-h-[92vh] w-full max-w-5xl overflow-hidden rounded-lg bg-paper shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-ink/10 p-3">
          <div>
            <div className="font-black text-ink">{formatDate(photo.captured_at)}</div>
            <div className="mt-1 break-all font-mono text-xs text-ink/50">{photo.path}</div>
          </div>
          <Button variant="secondary" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
        <div className="grid max-h-[78vh] gap-4 overflow-auto p-4 lg:grid-cols-[1fr_18rem]">
          <div className="relative mx-auto max-h-[72vh] overflow-hidden rounded-md bg-ink">
            <img src={photo.image_url} alt="" className="max-h-[72vh] w-full object-contain" />
            {landmarksQuery.data ? (
              <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
                {landmarksQuery.data.points.map((point, index) => (
                  <circle key={index} cx={point[0] * 100} cy={point[1] * 100} r="0.22" className="fill-coral/80" />
                ))}
              </svg>
            ) : null}
          </div>
          <div className="space-y-3">
            <Stat label="Quality" value={photo.quality_score?.toFixed(3) ?? "-"} />
            <Stat label="Yaw" value={formatNumber(photo.yaw)} />
            <Stat label="Pitch" value={formatNumber(photo.pitch)} />
            <Stat label="Roll" value={formatNumber(photo.roll)} />
            <Stat label="Eye open" value={formatNumber(photo.eye_open_ratio)} />
            <Stat label="Mouth open" value={formatNumber(photo.mouth_open_ratio)} />
            {photo.skipped ? <Badge tone="bad">{photo.skip_reason ?? "skipped"}</Badge> : <Badge tone="good">included</Badge>}
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-ink/10 bg-white p-3">
      <div className="text-xs font-bold uppercase tracking-[0.08em] text-ink/50">{label}</div>
      <div className="mt-1 font-mono text-lg font-black text-ink">{value}</div>
    </div>
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function formatNumber(value: number | null) {
  return value == null ? "-" : value.toFixed(2);
}
