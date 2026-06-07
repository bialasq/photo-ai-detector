import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchGalleryPhotos, searchPhotos } from "@/services/api";

describe("api fetch helpers", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetchGalleryPhotos builds gallery query with person_ids and ai_status", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify([{ photo_id: 1, file_path: "/a.jpg" }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await fetchGalleryPhotos({
      person_ids: [1, 2],
      ai_filter: "processed",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/gallery?person_ids=1%2C2&ai_status=processed",
    );
  });

  it("searchPhotos encodes comma-separated names including non-ASCII characters", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await searchPhotos(["Anna", "Łukasz"]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      `http://127.0.0.1:8000/api/search?names=${encodeURIComponent("Anna,Łukasz")}`,
    );
    expect(url).toContain("%C5%81ukasz");
  });

  it("fetchGalleryPhotos throws with unified API error message on 404", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          error: "No photo found",
          code: "NOT_FOUND",
          hint: "The requested resource was not found.",
        }),
        {
          status: 404,
          statusText: "Not Found",
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await expect(
      fetchGalleryPhotos({ person_ids: [], ai_filter: "all" }),
    ).rejects.toThrow("The requested resource was not found.");
  });
});
