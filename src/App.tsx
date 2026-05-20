import { useEffect, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { MainLayout } from "@/components/MainLayout";

const BACKEND_HEALTH_URL = "http://127.0.0.1:8000/health";
const HEALTH_POLL_INTERVAL_MS = 250;
const HEALTH_TIMEOUT_MS = 120_000;

async function waitForBackendReady(): Promise<void> {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS;

  while (Date.now() < deadline) {
    try {
      const response = await fetch(BACKEND_HEALTH_URL, {
        method: "GET",
        cache: "no-store",
      });
      if (response.ok) {
        return;
      }
    } catch {
      // Sidecar still booting — keep polling.
    }
    await new Promise((resolve) => setTimeout(resolve, HEALTH_POLL_INTERVAL_MS));
  }

  throw new Error(
    `Backend did not become ready within ${HEALTH_TIMEOUT_MS / 1000}s (${BACKEND_HEALTH_URL})`,
  );
}

export default function App(): JSX.Element {
  const [isBootstrapping, setIsBootstrapping] = useState<boolean>(true);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function bootstrap(): Promise<void> {
      try {
        await waitForBackendReady();
        if (cancelled) {
          return;
        }
        await getCurrentWindow().show();
        setIsBootstrapping(false);
      } catch (bootError) {
        if (cancelled) {
          return;
        }
        const message =
          bootError instanceof Error ? bootError.message : String(bootError);
        setBootstrapError(message);
        await getCurrentWindow().show();
        setIsBootstrapping(false);
      }
    }

    void bootstrap();

    return () => {
      cancelled = true;
    };
  }, []);

  if (isBootstrapping) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4 bg-slate-950 p-8">
        <p className="text-sm font-medium text-slate-300">
          Łączenie z silnikiem AI (sidecar)…
        </p>
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-600 border-t-sky-400" />
      </div>
    );
  }

  if (bootstrapError !== null) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4 bg-slate-950 p-8">
        <p className="text-center text-sm font-medium text-red-400">
          Błąd uruchomienia backendu
        </p>
        <p className="max-w-lg text-center text-xs text-slate-500">{bootstrapError}</p>
      </div>
    );
  }

  return <MainLayout />;
}
