import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { LazyThumbnail } from "@/components/LazyThumbnail";
import { PLACEHOLDER_THUMBNAIL_SRC } from "@/constants/placeholders";

class MockIntersectionObserver {
  static instances: MockIntersectionObserver[] = [];
  private callback: IntersectionObserverCallback;

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    MockIntersectionObserver.instances.push(this);
  }

  observe(): void {}

  disconnect(): void {}

  trigger(isIntersecting: boolean): void {
    this.callback(
      [{ isIntersecting } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }
}

describe("LazyThumbnail", () => {
  beforeEach(() => {
    MockIntersectionObserver.instances = [];
    vi.stubGlobal("IntersectionObserver", MockIntersectionObserver);
  });

  it("uses placeholder until the element enters the viewport", () => {
    render(<LazyThumbnail src="http://127.0.0.1:8000/thumb/1" alt="demo" />);

    const image = screen.getByRole("img", { name: "demo" }) as HTMLImageElement;
    expect(image.src).toContain(PLACEHOLDER_THUMBNAIL_SRC.slice(0, 32));

    act(() => {
      MockIntersectionObserver.instances[0]?.trigger(true);
    });
    expect(image.src).toBe("http://127.0.0.1:8000/thumb/1");
  });
});
