import { useEffect, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";

const BACKEND_HEALTH_URL = "http://127.0.0.1:8000/health";
const HEALTH_POLL_INTERVAL_MS = 250;
const HEALTH_TIMEOUT_MS = 120_000;

/**
 * Wait until the Python FastAPI sidecar responds on /health, then reveal the window.
 * Prevents white-flash: tauri.conf.json starts with visible: false.
 */
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
  const [status, setStatus] = useState<string>("Łączenie z silnikiem AI (sidecar)…");
  const [backendOk, setBackendOk] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function bootstrap(): Promise<void> {
      try {
        await waitForBackendReady();
        if (cancelled) {
          return;
        }
        setBackendOk(true);
        setStatus("Backend gotowy. Ładowanie interfejsu…");
        await getCurrentWindow().show();
        setStatus("Photo Organizer — offline");
      } catch (bootError) {
        if (cancelled) {
          return;
        }
        const message =
          bootError instanceof Error ? bootError.message : String(bootError);
        setError(message);
        setStatus("Błąd uruchomienia backendu");
        await getCurrentWindow().show();
      }
    }

    void bootstrap();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-6 p-8">
      <header className="text-center">
        <h1 className="text-3xl font-semibold tracking-tight text-white">
          Photo Organizer
        </h1>
        <p className="mt-2 text-sm text-slate-400">100% offline · Tauri 2 + Python sidecar</p>
      </header>

      <main className="w-full max-w-lg rounded-xl border border-slate-800 bg-slate-900/80 p-6 shadow-xl">
        <p
          className={`text-center text-sm ${
            error ? "text-red-400" : backendOk ? "text-emerald-400" : "text-amber-300"
          }`}
        >
          {error ?? status}
        </p>
        {backendOk && (
          <p className="mt-4 text-center text-xs text-slate-500">
            API: http://127.0.0.1:8000 · Dokumentacja: /docs
          </p>
        )}
      </main>
    </div>
  );
}
