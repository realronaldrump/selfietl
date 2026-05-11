export type Project = {
  id: number;
  name: string | null;
  source_folder: string;
  created_at: string;
  canonical_landmarks_path: string | null;
  photo_count: number;
  active_count: number;
  skipped_count: number;
};

export type Photo = {
  hash: string;
  path: string;
  captured_at: string;
  width: number | null;
  height: number | null;
  file_size: number | null;
  camera_make: string | null;
  camera_model: string | null;
  quality_score: number | null;
  yaw: number | null;
  pitch: number | null;
  roll: number | null;
  eye_open_ratio: number | null;
  mouth_open_ratio: number | null;
  skipped: boolean;
  skip_reason: string | null;
  user_override: boolean;
  thumb_url: string;
  image_url: string;
};

export type PhotoList = {
  items: Photo[];
  total: number;
  limit: number;
  offset: number;
};

export type JobStart = {
  job_id: string;
  status_url: string;
  events_url: string;
};

export type CapturePreviewItem = {
  index: number;
  filename: string;
  file_size: number;
  supported: boolean;
  captured_at: string | null;
  captured_at_source: string | null;
  camera_make: string | null;
  camera_model: string | null;
  width: number | null;
  height: number | null;
  warnings: string[];
  error: string | null;
};

export type CapturePreviewResponse = {
  items: CapturePreviewItem[];
};

export type CaptureBatchItem = {
  file: File;
  capturedAt?: string | null;
};

export type JobStatus = {
  id: string;
  name: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number;
  progress_done: number;
  progress_total: number;
  stage: string | null;
  message: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
};

export type Render = {
  id: number;
  project_id: number;
  output_path: string | null;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  config: Record<string, unknown> | null;
};

export type CleanupResult = {
  ok: boolean;
  deleted?: number;
  deleted_render_ids?: number[];
  deleted_files?: string[];
  missing_files?: string[];
  deleted_cache_dirs?: string[];
  freed_bytes?: number;
};

export type RenderConfig = {
  alignment_mode: "similarity" | "affine";
  morph_mode: "landmark_delaunay" | "rife" | "none";
  intermediate_frames: number;
  start_date?: string | null;
  end_date?: string | null;
  color_normalize: boolean;
  fps: number;
  resolution: "original" | "1080_square" | "1080_vertical" | "4k_landscape";
  aspect_ratio: "original" | "square" | "9:16" | "16:9";
  date_overlay: {
    enabled: boolean;
    format: string;
    position: "bottom-right" | "bottom-left" | "top-right" | "top-left";
    font_size_px: number;
    opacity: number;
  };
  audio_path: string | null;
  music_sync: boolean;
  fade_in_seconds: number;
  fade_out_seconds: number;
  codec: "h264" | "h265";
  crf: number;
  output_path?: string | null;
};

export type PathResponse = {
  path: string;
};

export type InboxStatus = {
  path: string;
  total_files: number;
  supported_files: number;
  project_id: number | null;
  cataloged_files: number;
  detected_files: number;
  last_scanned_at: string | null;
  needs_scan: boolean;
  needs_detection: boolean;
};

export type CapturedPhoto = {
  hash: string;
  captured_at: string;
  quality_score: number | null;
  yaw: number | null;
  pitch: number | null;
  roll: number | null;
  eye_open_ratio: number | null;
  skipped: boolean;
  skip_reason: string | null;
  user_override: boolean;
  thumb_url: string;
  image_url: string;
  aligned_url: string | null;
  warnings: string[];
};

export type LatestRender = {
  id: number;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  output_path: string | null;
  video_url: string | null;
};

export type TodayProject = {
  id: number;
  name: string | null;
  source_folder: string;
  photo_count: number;
  active_count: number;
};

export type TodayResponse = {
  date: string;
  has_today: boolean;
  streak: number;
  longest_streak: number;
  total_days: number;
  today_photo: CapturedPhoto | null;
  latest_render: LatestRender | null;
  project: TodayProject | null;
  canonical_ready: boolean;
};

export type DayPhotosResponse = {
  date: string;
  photos: CapturedPhoto[];
};

