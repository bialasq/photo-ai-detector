import { describe, expect, it } from "vitest";
import {
  BASE_URL,
  getClusterThumbnailUrl,
  getFaceCropThumbnailUrl,
  getNoiseFaceThumbnailUrl,
  getPersonThumbnailUrl,
  getPhotoUrl,
  getThumbnailUrl,
  resolveApiThumbnailUrl,
  resolveNoiseFaceThumbnailUrl,
  resolvePersonThumbnailUrl,
} from "@/services/api";
import type { NoiseFaceItem, PersonSummaryItem } from "@/types/api";

describe("api URL builders", () => {
  it("getThumbnailUrl uses default and custom width", () => {
    expect(getThumbnailUrl(42)).toBe(
      "http://127.0.0.1:8000/api/photos/42/thumbnail?width=300",
    );
    expect(getThumbnailUrl(42, 160)).toBe(
      "http://127.0.0.1:8000/api/photos/42/thumbnail?width=160",
    );
  });

  it("getPhotoUrl points at the full-resolution file endpoint", () => {
    expect(getPhotoUrl(7)).toBe("http://127.0.0.1:8000/api/photos/7/file");
  });

  it("getFaceCropThumbnailUrl includes crop=1 and default width 128", () => {
    expect(getFaceCropThumbnailUrl(15)).toBe(
      "http://127.0.0.1:8000/api/faces/15/thumbnail?crop=1&width=128",
    );
    expect(getFaceCropThumbnailUrl(15, 64)).toBe(
      "http://127.0.0.1:8000/api/faces/15/thumbnail?crop=1&width=64",
    );
  });

  it("getPersonThumbnailUrl and getClusterThumbnailUrl use width defaults", () => {
    expect(getPersonThumbnailUrl(3)).toBe(
      "http://127.0.0.1:8000/api/people/3/thumbnail?width=300",
    );
    expect(getClusterThumbnailUrl(9, 200)).toBe(
      "http://127.0.0.1:8000/api/clusters/9/thumbnail?width=200",
    );
  });

  it("getNoiseFaceThumbnailUrl defaults width to 96", () => {
    expect(getNoiseFaceThumbnailUrl(88)).toBe(
      "http://127.0.0.1:8000/api/faces/88/thumbnail?width=96",
    );
  });

  it("resolveApiThumbnailUrl leaves absolute URLs unchanged", () => {
    const absolute = "http://127.0.0.1:8000/api/photos/1/thumbnail?width=96";
    expect(resolveApiThumbnailUrl(absolute)).toBe(absolute);
  });

  it("resolveApiThumbnailUrl prefixes relative API paths with BASE_URL", () => {
    expect(resolveApiThumbnailUrl("/api/faces/2/thumbnail?crop=1&width=96")).toBe(
      `${BASE_URL}/api/faces/2/thumbnail?crop=1&width=96`,
    );
    expect(resolveApiThumbnailUrl("api/photos/5/thumbnail?width=120")).toBe(
      `${BASE_URL}/api/photos/5/thumbnail?width=120`,
    );
  });

  it("resolveNoiseFaceThumbnailUrl handles absolute and relative thumbnail_url", () => {
    const absoluteItem: NoiseFaceItem = {
      face_id: 1,
      photo_id: 1,
      bounding_box: { x: 0, y: 0, w: 10, h: 10 },
      thumbnail_url: "http://127.0.0.1:8000/api/faces/1/thumbnail?width=96",
    };
    expect(resolveNoiseFaceThumbnailUrl(absoluteItem)).toBe(
      absoluteItem.thumbnail_url,
    );

    const relativeItem: NoiseFaceItem = {
      face_id: 2,
      photo_id: 2,
      bounding_box: { x: 0, y: 0, w: 10, h: 10 },
      thumbnail_url: "/api/faces/2/thumbnail?crop=1&width=96",
    };
    expect(resolveNoiseFaceThumbnailUrl(relativeItem, 120)).toBe(
      `${BASE_URL}/api/faces/2/thumbnail?width=120`,
    );
  });

  it("resolvePersonThumbnailUrl prefers exemplar face crop over person thumbnail", () => {
    const withFace: PersonSummaryItem = {
      id: 10,
      name: "Anna",
      face_count: 1,
      exemplar_photo_path: null,
      exemplar_face_id: 55,
      bounding_box: null,
    };
    expect(resolvePersonThumbnailUrl(withFace)).toBe(
      "http://127.0.0.1:8000/api/faces/55/thumbnail?crop=1&width=128",
    );

    const withoutFace: PersonSummaryItem = {
      id: 11,
      name: "Bartek",
      face_count: 0,
      exemplar_photo_path: null,
      exemplar_face_id: null,
      bounding_box: null,
    };
    expect(resolvePersonThumbnailUrl(withoutFace, 240)).toBe(
      "http://127.0.0.1:8000/api/people/11/thumbnail?width=240",
    );
  });
});
