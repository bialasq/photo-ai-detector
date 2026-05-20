import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
} from "react";
import { CheckCircle, ImageOff, Loader2, RefreshCw, User } from "lucide-react";
import * as api from "@/services/api";
import type { IdentifyClusterResponse, PersonSummaryItem } from "@/types/api";

export type PeopleGridMode = "manage" | "filter";

/** Fade-out duration for unnamed cluster cards before moving to Named Profiles. */
const CLUSTER_EXIT_MS = 400;

type UnnamedClusterId = number;

type ClusterNamesInput = Record<UnnamedClusterId, string>;

type InputFocusState = "idle" | "focused" | "saving" | "saved" | "error";

const SKELETON_COUNT = 6;

/** Fixed card footprint — prevents grid reflow during opacity transitions. */
const PROFILE_CARD_MIN_H = "min-h-[11.5rem]";

const MANAGE_GRID_CLASS =
  "grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5";

function isNamedPerson(person: PersonSummaryItem): boolean {
  return person.name !== null && person.name.trim().length > 0;
}

function personDisplayName(person: PersonSummaryItem): string {
  if (isNamedPerson(person)) {
    return person.name as string;
  }
  return `Person #${person.id}`;
}

function upsertNamedPerson(
  previous: PersonSummaryItem[],
  response: IdentifyClusterResponse,
): PersonSummaryItem[] {
  const exists = previous.some((person) => person.id === response.person_id);
  if (exists) {
    return previous.map((person) =>
      person.id === response.person_id
        ? { ...person, name: response.name }
        : person,
    );
  }
  return [
    ...previous,
    {
      id: response.person_id,
      name: response.name,
      face_count: 0,
      exemplar_photo_path: null,
    },
  ];
}

interface PeopleGridProps {
  mode: PeopleGridMode;
  selectedPersonIds?: number[];
  onTogglePerson?: (personId: number) => void;
  enableNamedSelection?: boolean;
  selectionDisabled?: boolean;
  onDataChanged?: () => void;
  onPeopleUpdated?: (people: PersonSummaryItem[]) => void;
  className?: string;
}

interface UnnamedClusterCardProps {
  clusterId: UnnamedClusterId;
  nameValue: string;
  inputState: InputFocusState;
  errorMessage: string | null;
  isExiting: boolean;
  onNameChange: (clusterId: UnnamedClusterId, value: string) => void;
  onCommitName: (clusterId: UnnamedClusterId) => void;
}

function UnnamedClusterCard({
  clusterId,
  nameValue,
  inputState,
  errorMessage,
  isExiting,
  onNameChange,
  onCommitName,
}: UnnamedClusterCardProps): JSX.Element {
  const [imageLoadFailed, setImageLoadFailed] = useState<boolean>(false);
  const trimmedName = nameValue.trim();
  const thumbnailUrl = api.getClusterThumbnailUrl(clusterId, 128);

  useEffect(() => {
    setImageLoadFailed(false);
  }, [clusterId]);

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "Enter") {
      event.preventDefault();
      onCommitName(clusterId);
    }
  };

  const handleBlur = (_event: FocusEvent<HTMLInputElement>): void => {
    if (trimmedName.length === 0 || isExiting) {
      return;
    }
    onCommitName(clusterId);
  };

  const inputRingClass =
    inputState === "focused"
      ? "border-sky-400 ring-2 ring-sky-500/30"
      : inputState === "saving"
        ? "border-sky-300 ring-2 ring-sky-400/20"
        : inputState === "saved"
          ? "border-emerald-400 ring-2 ring-emerald-500/25"
          : inputState === "error"
            ? "border-red-400 ring-2 ring-red-500/25"
            : "border-slate-200";

  return (
    <div
      className={`h-full ${PROFILE_CARD_MIN_H} transition-[opacity,transform] duration-400 ease-out will-change-[opacity,transform] ${
        isExiting
          ? "pointer-events-none scale-[0.94] opacity-0"
          : "scale-100 opacity-100"
      }`}
    >
      <article className="flex h-full flex-col items-center gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="relative h-20 w-20 shrink-0 overflow-hidden rounded-full border-2 border-slate-100 bg-slate-50 shadow-inner">
          {!imageLoadFailed ? (
            <img
              src={thumbnailUrl}
              alt={`Unnamed cluster ${clusterId}`}
              className="h-full w-full object-cover"
              onError={() => setImageLoadFailed(true)}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-slate-400">
              <ImageOff className="h-6 w-6" aria-hidden="true" />
            </div>
          )}
        </div>

        <div className="w-full flex-1 space-y-1.5 text-center">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Cluster #{clusterId}
          </p>
          <label className="block w-full">
            <span className="sr-only">Name for cluster {clusterId}</span>
            <input
              type="text"
              value={nameValue}
              onChange={(event) => onNameChange(clusterId, event.target.value)}
              onKeyDown={handleKeyDown}
              onBlur={handleBlur}
              placeholder="Add a name…"
              disabled={inputState === "saving" || isExiting}
              autoComplete="off"
              className={`w-full rounded-lg border bg-slate-50 px-3 py-2 text-center text-sm text-slate-900 placeholder:text-slate-400 transition-[border-color,box-shadow] duration-200 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60 ${inputRingClass}`}
            />
          </label>
          {inputState === "saving" && (
            <p className="flex items-center justify-center gap-1 text-xs font-medium text-sky-600">
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
              Saving…
            </p>
          )}
          {inputState === "saved" && !isExiting && (
            <p className="text-xs font-medium text-emerald-600">Saved</p>
          )}
          {errorMessage !== null && (
            <p className="text-xs font-medium text-red-600">{errorMessage}</p>
          )}
        </div>
      </article>
    </div>
  );
}

