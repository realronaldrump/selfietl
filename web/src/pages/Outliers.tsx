import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Eye, Gauge, RotateCcw, ShieldAlert, XCircle } from "lucide-react";
import { api, type Photo, type Project } from "@/api/client";
import { Badge, Button, PageFrame, Panel, cn } from "@/components/ui";

const QUALITY_THRESHOLD = 0.6;
const MAX_YAW = 25;
const MAX_PITCH = 20;
const MAX_ROLL = 20;
const MIN_EYE_OPEN = 0.18;

type ReviewIssue = {
  kind: "no_face" | "face_map" | "score" | "pose" | "eyes" | "drift" | "manual";
  title: string;
  detail: string;
  value?: string;
};

type ReviewExplanation = {
  label: string;
  summary: string;
  primaryKind: ReviewIssue["kind"];
  issues: ReviewIssue[];
};

export function Outliers({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const photosQuery = useQuery({
    queryKey: ["photos", project.id, "skipped"],
    queryFn: () => api.photos(project.id, { skipped: true, limit: 300 }),
  });
  const markMutation = useMutation({
    mutationFn: async ({ photos, skipped }: { photos: Photo[]; skipped: boolean }) => {
      for (const photo of photos) {
        await api.patchPhoto(photo.hash, {
          skipped,
          user_override: true,
          skip_reason: skipped ? photo.skip_reason ?? "user_skipped" : null,
        });
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["photos", project.id] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const items = photosQuery.data?.items ?? [];

  return (
    <PageFrame size="wide">
      <Panel className="overflow-hidden p-0">
        <div className="grid gap-0 lg:grid-cols-[1fr_18rem]">
          <div className="p-5">
            <div className="flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-coral" />
              <h2 className="text-2xl font-black text-ink">Needs review</h2>
            </div>
            <p className="mt-2 max-w-3xl text-sm font-semibold leading-6 text-ink/60">
              These photos were held out because they could make the face jump, blink, tilt, or drift in the video. Each card shows the exact check that failed and a diagram over the photo.
            </p>
          </div>
          <div className="border-t border-ink/10 bg-white p-5 lg:border-l lg:border-t-0">
            <div className="text-xs font-bold uppercase tracking-[0.08em] text-ink/45">Visible decision</div>
            <Button className="mt-3 w-full" disabled={items.length === 0 || markMutation.isPending} onClick={() => markMutation.mutate({ photos: items, skipped: false })}>
              <Check className="h-4 w-4" />
              Include visible
            </Button>
            <p className="mt-3 text-xs font-semibold leading-5 text-ink/45">Use this only after the diagrams look acceptable. Original files are never changed.</p>
          </div>
        </div>
      </Panel>

      {photosQuery.isLoading ? <Panel>Loading review photos...</Panel> : null}

      {!photosQuery.isLoading && items.length === 0 ? (
        <Panel className="text-sm font-semibold text-ink/60">Nothing needs review. Every cataloged photo is currently included.</Panel>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {items.map((photo) => (
            <ReviewCard
              key={photo.hash}
              photo={photo}
              pending={markMutation.isPending}
              onInclude={() => markMutation.mutate({ photos: [photo], skipped: false })}
              onExclude={() => markMutation.mutate({ photos: [photo], skipped: true })}
            />
          ))}
        </div>
      )}
    </PageFrame>
  );
}

function ReviewCard({
  photo,
  pending,
  onInclude,
  onExclude,
}: {
  photo: Photo;
  pending: boolean;
  onInclude: () => void;
  onExclude: () => void;
}) {
  const explanation = explainReview(photo);
  return (
    <Panel className="grid gap-4 p-3 md:grid-cols-[17rem_1fr]">
      <ReviewDiagram photo={photo} explanation={explanation} />
      <div className="flex min-w-0 flex-col gap-3 p-1">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="bad">{explanation.label}</Badge>
            <Badge>{photo.quality_score == null ? "No score" : `Score ${photo.quality_score.toFixed(2)}`}</Badge>
            {photo.user_override ? <Badge tone="warn">Manual decision</Badge> : null}
          </div>
          <h3 className="mt-3 text-lg font-black text-ink">{formatDate(photo.captured_at)}</h3>
          <p className="mt-1 text-sm font-semibold leading-6 text-ink/60">{explanation.summary}</p>
        </div>

        <div className="grid gap-2">
          {explanation.issues.map((issue) => (
            <ReasonRow key={`${issue.kind}-${issue.title}`} issue={issue} />
          ))}
        </div>

        <div className="mt-auto grid gap-2 sm:grid-cols-2">
          <Button type="button" disabled={pending} onClick={onInclude}>
            <RotateCcw className="h-4 w-4" />
            Include
          </Button>
          <Button type="button" variant="secondary" disabled={pending} onClick={onExclude}>
            <XCircle className="h-4 w-4" />
            Exclude
          </Button>
        </div>
        <div className="truncate font-mono text-[0.68rem] font-semibold text-ink/35">{photo.path}</div>
      </div>
    </Panel>
  );
}

function ReviewDiagram({ photo, explanation }: { photo: Photo; explanation: ReviewExplanation }) {
  const poseIssue = explanation.issues.find((issue) => issue.kind === "pose");
  const roll = clamp(photo.roll ?? 0, -18, 18);
  const hasFaceGuide = !["no_face", "face_map"].includes(explanation.primaryKind);
  const isEyes = explanation.issues.some((issue) => issue.kind === "eyes");
  const isDrift = explanation.issues.some((issue) => issue.kind === "drift");
  const isPose = Boolean(poseIssue);
  const markerId = `arrow-${photo.hash.slice(0, 8)}`;

  return (
    <div className="relative aspect-[4/5] overflow-hidden rounded-md bg-ink shadow-line">
      <img src={photo.thumb_url} alt="" className="h-full w-full object-cover" loading="lazy" />
      <div className="absolute inset-0 bg-gradient-to-t from-ink/70 via-ink/5 to-transparent" />
      <svg className="pointer-events-none absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
        <defs>
          <marker id={markerId} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto" markerUnits="strokeWidth">
            <path d="M0,0 L6,3 L0,6 Z" className="fill-coral" />
          </marker>
        </defs>

        {!hasFaceGuide ? (
          <g>
            <ellipse cx="50" cy="45" rx="25" ry="31" className="fill-white/5 stroke-coral" strokeWidth="1.6" strokeDasharray="3 2" />
            <path d="M28 42 H72 M50 19 V76" className="stroke-coral/80" strokeWidth="0.8" strokeDasharray="2 3" />
            <text x="50" y="51" textAnchor="middle" className="fill-paper text-[18px] font-black">
              ?
            </text>
          </g>
        ) : (
          <g transform={`rotate(${roll} 50 46)`}>
            <ellipse
              cx="50"
              cy="46"
              rx="24"
              ry="31"
              className={cn("fill-teal/10 stroke-paper", isDrift ? "stroke-coral" : "stroke-paper")}
              strokeWidth="1.5"
              strokeDasharray={isDrift ? "3 2" : undefined}
            />
            <line x1="31" y1="40" x2="69" y2="40" className={cn("stroke-paper", isEyes && "stroke-coral")} strokeWidth={isEyes ? "2.4" : "1.2"} />
            <line x1="50" y1="22" x2="50" y2="72" className="stroke-paper/75" strokeWidth="0.8" strokeDasharray="2 3" />
            <circle cx="39" cy="40" r={isEyes ? "4.4" : "3"} className={cn("fill-teal/75 stroke-paper", isEyes && "fill-coral/85")} strokeWidth="0.6" />
            <circle cx="61" cy="40" r={isEyes ? "4.4" : "3"} className={cn("fill-teal/75 stroke-paper", isEyes && "fill-coral/85")} strokeWidth="0.6" />
            <circle cx="50" cy="51" r="2.2" className="fill-paper" />
            <path d="M39 61 Q50 67 61 61" className="fill-none stroke-paper" strokeWidth="1.2" />
            {isDrift ? (
              <g className="stroke-coral/80" strokeWidth="0.45">
                <path d="M30 40 L50 22 L70 40 L61 61 L39 61 Z" fill="none" />
                <path d="M30 40 L50 51 L70 40 M39 61 L50 51 L61 61 M50 22 L50 72" fill="none" />
              </g>
            ) : null}
          </g>
        )}

        {isPose ? <path d="M50 82 C67 72 74 58 70 42" className="fill-none stroke-coral" strokeWidth="2.2" markerEnd={`url(#${markerId})`} /> : null}
        {isEyes ? (
          <g>
            <rect x="31" y="36" width="16" height="8" rx="4" className="fill-coral/25 stroke-coral" strokeWidth="0.8" />
            <rect x="53" y="36" width="16" height="8" rx="4" className="fill-coral/25 stroke-coral" strokeWidth="0.8" />
          </g>
        ) : null}
      </svg>
      <div className="absolute inset-x-3 bottom-3 rounded-md border border-paper/20 bg-ink/72 p-3 text-paper backdrop-blur">
        <div className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.08em] text-paper/65">
          {explanation.primaryKind === "score" ? <Gauge className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          Diagram
        </div>
        <div className="mt-1 text-sm font-black">{diagramCaption(explanation)}</div>
      </div>
    </div>
  );
}

function ReasonRow({ issue }: { issue: ReviewIssue }) {
  return (
    <div className="grid gap-2 rounded-md border border-ink/10 bg-white p-3 sm:grid-cols-[9rem_1fr]">
      <div>
        <div className="text-[0.68rem] font-bold uppercase tracking-[0.08em] text-ink/45">{issue.title}</div>
        {issue.value ? <div className="mt-1 font-mono text-sm font-black text-coral">{issue.value}</div> : null}
      </div>
      <div className="text-sm font-semibold leading-5 text-ink/60">{issue.detail}</div>
    </div>
  );
}

function explainReview(photo: Photo): ReviewExplanation {
  const issues: ReviewIssue[] = [];
  const reason = photo.skip_reason ?? "review";

  if (reason === "no_face_detected") {
    issues.push({
      kind: "no_face",
      title: "Face found",
      value: "No",
      detail: "The detector could not find one reliable face. Including it could create a hard jump in the movie.",
    });
  }

  if (reason === "landmarks_unavailable") {
    issues.push({
      kind: "face_map",
      title: "Face map",
      value: "Missing",
      detail: "A face may be visible, but the detailed landmark map was not saved, so the app cannot lock the eyes and mouth.",
    });
  }

  if (photo.quality_score != null && photo.quality_score < QUALITY_THRESHOLD) {
    issues.push({
      kind: "score",
      title: "Overall check",
      value: `${photo.quality_score.toFixed(2)} < ${QUALITY_THRESHOLD.toFixed(2)}`,
      detail: "The combined face confidence, pose, eye-open, and landmark checks fell below the include threshold.",
    });
  }

  addPoseIssue(issues, "Yaw", photo.yaw, MAX_YAW, "Left/right head turn is outside the steady range.");
  addPoseIssue(issues, "Pitch", photo.pitch, MAX_PITCH, "Up/down head tilt is outside the steady range.");
  addPoseIssue(issues, "Roll", photo.roll, MAX_ROLL, "Head rotation is outside the steady range.");

  if (photo.eye_open_ratio != null && photo.eye_open_ratio < MIN_EYE_OPEN) {
    issues.push({
      kind: "eyes",
      title: "Eyes",
      value: `${photo.eye_open_ratio.toFixed(2)} < ${MIN_EYE_OPEN.toFixed(2)}`,
      detail: "The eyes look closed or partly closed, which can make the face morph blink unexpectedly.",
    });
  }

  if (reason === "landmark_outlier") {
    issues.push({
      kind: "drift",
      title: "Landmark drift",
      value: "Outside average",
      detail: "The detected face map is too far from this project's average face, so the anchor may pull features out of place.",
    });
  }

  if (reason === "user_skipped") {
    issues.push({
      kind: "manual",
      title: "Manual choice",
      value: "Not included",
      detail: "This was manually marked as not included. You can include it again if it belongs in the video.",
    });
  }

  if (reason === "replaced_by_newer_capture") {
    issues.push({
      kind: "manual",
      title: "Daily replacement",
      value: "Older take",
      detail: "A newer selfie from the same day is active, so this earlier take is held out of the video.",
    });
  }

  if (issues.length === 0) {
    issues.push({
      kind: "score",
      title: "Review",
      value: humanReason(reason),
      detail: "This photo is currently not included. Include it only if it looks like a stable selfie.",
    });
  }

  const primaryKind = pickPrimaryKind(issues);
  return {
    label: humanReason(reason),
    primaryKind,
    issues,
    summary: summaryFor(primaryKind, issues),
  };
}

function addPoseIssue(issues: ReviewIssue[], label: string, value: number | null, max: number, detail: string) {
  if (value == null || Math.abs(value) <= max) return;
  issues.push({
    kind: "pose",
    title: label,
    value: `${value.toFixed(1)}° > ±${max}°`,
    detail,
  });
}

function pickPrimaryKind(issues: ReviewIssue[]): ReviewIssue["kind"] {
  const priority: ReviewIssue["kind"][] = ["no_face", "face_map", "eyes", "pose", "drift", "score", "manual"];
  return priority.find((kind) => issues.some((issue) => issue.kind === kind)) ?? "score";
}

function summaryFor(kind: ReviewIssue["kind"], issues: ReviewIssue[]) {
  const first = issues.find((issue) => issue.kind === kind) ?? issues[0];
  const summaries: Record<ReviewIssue["kind"], string> = {
    no_face: "No reliable face was found, so the app cannot anchor this frame safely.",
    face_map: "The detailed face map is missing, so this photo cannot be aligned or morphed reliably.",
    score: "The overall face quality check is below the include threshold.",
    pose: first?.detail ?? "The head angle is outside the steady range.",
    eyes: "The eye-open check is below the threshold, so this frame may look like a blink.",
    drift: "The face map is too different from the project average and may cause a visible jump.",
    manual: "This photo is not included because it was manually marked that way.",
  };
  return summaries[kind];
}

function diagramCaption(explanation: ReviewExplanation) {
  const issue = explanation.issues.find((item) => item.kind === explanation.primaryKind) ?? explanation.issues[0];
  if (!issue) return "Review this photo before including it.";
  return issue.value ? `${issue.title}: ${issue.value}` : issue.title;
}

function humanReason(reason: string) {
  const labels: Record<string, string> = {
    no_face_detected: "No face found",
    landmarks_unavailable: "Face map missing",
    low_quality: "Low face score",
    landmark_outlier: "Face map drift",
    user_skipped: "Manually not included",
    replaced_by_newer_capture: "Replaced by newer take",
    review: "Needs review",
  };
  return labels[reason] ?? reason.replace(/_/g, " ");
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}
