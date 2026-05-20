import { FolderOpen, Image, Loader2, Users } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Gallery } from "@/components/Gallery";
import { PeopleIndex } from "@/components/PeopleIndex";
import { ScanProgressOverlay } from "@/components/ScanProgressOverlay";
import { useAppContext } from "@/context/AppContext";

type NavSection = "gallery" | "people";

interface NavItemConfig {
  id: NavSection;
  label: string;
  icon: typeof Image;
}

const NAV_ITEMS: NavItemConfig[] = [
  { id: "gallery", label: "Gallery", icon: Image },
  { id: "people", label: "People Profiles", icon: Users },
];

export interface MainLayoutProps {
  children?: ReactNode;
}

function SectionContent({ section }: { section: NavSection }): JSX.Element {
  if (section === "gallery") {
    return <Gallery />;
  }
  return <PeopleIndex />;
}

interface ScanFoldersButtonProps {
  disabled?: boolean;
  isLoading?: boolean;
  onClick: () => void;
}

function ScanFoldersButton({
  disabled = false,
  isLoading = false,
  onClick,
}: ScanFoldersButtonProps): JSX.Element {
  return (
    <div className="border-t border-slate-800 px-3 py-4">
      <p className="mb-2 px-1 text-xs font-semibold uppercase tracking-wider text-slate-500">
        Library
      </p>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled || isLoading}
        aria-label="Scan folders for photos"
        aria-busy={isLoading}
        className="group flex w-full items-center gap-3 rounded-xl border border-sky-500/35 bg-gradient-to-br from-sky-600 to-sky-500 px-3 py-3 text-left text-sm font-semibold text-white shadow-lg shadow-sky-950/40 transition-[transform,box-shadow] duration-200 hover:from-sky-500 hover:to-sky-400 hover:shadow-sky-950/50 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/15">
          {isLoading ? (
            <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
          ) : (
            <FolderOpen className="h-5 w-5" aria-hidden="true" />
          )}
        </span>
        <span className="flex flex-col">
          <span>{isLoading ? "Opening…" : "Scan Folders"}</span>
          <span className="text-[11px] font-normal text-sky-100/90">
            Index photos from disk
          </span>
        </span>
      </button>
    </div>
  );
}

export function MainLayout({ children }: MainLayoutProps): JSX.Element {
  const {
    isBackendAlive,
    isScanning,
    scanDisplay,
    scanActionError,
    isStartingScan,
    startFolderScan,
    simulateDevTestScan,
    clearScanActionError,
  } = useAppContext();
  const [activeSection, setActiveSection] = useState<NavSection>("gallery");

  const handleScanFoldersClick = (): void => {
    void startFolderScan();
  };

  return (
    <div className="flex h-screen w-full overflow-hidden bg-slate-950">
      <aside className="flex w-64 shrink-0 flex-col border-r border-slate-800 bg-slate-900">
        <div className="border-b border-slate-800 px-5 py-6">
          <p className="text-xs font-semibold uppercase tracking-widest text-slate-500">
            Navigation
          </p>
        </div>

        <nav className="flex flex-col gap-1 px-3 py-4">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const isActive = activeSection === item.id;

            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setActiveSection(item.id)}
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-slate-800 text-white"
                    : "text-slate-300 hover:bg-slate-800/60 hover:text-white"
                }`}
              >
                <Icon
                  className={`h-5 w-5 shrink-0 ${
                    isActive ? "text-sky-400" : "text-slate-400"
                  }`}
                  aria-hidden="true"
                />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="mt-auto flex flex-col">
          <ScanFoldersButton
            disabled={!isBackendAlive || isScanning}
            isLoading={isStartingScan}
            onClick={handleScanFoldersClick}
          />

          <div className="border-t border-slate-800/80 px-3 pb-4">
            <button
              type="button"
              disabled={!isBackendAlive || isScanning || isStartingScan}
              onClick={() => {
                void simulateDevTestScan();
              }}
              className="w-full px-1 py-1 text-left text-[11px] font-medium text-slate-500 underline-offset-2 transition-colors hover:text-sky-300 hover:underline disabled:cursor-not-allowed disabled:opacity-40"
            >
              Simulate Test Folder Scan
            </button>
          </div>

          {scanActionError !== null && !isScanning && (
            <div className="border-t border-slate-800 px-3 py-3">
              <p className="text-xs font-medium text-red-400">{scanActionError}</p>
              <button
                type="button"
                onClick={clearScanActionError}
                className="mt-1 text-[11px] font-semibold text-slate-400 underline hover:text-white"
              >
                Dismiss
              </button>
            </div>
          )}

          {isScanning && scanDisplay !== null && (
            <div className="border-t border-slate-800 px-4 py-3">
              <p className="truncate text-xs font-medium text-slate-400">
                {scanDisplay.title}
              </p>
            </div>
          )}
        </div>
      </aside>

      <div className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6 py-4 shadow-sm">
          <h1 className="text-lg font-semibold tracking-tight text-slate-900">
            Photo AI Organizer
          </h1>

          {isBackendAlive ? (
            <div className="flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full bg-emerald-500"
                aria-hidden="true"
              />
              <span className="text-sm font-medium text-emerald-800">
                Sidecar: Connected
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-full border border-red-200 bg-red-50 px-3 py-1.5">
              <span
                className="h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-red-500"
                aria-hidden="true"
              />
              <span className="text-sm font-medium text-red-800">
                Sidecar: Disconnected
              </span>
            </div>
          )}
        </header>

        <main className="flex-1 overflow-y-auto bg-slate-50 pb-28">
          {children ?? <SectionContent section={activeSection} />}
        </main>

        <ScanProgressOverlay />
      </div>
    </div>
  );
}