interface NamedPersonFilterChipProps {
  person: PersonSummaryItem;
  isSelected: boolean;
  disabled: boolean;
  onToggle: (personId: number) => void;
}

function NamedPersonFilterChip({
  person,
  isSelected,
  disabled,
  onToggle,
}: NamedPersonFilterChipProps): JSX.Element {
  const [imageLoadFailed, setImageLoadFailed] = useState<boolean>(false);
  const label = personDisplayName(person);
  const thumbnailUrl = api.getPersonThumbnailUrl(person.id, 96);

  useEffect(() => {
    setImageLoadFailed(false);
  }, [person.id]);

  return (
    <button
      type="button"
      onClick={() => onToggle(person.id)}
      disabled={disabled}
      aria-pressed={isSelected}
      aria-label={`${isSelected ? "Remove filter" : "Filter by"} ${label}`}
      className={`group flex flex-col items-center gap-2 rounded-xl p-2 transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-50 ${
        isSelected ? "bg-sky-50" : "hover:bg-slate-50"
      }`}
    >
      <span
        className={`relative flex h-14 w-14 items-center justify-center overflow-hidden rounded-full border-2 transition-colors duration-200 ${
          isSelected
            ? "border-sky-500 ring-2 ring-sky-500/30"
            : "border-slate-200 group-hover:border-sky-300"
        }`}
      >
        {!imageLoadFailed ? (
          <img
            src={thumbnailUrl}
            alt=""
            className="h-full w-full object-cover"
            onError={() => setImageLoadFailed(true)}
          />
        ) : (
          <User className="h-6 w-6 text-slate-400" aria-hidden="true" />
        )}
      </span>
      <span
        className={`max-w-[5.5rem] truncate text-xs font-semibold ${
          isSelected ? "text-sky-800" : "text-slate-600"
        }`}
        title={label}
      >
        {label}
      </span>
    </button>
  );
}

interface NamedPersonManageCardProps {
  person: PersonSummaryItem;
  isSelected?: boolean;
  isEntering?: boolean;
  selectionDisabled?: boolean;
  onToggleSelect?: (personId: number) => void;
}

