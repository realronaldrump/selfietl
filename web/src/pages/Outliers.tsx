import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, RotateCcw } from "lucide-react";
import { api, type Photo, type Project } from "@/api/client";
import { Badge, Button, Panel } from "@/components/ui";

export function Outliers({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const photosQuery = useQuery({
    queryKey: ["photos", project.id, "skipped"],
    queryFn: () => api.photos(project.id, { skipped: true, limit: 300 }),
  });
  const includeMutation = useMutation({
    mutationFn: async (photos: Photo[]) => {
      for (const photo of photos) {
        await api.patchPhoto(photo.hash, { skipped: false, user_override: true });
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["photos", project.id] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const items = photosQuery.data?.items ?? [];

  return (
    <div className="space-y-4">
      <Panel className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-xl font-black text-ink">Review skipped photos</h2>
          <p className="mt-1 text-sm font-medium text-ink/55">These photos looked risky for face matching. Include one only if you want it in the final video.</p>
        </div>
        <Button disabled={items.length === 0 || includeMutation.isPending} onClick={() => includeMutation.mutate(items)}>
          <Check className="h-4 w-4" />
          Include visible
        </Button>
      </Panel>

      {items.length === 0 ? (
        <Panel className="text-sm font-semibold text-ink/60">No skipped photos in this project.</Panel>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {items.map((photo) => (
            <Panel key={photo.hash} className="grid grid-cols-[6rem_1fr] gap-3">
              <img src={photo.thumb_url} alt="" className="aspect-square rounded-md object-cover" />
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="bad">{photo.skip_reason ?? "skipped"}</Badge>
                  <Badge>{photo.quality_score?.toFixed(2) ?? "new"}</Badge>
                </div>
                <div className="mt-2 truncate text-sm font-black text-ink">{new Date(photo.captured_at).toLocaleDateString()}</div>
                <div className="mt-1 truncate font-mono text-xs text-ink/45">{photo.path}</div>
                <Button className="mt-3" size="sm" variant="secondary" onClick={() => includeMutation.mutate([photo])}>
                  <RotateCcw className="h-4 w-4" />
                  Include
                </Button>
              </div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}
