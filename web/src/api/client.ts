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

export type RenderConfig = {
  alignment_mode: "similarity" | "affine";
  morph_mode: "landmark_delaunay" | "rife" | "none";
  intermediate_frames: number;
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

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
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
  patchPhoto: (hash: string, payload: { skipped?: boolean; user_override?: boolean; skip_reason?: string | null }) =>
    fetchJson<Photo>(`/api/photos/${hash}`, { method: "PATCH", body: JSON.stringify(payload) }),
  stats: (projectId: number) => fetchJson(`/api/projects/${projectId}/stats`),
  render: (projectId: number, payload: RenderConfig) =>
    fetchJson<JobStart>(`/api/projects/${projectId}/render`, { method: "POST", body: JSON.stringify(payload) }),
  renders: (projectId: number) => fetchJson<Render[]>(`/api/projects/${projectId}/renders`),
  jobs: () => fetchJson<JobStatus[]>("/api/jobs"),
  cancelJob: (jobId: string) => fetchJson(`/api/jobs/${jobId}`, { method: "DELETE" }),
  defaultSource: () => fetchJson<PathResponse>("/api/system/default-source"),
  inboxStatus: () => fetchJson<InboxStatus>("/api/system/inbox-status"),
  revealFolder: (path?: string | null) =>
    fetchJson<{ ok: boolean; path: string }>("/api/system/reveal", { method: "POST", body: JSON.stringify({ path: path || null }) }),
  pickFolder: () => fetchJson<PathResponse>("/api/system/pick-folder", { method: "POST" }),
  resetAppData: () => fetchJson<{ ok: boolean; inbox_path: string }>("/api/system/reset", { method: "POST", body: JSON.stringify({ confirm: true }) }),
};
