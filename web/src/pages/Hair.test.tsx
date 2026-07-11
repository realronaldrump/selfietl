import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HairManifest, Project } from "@/api/client";
import { Hair } from "@/pages/Hair";

const project: Project = {
  id: 1,
  name: "Archive",
  source_folder: "/tmp/archive",
  created_at: "2024-01-01",
  canonical_landmarks_path: "/tmp/canonical.npz",
  photo_count: 2,
  active_count: 2,
  skipped_count: 0,
};

let manifest: HairManifest;

describe("Hair", () => {
  beforeEach(() => {
    manifest = {
      status: "ready",
      analysis_version: "hair-v1",
      analysis_revision: "revision-1",
      coverage: { available: 2, included: 2, excluded: 0, total_photos: 2 },
      face_outline: [],
      frames: [frame("a", "2024-01-01"), frame("b", "2024-02-01")],
      haircuts: [{ id: 8, event_date: "2024-02-01", first_after_photo_hash: "b", source: "automatic", status: "suggested", score: 3.2 }],
      latest_export: { id: 4, status: "done", stale: false, file_url: "/api/hair-exports/4/file", playback_url: "/api/hair-exports/4/playback.mp4", finished_at: "2024-02-02", config: { seconds_per_selfie: 1 } },
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/photos/") && url.endsWith("/hair") && init?.method === "PATCH") {
        const hash = url.split("/photos/")[1].split("/")[0];
        manifest.frames = manifest.frames.map((item) => item.hash === hash ? { ...item, excluded: true } : item);
        manifest.coverage = { ...manifest.coverage, included: 1, excluded: 1 };
        return json({ hash, excluded: true });
      }
      if (url.includes("/haircuts/8") && init?.method === "PATCH") {
        manifest.haircuts = manifest.haircuts.map((event) => event.id === 8 ? { ...event, status: "confirmed" } : event);
        return json(manifest.haircuts[0]);
      }
      return json(manifest);
    }));
  });

  it("renders the responsive player, contact sheet, and haircut suggestion", async () => {
    renderPage();

    expect(await screen.findByText("Hair, over time.")).toBeInTheDocument();
    expect(screen.getByText("Daily silhouette")).toBeInTheDocument();
    expect(screen.getByText("Selfie-day contact sheet")).toBeInTheDocument();
    expect(screen.getByText("Possible cut")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "MP4" })).toHaveAttribute("href", "/api/hair-exports/4/file");
    expect(screen.getByRole("button", { name: "Confirm haircut" })).toHaveClass("h-11");
  });

  it("excludes a hair frame without changing the photo itself", async () => {
    renderPage();
    await screen.findByText("Hair, over time.");

    fireEvent.click(screen.getByRole("button", { name: "Exclude this hair frame" }));

    await waitFor(() => expect(screen.getByText("Excluded")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith("/api/photos/b/hair", expect.objectContaining({ method: "PATCH" }));
  });

  it("confirms an automatic haircut suggestion", async () => {
    renderPage();
    await screen.findByText("Possible cut");

    fireEvent.click(screen.getByRole("button", { name: "Confirm haircut" }));

    await waitFor(() => expect(screen.getByText("Confirmed")).toBeInTheDocument());
  });
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><Hair project={project} /></QueryClientProvider>);
}

function frame(hash: string, date: string) {
  return {
    hash,
    date,
    captured_at: `${date} 10:00:00`,
    quality: 0.88,
    eligible: true,
    excluded: false,
    reasons: [],
    thumb_url: `/api/photos/${hash}/thumb`,
    source_url: `/api/photos/${hash}/image`,
    composite_url: `/api/photos/${hash}/hair-composite.png`,
  };
}

function json(value: unknown) {
  return Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } }));
}
