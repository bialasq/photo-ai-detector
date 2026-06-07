import { describe, expect, it } from "vitest";
import {
  computeScanProgressPercent,
  deriveScanProgressDisplay,
  isScanPhaseActive,
} from "@/utils/scanDisplay";
import type { ScanStatusResponse } from "@/types/api";

function status(
  overrides: Partial<ScanStatusResponse>,
): ScanStatusResponse {
  return {
    processed: 0,
    total: 0,
    is_active: false,
    phase: "idle",
    current_file: null,
    last_error: null,
    cancelled: false,
    ...overrides,
  };
}

describe("scanDisplay", () => {
  it("isScanPhaseActive is true only for scanning and clustering", () => {
    expect(isScanPhaseActive("scanning")).toBe(true);
    expect(isScanPhaseActive("clustering")).toBe(true);
    expect(isScanPhaseActive("idle")).toBe(false);
    expect(isScanPhaseActive("cancelled")).toBe(false);
  });

  it("computeScanProgressPercent returns 100 during clustering", () => {
    expect(
      computeScanProgressPercent(
        status({ phase: "clustering", processed: 3, total: 10, is_active: true }),
      ),
    ).toBe(100);
  });

  it("computeScanProgressPercent rounds processed/total during scanning", () => {
    expect(
      computeScanProgressPercent(
        status({ phase: "scanning", processed: 1, total: 4, is_active: true }),
      ),
    ).toBe(25);
  });

  it("deriveScanProgressDisplay returns null when idle and inactive", () => {
    expect(deriveScanProgressDisplay(status({ phase: "idle" }))).toBeNull();
  });

  it("deriveScanProgressDisplay shows scanning title and basename-only file label", () => {
    const display = deriveScanProgressDisplay(
      status({
        phase: "scanning",
        is_active: true,
        processed: 2,
        total: 10,
        current_file: "C:\\Users\\Grzesiek\\Pictures\\vacation\\IMG_001.jpg",
      }),
    );

    expect(display).not.toBeNull();
    expect(display?.phase).toBe("scanning");
    expect(display?.title).toBe("Scanning images");
    expect(display?.detail).toBe("Processing photo 3 of 10: IMG_001.jpg");
    expect(display?.detail).not.toContain("Grzesiek");
    expect(display?.detail).not.toContain("C:\\");
    expect(display?.percent).toBe(20);
  });

  it("deriveScanProgressDisplay shows clustering copy", () => {
    const display = deriveScanProgressDisplay(
      status({
        phase: "clustering",
        is_active: true,
        processed: 10,
        total: 10,
      }),
    );

    expect(display).toMatchObject({
      phase: "clustering",
      title: "Running DBSCAN clustering",
      detail: "Grouping faces across 10 indexed photos…",
      percent: 100,
      indeterminate: true,
    });
  });

  it("deriveScanProgressDisplay shows stopping copy while cancelled scan is still active", () => {
    const display = deriveScanProgressDisplay(
      status({
        phase: "cancelled",
        cancelled: true,
        is_active: true,
        processed: 4,
        total: 10,
      }),
    );

    expect(display).toMatchObject({
      phase: "cancelled",
      title: "Stopping scan…",
      detail: "Finishing the current batch before saving progress.",
      indeterminate: true,
    });
  });

  it("deriveScanProgressDisplay hides overlay after cancelled scan finishes", () => {
    expect(
      deriveScanProgressDisplay(
        status({
          phase: "cancelled",
          cancelled: true,
          is_active: false,
          processed: 4,
          total: 10,
        }),
      ),
    ).toBeNull();
  });
});
