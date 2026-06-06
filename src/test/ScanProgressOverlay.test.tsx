import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { ScanProgressOverlay } from "@/components/ScanProgressOverlay";

const cancelFolderScan = vi.fn().mockResolvedValue(undefined);
const clearScanActionError = vi.fn();

vi.mock("@/context/AppContext", () => ({
  useAppContext: () => ({
    scanDisplay: {
      phase: "scanning" as const,
      title: "Scanning images",
      detail: "Processing photo 1 of 10",
      percent: 10,
      indeterminate: false,
    },
    scanStatus: {
      processed: 1,
      total: 10,
      is_active: true,
      phase: "scanning" as const,
      current_file: "a.jpg",
      last_error: null,
      cancelled: false,
    },
    scanActionError: null,
    isScanning: true,
    cancelFolderScan,
    clearScanActionError,
  }),
}));

vi.mock("@/api/scan", () => ({
  cancelScan: vi.fn(),
}));

describe("ScanProgressOverlay", () => {
  beforeEach(() => {
    cancelFolderScan.mockClear();
    vi.stubGlobal("confirm", vi.fn(() => true));
  });

  it("posts cancel when Stop scan is clicked", () => {
    render(<ScanProgressOverlay />);

    fireEvent.click(screen.getByRole("button", { name: /Stop scan/i }));

    expect(cancelFolderScan).toHaveBeenCalledTimes(1);
  });
});
