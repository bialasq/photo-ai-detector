import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import {
  ChevronDown,
  ImageOff,
  Loader2,
  UserPlus,
  VolumeX,
} from "lucide-react";
import * as api from "@/services/api";
import type { NoiseFaceItem, PersonSummaryItem } from "@/types/api";

const NOISE_EXIT_MS = 400;

function isNamedPerson(person: PersonSummaryItem): boolean {
  return person.name !== null && person.name.trim().length > 0;
}

function personLabel(person: PersonSummaryItem): string {
  return person.name !== null && person.name.trim().length > 0
    ? person.name
    : `Person #${person.id}`;
}

interface NoiseInspectorProps {
  namedPeople: PersonSummaryItem[];
  refreshSignal: number;
  onIdentified: () => void;
}

interface NoiseFaceAvatarProps {
  face: NoiseFaceItem;
  isExiting: boolean;
  isMenuOpen: boolean;
  isAssigning: boolean;
  onOpenMenu: () => void;
}

function NoiseFaceAvatar({
  face,
  isExiting,
  isMenuOpen,
  isAssigning,
  onOpenMenu,
}: NoiseFaceAvatarProps): JSX.Element {
  const [imageFailed, setImageFailed] = useState(false);
  const thumbnailUrl = api.resolveNoiseFaceThumbnailUrl(face, 96);

  useEffect(() => {
    setImageFailed(false);
  }, [face.face_id]);

  return (
    <button
      type="button"
      onClick={onOpenMenu}
      disabled={isAssigning || isExiting}
      aria-label={`Review noise face ${face.face_id}`}
      aria-expanded={isMenuOpen}
      className={`relative h-12 w-12 shrink-0 overflow-hidden rounded-full border-2 bg-slate-100 transition-[opacity,transform,box-shadow] duration-400 ease-out will-change-[opacity,transform] disabled:cursor-not-allowed ${
        isExiting
          ? "pointer-events-none scale-90 opacity-0"
          : "scale-100 opacity-100"
      } ${
        isMenuOpen
          ? "border-amber-400 ring-2 ring-amber-400/40"
          : "border-slate-200 hover:border-amber-300 hover:shadow-sm"
      }`}
    >
      {!imageFailed ? (
        <img
          src={thumbnailUrl}
          alt=""
          className="h-full w-full object-cover"
          onError={() => setImageFailed(true)}
        />
      ) : (
        <span className="flex h-full w-full items-center justify-center text-slate-400">
          <ImageOff className="h-4 w-4" aria-hidden="true" />
        </span>
      )}
      {isAssigning && (
        <span className="absolute inset-0 flex items-center justify-center bg-white/70">
          <Loader2 className="h-4 w-4 animate-spin text-amber-600" aria-hidden="true" />
        </span>
      )}
    </button>
  );
}

