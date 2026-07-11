import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FaceChange } from "@/pages/FaceChange";
import type { FaceShapeComparison, FaceShapeTrend, Project } from "@/api/client";

const project: Project = {
  id: 1,
  name: "Archive",
  source_folder: "/tmp/archive",
  created_at: "2024-01-01",
  canonical_landmarks_path: "/tmp/canonical.npz",
  photo_count: 12,
  active_count: 12,
  skipped_count: 0,
};

const trend: FaceShapeTrend = {
  status: "ready",
  analysis_version: "face-shape-v1",
  metric: { unit: "personal_robust_sd", baseline_value: 0, higher_means: "fuller_like", disclaimer: "Not weight." },
  baseline: { start: "2024-01-01", end: "2024-06-01", observation_count: 12, frozen: true },
  calibration: { status: "automatic" },
  summary: { latest_date: "2024-06-01", latest_index: 0.7, change_90d: 0.5, direction_90d: "fuller", confidence: "high" },
  insights: [{ kind: "shape", title: "Jaw looks broader relative to cheeks", detail: "This is the clearest regional change." }],
  coverage: { eligible_photos: 12, eligible_days: 12, excluded_photos: 0, first_date: "2024-01-01", last_date: "2024-06-01" },
  events: [{ date: "2024-03-01", type: "capture_profile_change", label: "Capture source changed" }],
  points: [
    point("2024-01-01", -0.4, "a"),
    point("2024-03-01", 0.1, "middle"),
    point("2024-06-01", 0.7, "b"),
  ],
};

const comparison: FaceShapeComparison = {
  a: period("2024-01-01", -0.4, "a"),
  b: period("2024-06-01", 0.7, "b"),
  delta: 1.1,
  uncertainty: 0.2,
  conclusion: "fuller",
  confidence: "high",
  same_capture_profile: true,
  contributions: [{ region: "lower cheek width", feature: "lower_face_width", delta: 0.8 }],
  disclaimer: "Face Shape Index is not weight.",
};

describe("FaceChange", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/compare") ? comparison : trend;
      return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
  });

  it("renders the centered personal index and accessible A/B controls", async () => {
    renderPage();

    expect(await screen.findByText("Your face, changing slowly.")).toBeInTheDocument();
    expect(screen.getByText("Personal baseline · 0")).toBeInTheDocument();
    expect(screen.getByLabelText("Period A")).toHaveAttribute("type", "date");
    await waitFor(() => expect(screen.getByLabelText("Period B")).toHaveValue("2024-06-01"));
    expect(screen.getByText(/not a scale reading/i)).toBeInTheDocument();
    expect(screen.getByText("Jaw looks broader relative to cheeks")).toBeInTheDocument();
    fireEvent.click(screen.getByText("How this was calculated"));
    expect(screen.getByRole("link", { name: "Spreadsheet" })).toHaveAttribute("href", "/api/projects/1/face-shape/export?format=csv");
  });

  it("switches comparison evidence modes and exposes calibration inputs", async () => {
    renderPage();
    await screen.findByText(/Period B appears 1.1 index units fuller-like/i);

    fireEvent.click(screen.getByRole("button", { name: "outline" }));
    expect(screen.getByRole("img", { name: "Overlaid face contours for periods A and B" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Choose anchor periods" }));
    expect(screen.getByLabelText("Known lighter start")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save calibration" })).toBeDisabled();
  });

  it("snaps an entered date to the nearest qualifying observation", async () => {
    renderPage();
    const input = await screen.findByLabelText("Period A");
    fireEvent.change(input, { target: { value: "2024-02-20" } });
    await waitFor(() => expect(input).toHaveValue("2024-03-01"));
  });

  it("explains when there are not enough eligible selfies", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      status: "insufficient",
      analysis_version: "face-shape-v1",
      metric: trend.metric,
      coverage: { measured_photos: 3, required: 6 },
      points: [],
      events: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    renderPage();
    expect(await screen.findByText("A few more clear selfies")).toBeInTheDocument();
    expect(screen.getByText(/3 are ready now/)).toBeInTheDocument();
  });
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><FaceChange project={project} /></QueryClientProvider>);
}

function point(date: string, value: number, hash: string) {
  return {
    date,
    raw_index: value,
    trend_index: value,
    lower: value - 0.1,
    upper: value + 0.1,
    uncertainty: 0.1,
    confidence: "high" as const,
    sample_count: 6,
    window_start: date,
    window_end: date,
    capture_profile: "camera",
    representative: { hash, thumb_url: `/photos/${hash}/thumb`, image_url: `/photos/${hash}/image`, aligned_url: `/photos/${hash}/aligned` },
  };
}

function period(date: string, index: number, hash: string) {
  return {
    start: date,
    end: date,
    index,
    uncertainty: 0.1,
    count: 6,
    distinct_days: 6,
    confidence: "high" as const,
    capture_profiles: ["camera"],
    contour: [[-1, -1], [1, -1], [1, 1], [-1, 1]] as Array<[number, number]>,
    representative: { hash, captured_at: `${date} 10:00:00`, thumb_url: `/photos/${hash}/thumb`, image_url: `/photos/${hash}/image`, aligned_url: `/photos/${hash}/aligned` },
  };
}
