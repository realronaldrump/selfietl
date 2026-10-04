import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, type CapturePreviewItem } from "@/api/client";
import { Capture } from "@/pages/Capture";

vi.mock("@/api/client", () => ({ api: { previewCapture: vi.fn(), captureBatch: vi.fn() } }));
vi.mock("@/hooks/useJobEvents", () => ({ useJobEvents: () => null }));

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:photo") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  vi.mocked(api.captureBatch).mockResolvedValue({ job_id: "job", status_url: "/api/jobs/job", events_url: "/api/jobs/job/events" });
});

function preview(capturedAt: string | null): CapturePreviewItem {
  return {
    index: 0, filename: "photo.jpg", file_size: 5, supported: true,
    captured_at: capturedAt, captured_at_source: capturedAt ? "exif_datetime_original" : null,
    camera_make: null, camera_model: null, width: 32, height: 32,
    warnings: capturedAt ? [] : ["missing_capture_timestamp"], error: null,
  };
}

function choosePhoto() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const { container } = render(<QueryClientProvider client={client}><Capture onBack={() => {}} onDone={() => {}} /></QueryClientProvider>);
  const input = container.querySelector<HTMLInputElement>('input[type="file"][multiple]')!;
  fireEvent.change(input, { target: { files: [new File(["photo"], "photo.jpg", { type: "image/jpeg", lastModified: Date.now() })] } });
  return container;
}

describe("Capture dates", () => {
  it("requires the original date when metadata has no timestamp", async () => {
    vi.mocked(api.previewCapture).mockResolvedValue({ items: [preview(null)] });
    choosePhoto();
    await screen.findByText("Metadata ready");
    const date = screen.getByLabelText("Original date and time") as HTMLInputElement;
    expect(date.value).toBe("");
    expect(screen.getByRole("button", { name: "Use this photo" })).toBeDisabled();
    expect(api.captureBatch).not.toHaveBeenCalled();
    fireEvent.change(date, { target: { value: "2026-10-03T23:59:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Use this photo" }));
    await waitFor(() => expect(api.captureBatch).toHaveBeenCalledWith([expect.objectContaining({ capturedAt: "2026-10-03T23:59:00" })]));
  });

  it("requires the original date after a metadata request fails", async () => {
    vi.mocked(api.previewCapture).mockRejectedValue(new Error("Could not read photo date"));
    const container = choosePhoto();
    await screen.findByText("Metadata ready");
    expect(container.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toBe("");
    expect(screen.getByRole("button", { name: "Use this photo" })).toBeDisabled();
  });

  it("requires the original date when the preview omits a photo", async () => {
    vi.mocked(api.previewCapture).mockResolvedValue({ items: [] });
    const container = choosePhoto();
    await screen.findByText("Metadata ready");
    expect(container.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toBe("");
    expect(screen.getByRole("button", { name: "Use this photo" })).toBeDisabled();
  });

  it.each([
    ["2026-10-03 23:59:00", "2026-10-03T23:59:00"],
    ["2026-10-03T23:59:00-06:00", "2026-10-03T23:59:00"],
    ["2026-03-08 02:30:00", "2026-03-08T02:30:00"],
  ])("sends the original clock time for %s without timezone conversion", async (timestamp, expected) => {
    vi.mocked(api.previewCapture).mockResolvedValue({ items: [preview(timestamp)] });
    const container = choosePhoto();
    await screen.findByText("Metadata ready");
    expect(container.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toBe(expected.slice(0, 16));
    fireEvent.click(screen.getByRole("button", { name: "Use this photo" }));
    await waitFor(() => expect(api.captureBatch).toHaveBeenCalledWith([expect.objectContaining({ capturedAt: expected })]));
  });
});
