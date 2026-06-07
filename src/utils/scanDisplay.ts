import type { ScanPhase, ScanStatusResponse } from "@/types/api";

export interface ScanProgressDisplay {
  phase: ScanPhase;
  title: string;
  detail: string;
  percent: number;
  indeterminate: boolean;
  etaLabel: string | null;
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

export function formatScanEtaRemaining(
  etaSeconds: number | null | undefined,
): string | null {
  if (etaSeconds === null || etaSeconds === undefined || etaSeconds <= 0) {
    return null;
  }
  if (etaSeconds >= 3600) {
    return ">1 h remaining";
  }
  if (etaSeconds < 60) {
    return `~${Math.ceil(etaSeconds)} s remaining`;
  }
  return `~${Math.round(etaSeconds / 60)} min remaining`;
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
  if (status.phase === "cancelled" || status.cancelled) {
    if (!status.is_active) {
      return null;
    }
    return {
      phase: "cancelled",
      title: "Stopping scan…",
      detail: "Finishing the current batch before saving progress.",
      percent: computeScanProgressPercent(status),
      indeterminate: true,
      etaLabel: null,
    };
  }

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
      etaLabel: null,
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

  const etaLabel =
    status.phase === "scanning" &&
    status.eta_seconds !== null &&
    status.eta_seconds > 0
      ? formatScanEtaRemaining(status.eta_seconds)
      : null;

  return {
    phase: "scanning",
    title: "Scanning images",
    detail,
    percent: computeScanProgressPercent(status),
    indeterminate: total === 0,
    etaLabel,
  };
}
