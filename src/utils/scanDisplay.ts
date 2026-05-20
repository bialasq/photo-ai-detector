import type { ScanPhase, ScanStatusResponse } from "@/types/api";

export interface ScanProgressDisplay {
  phase: ScanPhase;
  title: string;
  detail: string;
  percent: number;
  indeterminate: boolean;
}

function basename(filePath: string): string {
  const normalized = filePath.replace(/\\/g, "/");
  const segments = normalized.split("/");
  const name = segments[segments.length - 1];
  return name.length > 0 ? name : filePath;
}

export function isScanPhaseActive(phase: ScanPhase): boolean {
  return phase === "scanning" || phase === "clustering";
}

export function computeScanProgressPercent(status: ScanStatusResponse): number {
  if (status.phase === "clustering") {
    return 100;
  }
  if (status.total > 0) {
    return Math.round((status.processed / status.total) * 100);
  }
  return 0;
}

export function deriveScanProgressDisplay(
  status: ScanStatusResponse,
): ScanProgressDisplay | null {
  if (!status.is_active && !isScanPhaseActive(status.phase)) {
    return null;
  }

  if (status.phase === "clustering") {
    return {
      phase: "clustering",
      title: "Running DBSCAN clustering",
      detail:
        status.total > 0
          ? `Grouping faces across ${status.total} indexed photo${status.total === 1 ? "" : "s"}…`
          : "Grouping detected faces into people clusters…",
      percent: 100,
      indeterminate: true,
    };
  }

  const total = status.total;
  const currentIndex =
    total > 0 ? Math.min(status.processed + 1, total) : status.processed;
  const fileLabel =
    status.current_file !== null && status.current_file.length > 0
      ? basename(status.current_file)
      : null;

  let detail = "Discovering photos in folder…";
  if (total > 0) {
    detail = `Processing photo ${currentIndex} of ${total}`;
    if (fileLabel !== null) {
      detail += `: ${fileLabel}`;
    }
  }

  return {
    phase: "scanning",
    title: "Scanning images",
    detail,
    percent: computeScanProgressPercent(status),
    indeterminate: total === 0,
  };
}