export type CalendarDay = {
  date: string;
  count: number;
  has_active: boolean;
  quality: number | null;
  thumb_url: string | null;
  hash: string | null;
};

export type CalendarResponse = {
  days: CalendarDay[];
  start: string;
  end: string;
};

export type AutoRenderConfig = {
  enabled: boolean;
  time: string;
  next_run_at: string;
  last_run_date: string | null;
  last_render_id: number | null;
  last_attempt_at: string | null;
  last_error: string | null;
  last_render: LatestRender | null;
  render_config: Record<string, unknown>;
  project_id: number | null;
  scheduler_running: boolean;
};

export type AutoRenderUpdate = {
  enabled?: boolean;
  time?: string;
  render_config?: Record<string, unknown>;
};

const SELFIE_TL_BASE_PATH = "/selfietl";

function apiBasePath(): string {
  if (typeof window === "undefined") return "/api";
  const path = window.location.pathname;
  return path === SELFIE_TL_BASE_PATH || path.startsWith(`${SELFIE_TL_BASE_PATH}/`) ? `${SELFIE_TL_BASE_PATH}/api` : "/api";
}

export function apiUrl(path: string): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  const withoutApiPrefix = normalized === "/api" ? "" : normalized.startsWith("/api/") ? normalized.slice(4) : normalized;
  return `${apiBasePath()}${withoutApiPrefix}`;
}

export function renderFileUrl(renderId: number): string {
  return apiUrl(`/renders/${renderId}/file`);
}

export function renderPlaybackUrl(renderId: number): string {
  return apiUrl(`/renders/${renderId}/playback.mp4`);
}

