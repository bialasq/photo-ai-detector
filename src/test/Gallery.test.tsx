import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { GalleryMainContent } from "@/components/Gallery";

vi.mock("@/services/api", () => ({
  getThumbnailUrl: (photoId: number) => `http://127.0.0.1:8000/thumb/${photoId}`,
  getPhotoUrl: (photoId: number) => `http://127.0.0.1:8000/photo/${photoId}`,
}));

class MockResizeObserver {
  observe(): void {}
  disconnect(): void {}
}

describe("Gallery virtualization", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        observe(): void {}
        disconnect(): void {}
      },
    );
  });

  it("renders fewer than 100 images when given 1000 photos", () => {
    const photos = Array.from({ length: 1000 }, (_, index) => ({
      photo_id: index + 1,
      file_path: `/photos/img_${index + 1}.jpg`,
    }));

    render(
      <GalleryMainContent
        photos={photos}
        isLoadingPhotos={false}
        error={null}
        onOpenPhoto={() => undefined}
      />,
    );

    expect(screen.getByText(/Found 1000 photos/)).toBeInTheDocument();
    expect(screen.getAllByRole("img").length).toBeLessThan(100);
  });
});