function NamedPersonManageCard({
  person,
  isSelected = false,
  isEntering = false,
  selectionDisabled = false,
  onToggleSelect,
}: NamedPersonManageCardProps): JSX.Element {
  const [imageLoadFailed, setImageLoadFailed] = useState<boolean>(false);
  const label = personDisplayName(person);
  const faceLabel =
    person.face_count === 1 ? "1 face" : `${person.face_count} faces`;
  const thumbnailUrl = api.getPersonThumbnailUrl(person.id, 128);

  useEffect(() => {
    setImageLoadFailed(false);
  }, [person.id]);

  const selectable = onToggleSelect !== undefined;

  const handleCardClick = (): void => {
    if (!selectable || selectionDisabled) {
      return;
    }
    onToggleSelect(person.id);
  };

  return (
    <div
      className={`h-full ${PROFILE_CARD_MIN_H} ${
        isEntering ? "animate-profile-enter" : ""
      }`}
    >
      <article
        role={selectable ? "button" : undefined}
        tabIndex={selectable && !selectionDisabled ? 0 : undefined}
        onClick={handleCardClick}
        onKeyDown={(event) => {
          if (!selectable || selectionDisabled) {
            return;
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggleSelect(person.id);
          }
        }}
        className={`flex h-full flex-col items-center gap-3 rounded-2xl border bg-white p-4 shadow-sm transition-[box-shadow,ring-color] duration-200 ${
          selectable && !selectionDisabled ? "cursor-pointer hover:shadow-md" : ""
        } ${
          isSelected
            ? "border-transparent ring-2 ring-sky-500"
            : "border-slate-200"
        }`}
      >
        <div className="relative h-20 w-20 shrink-0 overflow-hidden rounded-full border-2 border-emerald-100 bg-slate-50">
          {!imageLoadFailed ? (
            <img
              src={thumbnailUrl}
              alt={`${label} avatar`}
              className="h-full w-full object-cover"
              onError={() => setImageLoadFailed(true)}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-slate-400">
              <User className="h-6 w-6" aria-hidden="true" />
            </div>
          )}
        </div>
        <div className="text-center">
          <p className="text-sm font-semibold text-slate-900">{label}</p>
          <p className="mt-0.5 text-xs text-slate-500">{faceLabel}</p>
        </div>
      </article>
    </div>
  );
}

function PeopleGridSkeleton({ compact }: { compact: boolean }): JSX.Element {
  const gridClass = compact
    ? "grid grid-cols-3 gap-3"
    : MANAGE_GRID_CLASS;

  return (
    <ul className={`${gridClass} list-none p-0`}>
      {Array.from({ length: compact ? 3 : SKELETON_COUNT }, (_, index) => (
        <li
          key={`people-grid-skeleton-${index}`}
          className={PROFILE_CARD_MIN_H}
        >
          <div className="flex h-full flex-col items-center gap-2 rounded-2xl border border-slate-100 bg-white p-4">
            <div className="h-20 w-20 animate-pulse rounded-full bg-slate-200" />
            <div className="h-3 w-16 animate-pulse rounded bg-slate-200" />
          </div>
        </li>
      ))}
    </ul>
  );
}

function NewFacesEmptyState(): JSX.Element {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white px-6 py-10 text-center">
      <div className="rounded-full bg-emerald-50 p-3">
        <CheckCircle className="h-8 w-8 text-emerald-500" aria-hidden="true" />
      </div>
      <p className="mt-3 text-sm font-medium text-slate-700">
        No new faces waiting — inbox is clear
      </p>
      <p className="mt-1 max-w-sm text-xs text-slate-500">
        Run a folder scan to discover more clusters, or check back after processing
        finishes.
      </p>
    </div>
  );
}

