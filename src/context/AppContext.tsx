import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { open } from "@tauri-apps/plugin-dialog";
import * as api from "@/services/api";
import {
  computeScanProgressPercent,
  deriveScanProgressDisplay,
  isScanPhaseActive,
} from "@/utils/scanDisplay";
import type { ScanStatusResponse } from "@/types/api";
import type { ScanProgressDisplay } from "@/utils/scanDisplay";

const HEALTH_POLL_INTERVAL_MS = 5000;
const SCAN_POLL_INTERVAL_MS = 500;

const IDLE_SCAN_STATUS: ScanStatusResponse = {
  processed: 0,
  total: 0,
  is_active: false,
  phase: "idle",
  current_file: null,
  last_error: null,
};

function normalizeScanStatus(raw: ScanStatusResponse): ScanStatusResponse {
  const phase = raw.phase ?? (raw.is_active ? "scanning" : "idle");
  return {
    processed: raw.processed,
    total: raw.total,
    is_active: raw.is_active,
    phase,
    current_file: raw.current_file ?? null,
    last_error: raw.last_error ?? null,
  };
}

export interface AppContextValue {
  isBackendAlive: boolean;
  isScanning: boolean;
  scanProgress: number;
  scanStatus: ScanStatusResponse;
  scanDisplay: ScanProgressDisplay | null;
  scanActionError: string | null;
  isStartingScan: boolean;
  dataRefreshToken: number;
  selectedPersonIds: number[];
  setSelectedPersonIds: React.Dispatch<React.SetStateAction<number[]>>;
  startFolderScan: () => Promise<void>;
  simulateDevTestScan: () => Promise<void>;
  refreshAppData: () => void;
  clearScanActionError: () => void;
}

const AppContext = createContext<AppContextValue | undefined>(undefined);

export interface AppProviderProps {
  children: ReactNode;
}

