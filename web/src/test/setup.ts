import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => cleanup());

class TestResizeObserver {
  private callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe(target: Element) {
    this.callback(
      [{ target, contentRect: { width: 800, height: 400, x: 0, y: 0, top: 0, left: 0, right: 800, bottom: 400, toJSON: () => ({}) } } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }

  unobserve() {}
  disconnect() {}
}

vi.stubGlobal("ResizeObserver", TestResizeObserver);
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});
