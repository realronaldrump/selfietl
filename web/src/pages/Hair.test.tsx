import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HairManifest, Project } from "@/api/client";
import { daysBetween, exportPayload, filterFrames, Hair } from "@/pages/Hair";

const project: Project = { id: 1, name: "Archive", source_folder: "/tmp/archive", created_at: "2024-01-01", canonical_landmarks_path: "/tmp/canonical.npz", photo_count: 2, active_count: 2, skipped_count: 0 };
let manifest: HairManifest;

beforeEach(() => {
  manifest = {
    status: "ready", analysis_version: "hair-v2", analysis_revision: "revision-1",
    coverage: { available: 2, included: 2, excluded: 0, total_photos: 2 }, face_outline: [],
    frames: [frame("a", "2026-04-16"), frame("b", "2026-09-26")],
    haircuts: [{ id: 8, event_date: "2026-04-16", first_after_photo_hash: "b", source: "automatic", status: "suggested", score: 3.2, evidence: { before_photo_hash: "a", after_photo_hash: "b", earliest_date: "2026-04-14", latest_date: "2026-04-16", baseline_days: 3, following_days: 2 } }],
    last_haircut: null,
    analysis: { latest_photo_date: "2026-09-26", latest_analyzed_date: "2026-09-26", updated_at: "2026-09-26T10:00:00", pending_photos: 0, failed_photos: 0 },
    change_since_haircut: null,
    latest_export: { id: 4, status: "done", stale: false, file_url: "/api/hair-exports/4/file", playback_url: "/api/hair-exports/4/playback.mp4", finished_at: "2026-09-26", config: { seconds_per_selfie: 1 } },
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
      const update = JSON.parse(String(init.body));
      manifest.haircuts = manifest.haircuts.map((event) => event.id === 8 ? { ...event, ...update } : event);
      if (update.status === "confirmed") manifest.last_haircut = { id: 8, event_date: update.event_date ?? "2026-04-16", days_since: 0 };
      return json(manifest.haircuts[0]);
    }
    return json(manifest);
  }));
});
afterEach(() => vi.useRealTimers());

describe("Hair", () => {
  it("shows plain labels, the latest analysis date, and a reviewable suggestion", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "Hair", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Hair timeline")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Photos" })).toBeInTheDocument();
    expect(screen.getByText("Possible haircut")).toBeInTheDocument();
    expect(screen.getByText("September 26, 2026", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download MP4" })).toHaveAttribute("href", "/api/hair-exports/4/file");
    fireEvent.click(screen.getByRole("button", { name: "Review photos" }));
    expect(screen.getByAltText("Selfie before the possible haircut")).toHaveAttribute("src", "/api/photos/a/image");
    expect(screen.getByAltText("Selfie after the possible haircut")).toHaveAttribute("src", "/api/photos/b/image");
    expect(screen.queryByText(/mask confidence|anchored|silhouette changes|one clear beat/i)).not.toBeInTheDocument();
  });

  it("excludes a hair frame", async () => {
    renderPage(); await screen.findByRole("heading", { name: "Hair", level: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Exclude this hair frame" }));
    await waitFor(() => expect(screen.getByText("Excluded")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith("/api/photos/b/hair", expect.objectContaining({ method: "PATCH" }));
  });

  it("counts calendar days from confirmed haircuts and updates after confirmation", async () => {
    vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date("2026-10-04T12:00:00"));
    renderPage(); await screen.findByText("Possible haircut");
    expect(screen.getByText("No haircut recorded")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm haircut" }));
    await waitFor(() => expect(screen.getByText("Confirmed")).toBeInTheDocument());
    expect(screen.getByTestId("haircut-elapsed")).toHaveTextContent("171 days");
  });

  it("shows haircut controls even without analyzed photos", async () => {
    manifest.status = "insufficient"; manifest.frames = []; manifest.latest_export = null;
    manifest.last_haircut = { id: 9, event_date: "2026-04-16", days_since: 171 };
    renderPage();
    expect(await screen.findByRole("button", { name: "Add haircut" })).toBeInTheDocument();
    expect(screen.getByTestId("haircut-elapsed")).toBeInTheDocument();
  });

  it("does not label an ineligible mask as included", async () => {
    manifest.frames[1] = { ...manifest.frames[1], eligible: false, composite_url: null, reasons: ["head_pose"] };
    manifest.coverage.included = 1;
    renderPage();
    expect(await screen.findByText("Not used")).toBeInTheDocument();
    expect(screen.getByText("Head angle is too different.")).toBeInTheDocument();
    expect(screen.getByAltText("Selfie on September 26, 2026")).toHaveAttribute("src", "/api/photos/b/image");
  });

  it("shows save errors and keeps the date editable", async () => {
    vi.mocked(fetch).mockImplementation(async (_input, init) => init?.method === "PATCH" ? new Response(JSON.stringify({ detail: "Could not save haircut" }), { status: 400 }) : json(manifest));
    renderPage(); await screen.findByText("Possible haircut");
    fireEvent.click(screen.getByRole("button", { name: "Confirm haircut" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save haircut");
    expect(screen.getByLabelText("Date for haircut 8")).not.toBeDisabled();
  });

  it("does not save a cleared or future date", async () => {
    renderPage(); await screen.findByText("Possible haircut");
    fireEvent.change(screen.getByLabelText("Date for haircut 8"), { target: { value: "" } });
    expect(screen.getByRole("button", { name: "Confirm haircut" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save date" })).toBeDisabled();
  });

  it("distinguishes a provisional change that needs more photos", async () => {
    manifest.haircuts[0].status = "provisional";
    renderPage();
    expect(await screen.findByText(/Waiting for more photos/)).toBeInTheDocument();
    expect(screen.getByText("No haircut recorded")).toBeInTheDocument();
  });

  it("switches between the original photo and the mask", async () => {
    renderPage(); await screen.findByRole("heading", { name: "Hair", level: 1 });
    fireEvent.click(within(screen.getByRole("group", { name: "Photo preview" })).getByRole("button", { name: "Mask" }));
    expect(screen.getByAltText("Hair mask on September 26, 2026")).toHaveAttribute("src", "/api/photos/b/hair-composite.png");
  });
});

it("uses the same archived dates for the range and the export", () => {
  const frames = [frame("a", "2020-01-01"), frame("b", "2020-02-29"), frame("c", "2020-08-31")];
  const filtered = filterFrames(frames, "6m");
  expect(filtered.map((item) => item.hash)).toEqual(["b", "c"]);
  expect(exportPayload(filtered, "6m", 2)).toEqual({ start_date: "2020-02-29", end_date: "2020-08-31", seconds_per_selfie: 0.5 });
});
it("counts leap days and daylight-saving boundaries as calendar days", () => {
  expect(daysBetween("2024-02-28", "2024-03-01")).toBe(2);
  expect(daysBetween("2026-03-07", "2026-03-09")).toBe(2);
  expect(daysBetween("2026-11-01", "2026-11-02")).toBe(1);
});
function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><Hair project={project} /></QueryClientProvider>);
}
function frame(hash: string, date: string) {
  return { hash, date, captured_at: `${date} 10:00:00`, quality: 0.88, eligible: true, excluded: false, reasons: [], metrics: { area: 4.2 }, thumb_url: `/api/photos/${hash}/thumb`, source_url: `/api/photos/${hash}/image`, composite_url: `/api/photos/${hash}/hair-composite.png` };
}
function json(value: unknown) { return Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } })); }
