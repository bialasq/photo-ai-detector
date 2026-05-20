import { Loader2, ScanLine, X } from "lucide-react";
import { useAppContext } from "@/context/AppContext";

export function ScanProgressOverlay(): JSX.Element | null {
  const { scanDisplay, scanStatus, scanActionError, clearScanActionError } =
    useAppContext();

  if (scanDisplay === null) {
    return null;
  }

  const barWidth = scanDisplay.indeterminate
    ? "100%"
    : `${Math.min(100, Math.max(0, scanDisplay.percent))}%`;

  return (
    <div
      className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center px-4 pb-6"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="pointer-events-auto w-full max-w-2xl overflow-hidden rounded-2xl border border-slate-700/80 bg-slate-900/95 shadow-2xl shadow-slate-950/50 backdrop-blur-md">
        <div className="flex items-start gap-4 px-5 py-4">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-sky-500/20 text-sky-400">
            {scanDisplay.indeterminate ? (
              <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
            ) : (
              <ScanLine className="h-6 w-6" aria-hidden="true" />
            )}
          </div>

          <div className="min-w-0 flex-1 space-y-2">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-bold text-white">{scanDisplay.title}</p>
                <p className="mt-0.5 truncate text-sm text-slate-400">
                  {scanDisplay.detail}
                </p>
              </div>
              {!scanDisplay.indeterminate && (
                <span className="shrink-0 text-sm font-semibold tabular-nums text-sky-300">
                  {scanDisplay.percent}%
                </span>
              )}
            </div>

            <div className="h-2 overflow-hidden rounded-full bg-slate-800">
              <div
                className={`h-full rounded-full bg-gradient-to-r from-sky-500 to-sky-400 transition-[width] duration-300 ease-out ${
                  scanDisplay.indeterminate ? "animate-pulse opacity-80" : ""
                }`}
                style={{ width: barWidth }}
              />
            </div>

            {scanStatus.last_error !== null && (
              <p className="text-xs text-amber-400/90">
                Last issue: {scanStatus.last_error}
              </p>
            )}
          </div>
        </div>

        {scanActionError !== null && (
          <div className="flex items-center justify-between gap-3 border-t border-slate-800 bg-red-950/40 px-5 py-2.5">
            <p className="text-xs font-medium text-red-300">{scanActionError}</p>
            <button
              type="button"
              onClick={clearScanActionError}
              className="shrink-0 rounded-md p-1 text-red-300 transition-colors hover:bg-red-900/50 hover:text-white"
              aria-label="Dismiss scan error"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