export function NoiseInspector({
  namedPeople,
  refreshSignal,
  onIdentified,
}: NoiseInspectorProps): JSX.Element {
  const [isExpanded, setIsExpanded] = useState<boolean>(true);
  const [noiseFaces, setNoiseFaces] = useState<NoiseFaceItem[]>([]);
  const [exitingFaceIds, setExitingFaceIds] = useState<number[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [openFaceId, setOpenFaceId] = useState<number | null>(null);
  const [creatingForFaceId, setCreatingForFaceId] = useState<number | null>(null);
  const [newProfileName, setNewProfileName] = useState<string>("");
  const [assigningFaceId, setAssigningFaceId] = useState<number | null>(null);

  const sectionRef = useRef<HTMLElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const profiles = namedPeople.filter(isNamedPerson);

  const loadNoiseFaces = useCallback(async (): Promise<void> => {
    setIsLoading(true);
    setError(null);

    try {
      const rows = await api.getNoiseFaces();
      setNoiseFaces(rows);
      setExitingFaceIds([]);
    } catch (loadError: unknown) {
      setError(api.getApiErrorMessage(loadError));
      setNoiseFaces([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadNoiseFaces();
  }, [loadNoiseFaces, refreshSignal]);

  useEffect(() => {
    if (openFaceId === null) {
      return;
    }

    const handlePointerDown = (event: MouseEvent): void => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (
        popoverRef.current?.contains(target) ||
        sectionRef.current?.querySelector(`[data-face-id="${openFaceId}"]`)?.contains(target)
      ) {
        return;
      }
      setOpenFaceId(null);
      setCreatingForFaceId(null);
      setNewProfileName("");
    };

    document.addEventListener("mousedown", handlePointerDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
    };
  }, [openFaceId]);

  const removeFaceAfterAnimation = useCallback(
    (faceId: number): void => {
      setExitingFaceIds((previous) =>
        previous.includes(faceId) ? previous : [...previous, faceId],
      );

      window.setTimeout(() => {
        setNoiseFaces((previous) =>
          previous.filter((face) => face.face_id !== faceId),
        );
        setExitingFaceIds((previous) =>
          previous.filter((id) => id !== faceId),
        );
        onIdentified();
      }, NOISE_EXIT_MS);
    },
    [onIdentified],
  );

  const handleAssignExisting = async (
    faceId: number,
    personId: number,
  ): Promise<void> => {
    setAssigningFaceId(faceId);
    setError(null);

    try {
      await api.identifyNoiseFaceExisting(faceId, personId);
      setOpenFaceId(null);
      setCreatingForFaceId(null);
      removeFaceAfterAnimation(faceId);
    } catch (assignError: unknown) {
      setError(api.getApiErrorMessage(assignError));
    } finally {
      setAssigningFaceId(null);
    }
  };

  const handleCreateNewProfile = async (faceId: number): Promise<void> => {
    const trimmed = newProfileName.trim();
    if (trimmed.length === 0) {
      return;
    }

    setAssigningFaceId(faceId);
    setError(null);

    try {
      await api.identifyNoiseFaceNew(faceId, trimmed);
      setOpenFaceId(null);
      setCreatingForFaceId(null);
      setNewProfileName("");
      removeFaceAfterAnimation(faceId);
    } catch (assignError: unknown) {
      setError(api.getApiErrorMessage(assignError));
    } finally {
      setAssigningFaceId(null);
    }
  };

  const handleNewNameKeyDown = (
    event: KeyboardEvent<HTMLInputElement>,
    faceId: number,
  ): void => {
    if (event.key === "Enter") {
      event.preventDefault();
      void handleCreateNewProfile(faceId);
    }
    if (event.key === "Escape") {
      setCreatingForFaceId(null);
      setNewProfileName("");
    }
  };

  const visibleFaces = [
    ...noiseFaces,
    ...exitingFaceIds
      .filter((id) => !noiseFaces.some((face) => face.face_id === id))
      .map(
        (faceId) =>
          ({
            face_id: faceId,
            photo_id: 0,
            thumbnail_url: api.getNoiseFaceThumbnailUrl(faceId, 96),
          }) satisfies NoiseFaceItem,
      ),
  ];

  const uniqueVisibleFaces = [
    ...new Map(visibleFaces.map((face) => [face.face_id, face])).values(),
  ];

  return (
    <section
      ref={sectionRef}
      className="border-t border-slate-200 pt-8"
      aria-label="Review unmapped faces"
    >
      <button
        type="button"
        onClick={() => setIsExpanded((previous) => !previous)}
        className="flex w-full items-center justify-between gap-3 rounded-xl border border-amber-100 bg-amber-50/60 px-4 py-3 text-left transition-colors hover:bg-amber-50"
      >
        <span className="flex items-center gap-2">
          <VolumeX className="h-5 w-5 text-amber-600" aria-hidden="true" />
          <span>
            <span className="block text-sm font-bold text-slate-900">
              Review Unmapped Faces (AI Noise)
            </span>
            <span className="mt-0.5 block text-xs text-slate-500">
              DBSCAN outliers not grouped into a cluster — assign manually
            </span>
          </span>
        </span>
        <ChevronDown
          className={`h-5 w-5 shrink-0 text-amber-700 transition-transform duration-200 ${
            isExpanded ? "rotate-180" : ""
          }`}
          aria-hidden="true"
        />
      </button>

      {isExpanded && (
        <div className="relative mt-4 min-h-[4rem] rounded-2xl border border-dashed border-amber-200/80 bg-white p-4">
          {error !== null && (
            <p className="mb-3 text-sm font-medium text-red-600">{error}</p>
          )}

          {isLoading ? (
            <div className="flex flex-wrap gap-3">
              {Array.from({ length: 6 }, (_, index) => (
                <div
                  key={`noise-skeleton-${index}`}
                  className="h-12 w-12 animate-pulse rounded-full bg-slate-200"
                />
              ))}
            </div>
          ) : uniqueVisibleFaces.length === 0 ? (
            <p className="py-4 text-center text-sm text-slate-500">
              No noise faces to review — DBSCAN did not leave unmapped detections.
            </p>
          ) : (
            <div className="flex flex-wrap gap-3">
              {uniqueVisibleFaces.map((face) => {
                const isExiting = exitingFaceIds.includes(face.face_id);
                const isMenuOpen = openFaceId === face.face_id;
                const isAssigning = assigningFaceId === face.face_id;

                return (
                  <div
                    key={`noise-face-${face.face_id}`}
                    data-face-id={face.face_id}
                    className="relative"
                  >
                    <NoiseFaceAvatar
                      face={face}
                      isExiting={isExiting}
                      isMenuOpen={isMenuOpen}
                      isAssigning={isAssigning}
                      onOpenMenu={() => {
                        if (isExiting || isAssigning) {
                          return;
                        }
                        setOpenFaceId((previous) =>
                          previous === face.face_id ? null : face.face_id,
                        );
                        setCreatingForFaceId(null);
                        setNewProfileName("");
                      }}
                    />

                    {isMenuOpen && !isExiting && (
                      <div
                        ref={popoverRef}
                        className="absolute left-0 top-full z-30 mt-2 w-56 rounded-xl border border-slate-200 bg-white p-2 shadow-lg shadow-slate-900/10"
                        role="menu"
                      >
                        <p className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                          Assign face #{face.face_id}
                        </p>

                        {profiles.length > 0 ? (
                          <ul className="max-h-40 overflow-y-auto py-1">
                            {profiles.map((person) => (
                              <li key={person.id}>
                                <button
                                  type="button"
                                  role="menuitem"
                                  disabled={isAssigning}
                                  onClick={() => {
                                    void handleAssignExisting(
                                      face.face_id,
                                      person.id,
                                    );
                                  }}
                                  className="w-full rounded-lg px-2 py-1.5 text-left text-sm font-medium text-slate-700 transition-colors hover:bg-sky-50 hover:text-sky-900 disabled:opacity-50"
                                >
                                  {personLabel(person)}
                                </button>
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="px-2 py-1 text-xs text-slate-500">
                            No named profiles yet
                          </p>
                        )}

                        <div className="my-1 border-t border-slate-100" />

                        {creatingForFaceId === face.face_id ? (
                          <div className="space-y-2 px-1 py-1">
                            <input
                              type="text"
                              value={newProfileName}
                              onChange={(event) =>
                                setNewProfileName(event.target.value)
                              }
                              onKeyDown={(event) =>
                                handleNewNameKeyDown(event, face.face_id)
                              }
                              placeholder="New profile name…"
                              autoFocus
                              disabled={isAssigning}
                              className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-500/30"
                            />
                            <button
                              type="button"
                              disabled={
                                isAssigning || newProfileName.trim().length === 0
                              }
                              onClick={() => {
                                void handleCreateNewProfile(face.face_id);
                              }}
                              className="w-full rounded-lg bg-sky-600 px-2 py-1.5 text-xs font-semibold text-white hover:bg-sky-700 disabled:bg-slate-300"
                            >
                              Save profile
                            </button>
                          </div>
                        ) : (
                          <button
                            type="button"
                            role="menuitem"
                            disabled={isAssigning}
                            onClick={() => {
                              setCreatingForFaceId(face.face_id);
                              setNewProfileName("");
                            }}
                            className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm font-semibold text-sky-700 transition-colors hover:bg-sky-50 disabled:opacity-50"
                          >
                            <UserPlus className="h-4 w-4" aria-hidden="true" />
                            Create New Profile
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