export function renderPosterUrl(renderId: number): string {
  return apiUrl(`/renders/${renderId}/poster.jpg`);
}

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(url), {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      message = payload.detail ?? message;
    } catch {
      // Keep HTTP status.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  projects: () => fetchJson<Project[]>("/api/projects"),
  createProject: (payload: { name: string; source_folder: string }) =>
    fetchJson<Project>("/api/projects", { method: "POST", body: JSON.stringify(payload) }),
  scan: (projectId: number) => fetchJson<JobStart>(`/api/projects/${projectId}/scan`, { method: "POST" }),
  detect: (projectId: number) => fetchJson<JobStart>(`/api/projects/${projectId}/detect`, { method: "POST" }),
  recompute: (projectId: number) => fetchJson<JobStart>(`/api/projects/${projectId}/recompute`, { method: "POST" }),
  photos: (projectId: number, params: { offset?: number; limit?: number; skipped?: boolean | null } = {}) => {
    const query = new URLSearchParams();
    query.set("limit", String(params.limit ?? 80));
    query.set("offset", String(params.offset ?? 0));
    if (params.skipped !== null && params.skipped !== undefined) query.set("skipped", String(params.skipped));
    return fetchJson<PhotoList>(`/api/projects/${projectId}/photos?${query.toString()}`);
  },
  patchPhoto: (
    hash: string,
    payload: { skipped?: boolean; user_override?: boolean; skip_reason?: string | null; captured_at?: string | null },
  ) =>
    fetchJson<Photo>(`/api/photos/${hash}`, { method: "PATCH", body: JSON.stringify(payload) }),
  stats: (projectId: number) => fetchJson(`/api/projects/${projectId}/stats`),
  render: (projectId: number, payload: RenderConfig) =>
    fetchJson<JobStart>(`/api/projects/${projectId}/render`, { method: "POST", body: JSON.stringify(payload) }),
  renders: (projectId: number) => fetchJson<Render[]>(`/api/projects/${projectId}/renders`),
  deleteRenderHistory: (projectId: number, params: { status?: string; deleteFiles?: boolean; deleteCache?: boolean } = {}) => {
    const query = new URLSearchParams();
    query.set("status", params.status ?? "failed,cancelled");
    query.set("delete_files", String(params.deleteFiles ?? true));
    query.set("delete_cache", String(params.deleteCache ?? true));
    return fetchJson<CleanupResult>(`/api/projects/${projectId}/renders?${query.toString()}`, { method: "DELETE" });
  },
  deleteRender: (renderId: number, params: { deleteFile?: boolean; deleteCache?: boolean } = {}) => {
    const query = new URLSearchParams();
    query.set("delete_file", String(params.deleteFile ?? true));
    query.set("delete_cache", String(params.deleteCache ?? true));
    return fetchJson<CleanupResult>(`/api/renders/${renderId}?${query.toString()}`, { method: "DELETE" });
  },
  clearRenderCache: () => fetchJson<CleanupResult>("/api/render-cache", { method: "DELETE" }),
  jobs: () => fetchJson<JobStatus[]>("/api/jobs"),
  job: (jobId: string) => fetchJson<JobStatus>(`/api/jobs/${jobId}`),
  cancelJob: (jobId: string) => fetchJson(`/api/jobs/${jobId}`, { method: "DELETE" }),
  clearCompletedJobs: () => fetchJson<CleanupResult>("/api/jobs", { method: "DELETE" }),
  defaultSource: () => fetchJson<PathResponse>("/api/system/default-source"),
  inboxStatus: () => fetchJson<InboxStatus>("/api/system/inbox-status"),
  revealFolder: (path?: string | null) =>
    fetchJson<{ ok: boolean; path: string }>("/api/system/reveal", { method: "POST", body: JSON.stringify({ path: path || null }) }),
  pickFolder: () => fetchJson<PathResponse>("/api/system/pick-folder", { method: "POST" }),
  resetAppData: () => fetchJson<{ ok: boolean; inbox_path: string }>("/api/system/reset", { method: "POST", body: JSON.stringify({ confirm: true }) }),
  today: () => fetchJson<TodayResponse>("/api/today"),
  previewCapture: (files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));
    return fetch(apiUrl("/capture/preview"), { method: "POST", body: form }).then(async (response) => {
      if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        try {
          const payload = await response.json();
          message = payload.detail ?? message;
        } catch {
          // keep status text
        }
        throw new Error(message);
      }
      return response.json() as Promise<CapturePreviewResponse>;
    });
  },
  capture: (file: File | Blob, capturedAt?: string) => {
    const form = new FormData();
    const filename = file instanceof File ? file.name : "selfie.jpg";
    form.append("file", file, filename);
    const query = capturedAt ? `?captured_at=${encodeURIComponent(capturedAt)}` : "";
    return fetch(apiUrl(`/capture${query}`), { method: "POST", body: form }).then(async (response) => {
      if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        try {
          const payload = await response.json();
          message = payload.detail ?? message;
        } catch {
          // keep status text
        }
        throw new Error(message);
      }
      return response.json() as Promise<JobStart>;
    });
  },
  captureBatch: (items: CaptureBatchItem[]) => {
    const form = new FormData();
    const metadata = items.map((item) => ({ captured_at: item.capturedAt ?? null }));
    items.forEach((item) => form.append("files", item.file, item.file.name));
    form.append("metadata", JSON.stringify(metadata));
    return fetch(apiUrl("/capture/batch"), { method: "POST", body: form }).then(async (response) => {
      if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        try {
          const payload = await response.json();
          message = payload.detail ?? message;
        } catch {
          // keep status text
        }
        throw new Error(message);
      }
      return response.json() as Promise<JobStart>;
    });
  },
  deleteCapture: (hash: string) =>
    fetchJson<{ hash: string; deleted: boolean }>(`/api/capture/${hash}`, { method: "DELETE" }),
  photosByDate: (date: string) => fetchJson<DayPhotosResponse>(`/api/photos/by-date/${date}`),
  calendar: (params: { start?: string; end?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.start) query.set("start", params.start);
    if (params.end) query.set("end", params.end);
    const suffix = query.toString();
    return fetchJson<CalendarResponse>(`/api/calendar${suffix ? `?${suffix}` : ""}`);
  },
  autoRender: () => fetchJson<AutoRenderConfig>("/api/auto-render"),
  updateAutoRender: (payload: AutoRenderUpdate) =>
    fetchJson<AutoRenderConfig>("/api/auto-render", { method: "PATCH", body: JSON.stringify(payload) }),
  runAutoRenderNow: () => fetchJson<JobStart>("/api/auto-render/run", { method: "POST" }),
};