export function PeopleGrid({
  mode,
  selectedPersonIds = [],
  onTogglePerson,
  enableNamedSelection = false,
  selectionDisabled = false,
  onDataChanged,
  onPeopleUpdated,
  className = "",
}: PeopleGridProps): JSX.Element {
  const [people, setPeople] = useState<PersonSummaryItem[]>([]);
  const [unnamedClusterIds, setUnnamedClusterIds] = useState<UnnamedClusterId[]>([]);
  const [exitingClusterIds, setExitingClusterIds] = useState<UnnamedClusterId[]>([]);
  const [enteringPersonIds, setEnteringPersonIds] = useState<number[]>([]);
  const [namesInput, setNamesInput] = useState<ClusterNamesInput>({});
  const [inputStates, setInputStates] = useState<Record<UnnamedClusterId, InputFocusState>>(
    {},
  );
  const [inputErrors, setInputErrors] = useState<Record<UnnamedClusterId, string | null>>(
    {},
  );
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchGenerationRef = useRef<number>(0);
  const exitTimersRef = useRef<Map<UnnamedClusterId, ReturnType<typeof setTimeout>>>(
    new Map(),
  );

  const namedPeople = people.filter(isNamedPerson);

  const notifyPeopleUpdated = useCallback(
    (nextPeople: PersonSummaryItem[]) => {
      onPeopleUpdated?.(nextPeople);
    },
    [onPeopleUpdated],
  );

  const loadData = useCallback(async (): Promise<void> => {
    const generation = fetchGenerationRef.current + 1;
    fetchGenerationRef.current = generation;

    setIsLoading(true);
    setError(null);

    try {
      const peoplePromise = api.getPeople();
      const clustersPromise =
        mode === "manage" ? api.getUnnamedClusters() : Promise.resolve([] as number[]);

      const [peopleRows, clusterIds] = await Promise.all([
        peoplePromise,
        clustersPromise,
      ]);

      if (fetchGenerationRef.current !== generation) {
        return;
      }

      setPeople(peopleRows);
      notifyPeopleUpdated(peopleRows);
      setUnnamedClusterIds(clusterIds);
      setExitingClusterIds([]);
      setEnteringPersonIds([]);

      const initialNames: ClusterNamesInput = {};
      const initialStates: Record<UnnamedClusterId, InputFocusState> = {};
      const initialErrors: Record<UnnamedClusterId, string | null> = {};

      for (const clusterId of clusterIds) {
        initialNames[clusterId] = "";
        initialStates[clusterId] = "idle";
        initialErrors[clusterId] = null;
      }

      setNamesInput(initialNames);
      setInputStates(initialStates);
      setInputErrors(initialErrors);
      setIsLoading(false);
    } catch (fetchError: unknown) {
      if (fetchGenerationRef.current !== generation) {
        return;
      }

      setError(api.getApiErrorMessage(fetchError));
      setPeople([]);
      setUnnamedClusterIds([]);
      setIsLoading(false);
    }
  }, [mode, notifyPeopleUpdated]);

  useEffect(() => {
    void loadData();

    return () => {
      fetchGenerationRef.current += 1;
      for (const timer of exitTimersRef.current.values()) {
        clearTimeout(timer);
      }
      exitTimersRef.current.clear();
    };
  }, [loadData]);

  const handleNameChange = (clusterId: UnnamedClusterId, value: string): void => {
    setNamesInput((previous) => ({
      ...previous,
      [clusterId]: value,
    }));
    setInputStates((previous) => ({
      ...previous,
      [clusterId]: "focused",
    }));
    setInputErrors((previous) => ({
      ...previous,
      [clusterId]: null,
    }));
  };

  const finalizeClusterPromotion = useCallback(
    async (clusterId: UnnamedClusterId, response: IdentifyClusterResponse) => {
      setUnnamedClusterIds((previous) =>
        previous.filter((id) => id !== clusterId),
      );
      setExitingClusterIds((previous) =>
        previous.filter((id) => id !== clusterId),
      );
      setNamesInput((previous) => {
        const next = { ...previous };
        delete next[clusterId];
        return next;
      });
      setInputStates((previous) => {
        const next = { ...previous };
        delete next[clusterId];
        return next;
      });

      let nextPeople: PersonSummaryItem[] = [];
      setPeople((previous) => {
        nextPeople = upsertNamedPerson(previous, response);
        return nextPeople;
      });

      setEnteringPersonIds((previous) => [...previous, response.person_id]);

      try {
        const refreshed = await api.getPeople();
        const namedOnly = refreshed.filter(isNamedPerson);
        setPeople((previous) => {
          const merged = new Map<number, PersonSummaryItem>();
          for (const person of previous.filter(isNamedPerson)) {
            merged.set(person.id, person);
          }
          for (const person of namedOnly) {
            merged.set(person.id, person);
          }
          const result = [...merged.values()].sort((a, b) => a.id - b.id);
          nextPeople = result;
          return result;
        });
      } catch {
        // Keep optimistic row if refresh fails.
      }

      notifyPeopleUpdated(nextPeople);
      onDataChanged?.();

      window.setTimeout(() => {
        setEnteringPersonIds((previous) =>
          previous.filter((id) => id !== response.person_id),
        );
      }, CLUSTER_EXIT_MS);
    },
    [notifyPeopleUpdated, onDataChanged],
  );

  const handleCommitName = async (clusterId: UnnamedClusterId): Promise<void> => {
    const name = namesInput[clusterId]?.trim() ?? "";
    if (
      name.length === 0 ||
      inputStates[clusterId] === "saving" ||
      exitingClusterIds.includes(clusterId)
    ) {
      return;
    }

    setInputStates((previous) => ({
      ...previous,
      [clusterId]: "saving",
    }));
    setInputErrors((previous) => ({
      ...previous,
      [clusterId]: null,
    }));

    try {
      const response: IdentifyClusterResponse = await api.identifyCluster(
        clusterId,
        name,
      );

      setInputStates((previous) => ({
        ...previous,
        [clusterId]: "saved",
      }));
      setExitingClusterIds((previous) =>
        previous.includes(clusterId) ? previous : [...previous, clusterId],
      );

      const existingTimer = exitTimersRef.current.get(clusterId);
      if (existingTimer !== undefined) {
        clearTimeout(existingTimer);
      }

      const timer = window.setTimeout(() => {
        exitTimersRef.current.delete(clusterId);
        void finalizeClusterPromotion(clusterId, response);
      }, CLUSTER_EXIT_MS);

      exitTimersRef.current.set(clusterId, timer);
    } catch (identifyError: unknown) {
      setInputStates((previous) => ({
        ...previous,
        [clusterId]: "error",
      }));
      setInputErrors((previous) => ({
        ...previous,
        [clusterId]: api.getApiErrorMessage(identifyError),
      }));
    }
  };

  const compact = mode === "filter";
  const filterGridClass = "grid grid-cols-3 gap-2 sm:grid-cols-4";

  if (isLoading) {
    return (
      <div className={className}>
        <PeopleGridSkeleton compact={compact} />
      </div>
    );
  }

  if (error !== null) {
    return (
      <div className={`space-y-3 ${className}`}>
        <p className="text-sm font-medium text-red-600">{error}</p>
        <button
          type="button"
          onClick={() => void loadData()}
          className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-1.5 text-sm font-semibold text-red-700 hover:bg-red-50"
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          Retry
        </button>
      </div>
    );
  }

  if (mode === "filter") {
    if (namedPeople.length === 0) {
      return (
        <p className={`text-sm text-slate-500 ${className}`}>
          No named people yet. Name clusters in People Profiles.
        </p>
      );
    }

    return (
      <div className={`${filterGridClass} ${className}`}>
        {namedPeople.map((person) => (
          <NamedPersonFilterChip
            key={person.id}
            person={person}
            isSelected={selectedPersonIds.includes(person.id)}
            disabled={onTogglePerson === undefined}
            onToggle={(personId) => onTogglePerson?.(personId)}
          />
        ))}
      </div>
    );
  }

  const visibleClusterIds = [
    ...unnamedClusterIds,
    ...exitingClusterIds.filter((id) => !unnamedClusterIds.includes(id)),
  ];
  const uniqueClusterIds = [...new Set(visibleClusterIds)];

  return (
    <div className={`space-y-10 ${className}`}>
      <section className="space-y-4">
        <div>
          <h3 className="text-sm font-bold tracking-tight text-slate-900">
            New Faces Discovered
          </h3>
          <p className="mt-1 text-sm text-slate-500">
            Name each cluster to add it to your library. Saved faces move into
            Named Profiles below.
          </p>
        </div>

        {uniqueClusterIds.length === 0 ? (
          <NewFacesEmptyState />
        ) : (
          <ul className={`${MANAGE_GRID_CLASS} list-none p-0`}>
            {uniqueClusterIds.map((clusterId) => (
              <li key={`cluster-${clusterId}`} className={PROFILE_CARD_MIN_H}>
                <UnnamedClusterCard
                  clusterId={clusterId}
                  nameValue={namesInput[clusterId] ?? ""}
                  inputState={inputStates[clusterId] ?? "idle"}
                  errorMessage={inputErrors[clusterId] ?? null}
                  isExiting={exitingClusterIds.includes(clusterId)}
                  onNameChange={handleNameChange}
                  onCommitName={(id) => {
                    void handleCommitName(id);
                  }}
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-4 border-t border-slate-200 pt-8">
        <div>
          <h3 className="text-sm font-bold tracking-tight text-slate-900">
            Named Profiles
          </h3>
          <p className="mt-1 text-sm text-slate-500">
            Identified people in your library. Select multiple to merge duplicates.
          </p>
        </div>

        {namedPeople.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-slate-200 bg-white px-6 py-8 text-center text-sm text-slate-500">
            No named profiles yet. Identify a new face above to get started.
          </p>
        ) : (
          <ul className={`${MANAGE_GRID_CLASS} list-none p-0`}>
            {namedPeople.map((person) => (
              <li key={`person-${person.id}`} className={PROFILE_CARD_MIN_H}>
                <NamedPersonManageCard
                  person={person}
                  isSelected={selectedPersonIds.includes(person.id)}
                  isEntering={enteringPersonIds.includes(person.id)}
                  selectionDisabled={selectionDisabled}
                  onToggleSelect={
                    enableNamedSelection ? onTogglePerson : undefined
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
