import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, Users } from "lucide-react";
import { NoiseInspector } from "@/components/NoiseInspector";
import { PeopleGrid } from "@/components/PeopleGrid";
import { useAppContext } from "@/context/AppContext";
import * as api from "@/services/api";
import type { PersonSummaryItem } from "@/types/api";

function PeopleProfilesHeader({
  selectedCount,
  isMerging,
  onMerge,
  mergeEnabled,
}: {
  selectedCount: number;
  isMerging: boolean;
  onMerge: () => void;
  mergeEnabled: boolean;
}): JSX.Element {
  return (
    <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h2 className="flex items-center gap-2 text-2xl font-bold tracking-tight text-slate-900">
          <Users className="h-7 w-7 text-sky-600" aria-hidden="true" />
          People Profiles
        </h2>
        <p className="mt-1 max-w-2xl text-sm text-slate-500">
          One place to name newly discovered faces and manage your indexed people.
          Named profiles appear in the gallery filter automatically.
        </p>
      </div>
      <button
        type="button"
        onClick={onMerge}
        disabled={!mergeEnabled}
        className={`inline-flex shrink-0 items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold shadow-sm transition-colors ${
          mergeEnabled
            ? "bg-sky-600 text-white hover:bg-sky-700"
            : "cursor-not-allowed bg-slate-200 text-slate-500"
        }`}
      >
        {isMerging ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Merging…
          </>
        ) : (
          <>Merge selected ({selectedCount})</>
        )}
      </button>
    </header>
  );
}

/** Unified People Profiles screen (formerly Identity Inbox + People Index). */
export function PeopleIndex(): JSX.Element {
  const { dataRefreshToken, refreshAppData } = useAppContext();
  const [peopleSnapshot, setPeopleSnapshot] = useState<PersonSummaryItem[]>([]);
  const [selectedPersonIds, setSelectedPersonIds] = useState<number[]>([]);
  const [isMerging, setIsMerging] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [mergeRefreshKey, setMergeRefreshKey] = useState<number>(0);
  const [profilesDataKey, setProfilesDataKey] = useState<number>(0);

  useEffect(() => {
    setProfilesDataKey(dataRefreshToken);
  }, [dataRefreshToken]);

  const handlePeopleUpdated = useCallback((people: PersonSummaryItem[]) => {
    setPeopleSnapshot(
      people.filter(
        (person) => person.name !== null && person.name.trim().length > 0,
      ),
    );
  }, []);

  const togglePersonSelection = (personId: number): void => {
    if (isMerging) {
      return;
    }

    setSelectedPersonIds((previous) => {
      if (previous.includes(personId)) {
        return previous.filter((id) => id !== personId);
      }
      return [...previous, personId];
    });
  };

  const handleMergePeople = async (): Promise<void> => {
    if (selectedPersonIds.length < 2 || isMerging) {
      return;
    }

    const idsToMerge = [...selectedPersonIds];
    const survivorPersonId = idsToMerge[0];

    setIsMerging(true);
    setError(null);

    try {
      await api.mergePeople({ person_ids: idsToMerge });

      const selectedPeople = peopleSnapshot.filter((person) =>
        idsToMerge.includes(person.id),
      );
      const combinedFaceCount = selectedPeople.reduce(
        (sum, person) => sum + person.face_count,
        0,
      );
      const mergedAwayIds = new Set(
        idsToMerge.filter((id) => id !== survivorPersonId),
      );

      setPeopleSnapshot((previous) =>
        previous
          .filter((person) => !mergedAwayIds.has(person.id))
          .map((person) =>
            person.id === survivorPersonId
              ? { ...person, face_count: combinedFaceCount }
              : person,
          ),
      );
      setSelectedPersonIds([]);
      setMergeRefreshKey((previous) => previous + 1);
    } catch (mergeError: unknown) {
      setError(api.getApiErrorMessage(mergeError));
    } finally {
      setIsMerging(false);
    }
  };

  const mergeEnabled = selectedPersonIds.length >= 2 && !isMerging;

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6">
      <PeopleProfilesHeader
        selectedCount={selectedPersonIds.length}
        isMerging={isMerging}
        mergeEnabled={mergeEnabled}
        onMerge={() => {
          void handleMergePeople();
        }}
      />

      {error !== null && (
        <div className="flex items-center justify-between gap-4 rounded-xl border border-red-200 bg-red-50 p-4 text-red-700">
          <p className="text-sm font-medium">{error}</p>
          <button
            type="button"
            onClick={() => setError(null)}
            className="inline-flex items-center gap-2 rounded-lg border border-red-300 bg-white px-3 py-1.5 text-sm font-semibold hover:bg-red-50"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            Dismiss
          </button>
        </div>
      )}

      <PeopleGrid
        key={`profiles-${profilesDataKey}-merge-${mergeRefreshKey}`}
        mode="manage"
        enableNamedSelection
        selectedPersonIds={selectedPersonIds}
        onTogglePerson={togglePersonSelection}
        selectionDisabled={isMerging}
        onPeopleUpdated={handlePeopleUpdated}
        onDataChanged={refreshAppData}
      />

      <NoiseInspector
        namedPeople={peopleSnapshot}
        refreshSignal={dataRefreshToken}
        onIdentified={refreshAppData}
      />
    </div>
  );
}