export function AppProvider({ children }: AppProviderProps): JSX.Element {
  const [isBackendAlive, setIsBackendAlive] = useState<boolean>(false);
  const [scanStatus, setScanStatus] = useState<ScanStatusResponse>(IDLE_SCAN_STATUS);
  const [scanPollEnabled, setScanPollEnabled] = useState<boolean>(false);
  const [scanActionError, setScanActionError] = useState<string | null>(null);
  const [isStartingScan, setIsStartingScan] = useState<boolean>(false);
  const [dataRefreshToken, setDataRefreshToken] = useState<number>(0);
  const [selectedPersonIds, setSelectedPersonIds] = useState<number[]>([]);

  const wasActiveRef = useRef<boolean>(false);
  const pollInFlightRef = useRef<boolean>(false);

  const isScanning = scanStatus.is_active;
  const scanProgress = computeScanProgressPercent(scanStatus);
  const scanDisplay = deriveScanProgressDisplay(scanStatus);

  const applyScanStatus = useCallback((raw: ScanStatusResponse): void => {
    const next = normalizeScanStatus(raw);

    if (wasActiveRef.current && !next.is_active) {
      setDataRefreshToken((previous) => previous + 1);
    }

    wasActiveRef.current = next.is_active;
    setScanStatus(next);

    if (next.is_active || isScanPhaseActive(next.phase)) {
      setScanPollEnabled(true);
    }
  }, []);

  const pollScanStatusOnce = useCallback(async (): Promise<void> => {
    if (pollInFlightRef.current) {
      return;
    }

    pollInFlightRef.current = true;

    try {
      const response = await api.getScanStatus();
      applyScanStatus(response);

      const normalized = normalizeScanStatus(response);
      if (!normalized.is_active && !isScanPhaseActive(normalized.phase)) {
        setScanPollEnabled(false);
      }
    } catch (pollError: unknown) {
      setScanActionError(api.getApiErrorMessage(pollError));
      setScanPollEnabled(false);
      setScanStatus(IDLE_SCAN_STATUS);
      wasActiveRef.current = false;
    } finally {
      pollInFlightRef.current = false;
    }
  }, [applyScanStatus]);

  useEffect(() => {
    let cancelled = false;

    const runHealthCheck = async (): Promise<void> => {
      const alive = await api.checkHealth();
      if (!cancelled) {
        setIsBackendAlive(alive);
      }
    };

    void runHealthCheck();

    const healthIntervalId = window.setInterval(() => {
      void runHealthCheck();
    }, HEALTH_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(healthIntervalId);
    };
  }, []);

  useEffect(() => {
    if (!isBackendAlive) {
      setScanPollEnabled(false);
      return;
    }

    let cancelled = false;

    const bootstrapScanStatus = async (): Promise<void> => {
      try {
        const response = await api.getScanStatus();
        if (cancelled) {
          return;
        }
        applyScanStatus(response);
        const normalized = normalizeScanStatus(response);
        if (normalized.is_active || isScanPhaseActive(normalized.phase)) {
          setScanPollEnabled(true);
        }
      } catch {
        if (!cancelled) {
          setScanStatus(IDLE_SCAN_STATUS);
        }
      }
    };

    void bootstrapScanStatus();

    return () => {
      cancelled = true;
    };
  }, [isBackendAlive, applyScanStatus]);

  useEffect(() => {
    if (!isBackendAlive || !scanPollEnabled) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void pollScanStatusOnce();
    }, SCAN_POLL_INTERVAL_MS);

    void pollScanStatusOnce();

    return () => {
      window.clearInterval(intervalId);
    };
  }, [isBackendAlive, scanPollEnabled, pollScanStatusOnce]);

  const beginScanPolling = useCallback(async (): Promise<void> => {
    setScanPollEnabled(true);
    await pollScanStatusOnce();
  }, [pollScanStatusOnce]);

  const startFolderScan = useCallback(async (): Promise<void> => {
    if (!isBackendAlive || isStartingScan || isScanning) {
      return;
    }

    setScanActionError(null);
    setIsStartingScan(true);

    try {
      const selected = await open({
        directory: true,
        multiple: false,
      });

      if (selected === null) {
        return;
      }

      const folderPath = Array.isArray(selected) ? selected[0] : selected;

      if (typeof folderPath !== "string" || folderPath.trim().length === 0) {
        return;
      }

      await api.scanFolder(folderPath.trim());
      await beginScanPolling();
    } catch (scanError: unknown) {
      setScanActionError(api.getApiErrorMessage(scanError));
      setScanPollEnabled(false);
    } finally {
      setIsStartingScan(false);
    }
  }, [
    isBackendAlive,
    isStartingScan,
    isScanning,
    beginScanPolling,
  ]);

  const clearScanActionError = useCallback((): void => {
    setScanActionError(null);
  }, []);

  const refreshAppData = useCallback((): void => {
    setDataRefreshToken((previous) => previous + 1);
  }, []);

  const simulateDevTestScan = useCallback(async (): Promise<void> => {
    if (!isBackendAlive || isStartingScan || isScanning) {
      return;
    }

    setScanActionError(null);
    setIsStartingScan(true);

    try {
      await api.simulateDevScan({ reset_first: true });
      await beginScanPolling();
    } catch (scanError: unknown) {
      setScanActionError(api.getApiErrorMessage(scanError));
      setScanPollEnabled(false);
    } finally {
      setIsStartingScan(false);
    }
  }, [isBackendAlive, isStartingScan, isScanning, beginScanPolling]);

  const value = useMemo<AppContextValue>(
    () => ({
      isBackendAlive,
      isScanning,
      scanProgress,
      scanStatus,
      scanDisplay,
      scanActionError,
      isStartingScan,
      dataRefreshToken,
      selectedPersonIds,
      setSelectedPersonIds,
      startFolderScan,
      simulateDevTestScan,
      refreshAppData,
      clearScanActionError,
    }),
    [
      isBackendAlive,
      isScanning,
      scanProgress,
      scanStatus,
      scanDisplay,
      scanActionError,
      isStartingScan,
      dataRefreshToken,
      selectedPersonIds,
      startFolderScan,
      simulateDevTestScan,
      refreshAppData,
      clearScanActionError,
    ],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppContext(): AppContextValue {
  const context = useContext(AppContext);
  if (context === undefined) {
    throw new Error("useAppContext must be used within an AppProvider");
  }
  return context;
}
