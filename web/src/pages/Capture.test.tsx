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

function choosePhotos(files = [new File(["photo"], "photo.jpg", { type: "image/jpeg" })], camera = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const { container } = render(<QueryClientProvider client={client}><Capture onBack={() => {}} onDone={() => {}} /></QueryClientProvider>);
  const pickerName = camera ? "Open camera" : "Choose photos";
  fireEvent.click(screen.getByRole("button", { name: pickerName }));
  const input = container.querySelector<HTMLInputElement>(camera ? "input[capture]" : "input[multiple]")!;
  fireEvent.change(input, { target: { files } });
  return container;
}

describe("Capture dates", () => {
  it("requires the original date when metadata has no timestamp", async () => {
    vi.mocked(api.previewCapture).mockResolvedValue({ items: [preview(null)] });
    choosePhotos();
    await screen.findByText("1 needs a date");
    expect(screen.getByText(/Export Unmodified Originals/)).toBeInTheDocument();
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
    const container = choosePhotos();
    await screen.findByText("Metadata unavailable");
    expect(container.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toBe("");
    expect(screen.getByRole("button", { name: "Use this photo" })).toBeDisabled();
  });

  it("requires the original date when the preview omits a photo", async () => {
    vi.mocked(api.previewCapture).mockResolvedValue({ items: [] });
    const container = choosePhotos();
    await screen.findByText("Metadata unavailable");
    expect(container.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toBe("");
    expect(screen.getByRole("button", { name: "Use this photo" })).toBeDisabled();
  });

  it.each([
    ["2026-10-03 23:59:00", "2026-10-03T23:59:00"],
    ["2026-10-03T23:59:00-06:00", "2026-10-03T23:59:00"],
    ["2026-03-08 02:30:00", "2026-03-08T02:30:00"],
  ])("sends the original clock time for %s without timezone conversion", async (timestamp, expected) => {
    vi.mocked(api.previewCapture).mockResolvedValue({ items: [preview(timestamp)] });
    const container = choosePhotos();
    await screen.findByText("Metadata ready");
    expect(container.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toBe(expected.slice(0, 16));
    fireEvent.click(screen.getByRole("button", { name: "Use this photo" }));
    await waitFor(() => expect(api.captureBatch).toHaveBeenCalledWith([expect.objectContaining({ capturedAt: expected })]));
  });

  it("recovers 33 distinct local dates from historical capture filenames", async () => {
    const files = Array.from({ length: 33 }, (_, index) => {
      const date = new Date(2025, 9, index + 1, 23, 59, 12);
      return new File(["photo"], `capture_${date.getTime()}.jpeg`, { type: "image/jpeg" });
    });
    vi.mocked(api.previewCapture).mockResolvedValue({
      items: files.map((file, index) => ({ ...preview(null), index, filename: file.name })),
    });
    choosePhotos(files);
    expect(await screen.findAllByText("Capture filename")).toHaveLength(33);
    expect(screen.queryByText("Original date required")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Upload 33 photos" }));
    await waitFor(() => expect(api.captureBatch).toHaveBeenCalled());
    const [batch] = vi.mocked(api.captureBatch).mock.calls[0];
    expect(batch).toHaveLength(33);
    expect(new Set(batch.map((item) => item.capturedAt)).size).toBe(33);
    expect(batch[0]).toEqual({ file: files[0], capturedAt: "2025-10-01T23:59:12" });
    expect(batch[32]).toEqual({ file: files[32], capturedAt: "2025-11-02T23:59:12" });
  });

  it("uses the fresh camera file's device-local time when the camera omits EXIF", async () => {
    const captured = new Date(2025, 9, 3, 23, 59, 12).getTime();
    const clock = vi.spyOn(Date, "now").mockReturnValue(captured);
    try {
      const file = new File(["photo"], "image.jpg", { type: "image/jpeg", lastModified: captured });
      vi.mocked(api.previewCapture).mockResolvedValue({ items: [preview(null)] });
      choosePhotos([file], true);
      await screen.findByText("Camera time");
      fireEvent.click(screen.getByRole("button", { name: "Use this photo" }));
      await waitFor(() => expect(api.captureBatch).toHaveBeenCalledWith([{ file, capturedAt: "2025-10-03T23:59:12" }]));
    } finally {
      clock.mockRestore();
    }
  });

  it("leaves a library file undated when its timestamp could come from the picker", async () => {
    const pickerTime = new Date(2025, 9, 3, 23, 59, 12).getTime();
    const clock = vi.spyOn(Date, "now").mockReturnValue(pickerTime);
    try {
      const file = new File(["photo"], `capture_${pickerTime}.jpeg`, { lastModified: pickerTime });
      vi.mocked(api.previewCapture).mockResolvedValue({ items: [{ ...preview(null), filename: file.name }] });
      choosePhotos([file]);
      await screen.findByText("1 needs a date");
      expect(screen.getByRole("button", { name: "Use this photo" })).toBeDisabled();
    } finally {
      clock.mockRestore();
    }
  });

  it("prefers the embedded original date and keeps other dated photos when an export loses its date", async () => {
    const captureName = `capture_${new Date(2025, 9, 3, 23, 59, 12).getTime()}.jpeg`;
    const files = [new File(["photo"], captureName), new File(["photo"], "undated.jpg")];
    vi.mocked(api.previewCapture).mockResolvedValue({
      items: [
        { ...preview("2025-10-04 03:12:32"), filename: captureName },
        { ...preview(null), index: 1, filename: files[1].name },
      ],
    });
    choosePhotos(files);
    await screen.findByText("1 needs a date");
    expect(screen.getByRole("button", { name: "Upload 2 photos" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Keep 1 dated photo" }));
    fireEvent.click(screen.getByRole("button", { name: "Use this photo" }));
    await waitFor(() => expect(api.captureBatch).toHaveBeenCalledWith([{ file: files[0], capturedAt: "2025-10-04T03:12:32" }]));
  });
});
