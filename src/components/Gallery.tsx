import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent,
} from "react";
import { Image, ImageOff, RefreshCw, X } from "lucide-react";
import { PeopleGrid } from "@/components/PeopleGrid";
import { useAppContext } from "@/context/AppContext";
import * as api from "@/services/api";
import type {
  GalleryAiFilter,
  GalleryQueryParams,
  PersonSummaryItem,
  SearchResultItem,
} from "@/types/api";

const PHOTO_SKELETON_COUNT = 8;

const AI_FILTER_OPTIONS: ReadonlyArray<{
  value: GalleryAiFilter;
  label: string;
}> = [
  { value: "all", label: "All Photos" },
  { value: "processed", label: "AI Processed Only" },
  { value: "unprocessed", label: "Unprocessed" },
];

function trimDisplayPath(filePath: string): string {
  const normalized = filePath.replace(/\\/g, "/");
  const segments = normalized.split("/");
  const fileName = segments[segments.length - 1];
  return fileName.length > 0 ? fileName : filePath;
}

function hasActiveFilters(
  selectedPersonIds: number[],
  aiFilter: GalleryAiFilter,
): boolean {
  return selectedPersonIds.length > 0 || aiFilter !== "all";
}

function GalleryPageHeader(): JSX.Element {
  return (
    <header className="mb-2">
      <h2 className="flex items-center gap-2 text-2xl font-bold tracking-tight text-slate-900">
        <Image className="h-7 w-7 text-sky-600" aria-hidden="true" />
        Gallery
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        Browse photos with multi-person intersection filters and AI processing
        status.
      </p>
    </header>
  );
}

interface GalleryActiveFilterBadgeProps {
  selectedPeople: PersonSummaryItem[];
  photoCount: number;
  isLoadingPhotos: boolean;
  onClearPersonFilter: () => void;
}

function GalleryActiveFilterBadge({
  selectedPeople,
  photoCount,
  isLoadingPhotos,
  onClearPersonFilter,
}: GalleryActiveFilterBadgeProps): JSX.Element {
  const labels = selectedPeople.map((person) =>
    person.name !== null && person.name.trim().length > 0
      ? person.name
      : `Person #${person.id}`,
  );
  const labelText =
    labels.length === 1
      ? labels[0]
      : `${labels.length} people (${labels.join(" + ")})`;

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 transition-all duration-200">
      <p className="text-sm text-sky-900">
        <span className="font-semibold">Filtered by:</span> {labelText}
        {!isLoadingPhotos && (
          <span className="ml-2 text-sky-700">
            · {photoCount} photo{photoCount === 1 ? "" : "s"}
          </span>
        )}
      </p>
      <button
        type="button"
        onClick={onClearPersonFilter}
        className="inline-flex items-center gap-1.5 rounded-full border border-sky-300 bg-white px-3 py-1 text-xs font-bold uppercase tracking-wide text-sky-800 shadow-sm transition-colors hover:bg-sky-100"
      >
        <X className="h-3.5 w-3.5" aria-hidden="true" />
        Clear filter
      </button>
    </div>
  );
}

interface GallerySidebarProps {
  aiFilter: GalleryAiFilter;
  selectedPersonIds: number[];
  isLoadingPhotos: boolean;
  peopleGridRefreshKey: number;
  onAiFilterChange: (filter: GalleryAiFilter) => void;
  onTogglePerson: (personId: number) => void;
  onClearFilters: () => void;
}

function GallerySidebar({
  aiFilter,
  selectedPersonIds,
  isLoadingPhotos,
  peopleGridRefreshKey,
  onAiFilterChange,
  onTogglePerson,
  onClearFilters,
}: GallerySidebarProps): JSX.Element {
  const filtersActive = hasActiveFilters(selectedPersonIds, aiFilter);

  const handleRadioChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const value = event.target.value;
    if (value === "all" || value === "processed" || value === "unprocessed") {
      onAiFilterChange(value);
    }
  };

  return (
    <aside className="h-fit w-full shrink-0 space-y-6 rounded-2xl border border-slate-200 bg-white p-5 md:w-64">
      <div className="space-y-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          AI status
        </p>
        <fieldset className="space-y-2" disabled={isLoadingPhotos}>
          <legend className="sr-only">AI processing filter</legend>
          {AI_FILTER_OPTIONS.map((option) => (
            <label
              key={option.value}
              className="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-100 px-3 py-2 text-sm text-slate-700 transition-colors hover:bg-slate-50 has-[:checked]:border-sky-200 has-[:checked]:bg-sky-50"
            >
              <input
                type="radio"
                name="gallery-ai-filter"
                value={option.value}
                checked={aiFilter === option.value}
                onChange={handleRadioChange}
                className="h-4 w-4 border-slate-300 text-sky-600 focus:ring-sky-500"
              />
              <span className="font-medium">{option.label}</span>
            </label>
          ))}
        </fieldset>
      </div>

      <div className="space-y-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Filter by person
        </p>
        <div className="max-h-72 overflow-y-auto pr-1">
          <PeopleGrid
            key={peopleGridRefreshKey}
            mode="filter"
            selectedPersonIds={selectedPersonIds}
            onTogglePerson={onTogglePerson}
          />
        </div>
      </div>

      <button
        type="button"
        onClick={onClearFilters}
        disabled={!filtersActive || isLoadingPhotos}
        className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        Clear filters
      </button>
    </aside>
  );
}

interface GalleryErrorBannerProps {
  message: string;
  onRetryPhotos: () => void;
}

function GalleryErrorBanner({
  message,
  onRetryPhotos,
}: GalleryErrorBannerProps): JSX.Element {
  return (
    <div className="flex flex-col items-start justify-between gap-4 rounded-xl border border-red-200 bg-red-50 p-4 text-red-700 sm:flex-row sm:items-center">
      <p className="text-sm font-medium leading-relaxed">{message}</p>
      <button
        type="button"
        onClick={onRetryPhotos}
        className="inline-flex items-center gap-2 rounded-lg border border-red-300 bg-white px-3 py-1.5 text-sm font-semibold text-red-700 transition-colors hover:bg-red-50"
      >
        <RefreshCw className="h-4 w-4" aria-hidden="true" />
        Reload photos
      </button>
    </div>
  );
}

function PhotoGridSkeleton(): JSX.Element {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
      {Array.from({ length: PHOTO_SKELETON_COUNT }, (_, index) => (
        <div
          key={`photo-skeleton-${index}`}
          className="aspect-square animate-pulse rounded-xl bg-slate-100"
        />
      ))}
    </div>
  );
}

function GalleryEmptyState(): JSX.Element {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-slate-100 bg-white px-6 py-16 text-center">
      <div className="mb-4 rounded-full bg-slate-100 p-4">
        <ImageOff className="h-10 w-10 text-slate-400" aria-hidden="true" />
      </div>
      <h3 className="text-lg font-medium text-slate-900">
        No photos match the active filters
      </h3>
      <p className="mt-2 max-w-sm text-sm text-slate-500">
        Try loosening your search criteria. Clear person filters or switch the AI
        status to include more photos.
      </p>
    </div>
  );
}

interface PhotoCardProps {
  photo: SearchResultItem;
  onOpen: (photoId: number) => void;
}

function PhotoCard({ photo, onOpen }: PhotoCardProps): JSX.Element {
  const [imageLoadFailed, setImageLoadFailed] = useState<boolean>(false);
  const thumbnailUrl = api.getThumbnailUrl(photo.photo_id);
  const displayName = trimDisplayPath(photo.file_path);

  useEffect(() => {
    setImageLoadFailed(false);
  }, [photo.photo_id]);

  const handleOpen = (): void => {
    onOpen(photo.photo_id);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLElement>): void => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onOpen(photo.photo_id);
    }
  };

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={handleOpen}
      onKeyDown={handleKeyDown}
      aria-label={`Open photo ${photo.photo_id}: ${displayName}`}
      className="group relative aspect-square cursor-pointer overflow-hidden rounded-xl border border-slate-200 bg-slate-50 transition-all duration-200 hover:shadow-md"
    >
      {!imageLoadFailed ? (
        <img
          src={thumbnailUrl}
          alt={`Photo ${photo.photo_id}: ${displayName}`}
          className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
          onError={() => setImageLoadFailed(true)}
        />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-slate-400">
          <ImageOff className="h-8 w-8" aria-hidden="true" />
          <span className="px-2 text-center text-xs font-medium">
            Thumbnail unavailable
          </span>
        </div>
      )}

      <div className="absolute inset-0 flex items-end bg-gradient-to-t from-black/60 via-transparent to-transparent p-3 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
        <p className="w-full truncate text-xs text-white" title={photo.file_path}>
          {displayName}
        </p>
      </div>
    </article>
  );
}

interface GalleryMainContentProps {
  photos: SearchResultItem[];
  isLoadingPhotos: boolean;
  error: string | null;
  onOpenPhoto: (photoId: number) => void;
}

function GalleryMainContent({
  photos,
  isLoadingPhotos,
  error,
  onOpenPhoto,
}: GalleryMainContentProps): JSX.Element {
  const showEmpty =
    !isLoadingPhotos && error === null && photos.length === 0;

  const showGrid =
    !isLoadingPhotos && error === null && photos.length > 0;

  return (
    <section className="flex-1 space-y-4">
      <p className="text-sm font-semibold text-slate-500">
        {isLoadingPhotos
          ? "Loading photos…"
          : `Found ${photos.length} photo${photos.length === 1 ? "" : "s"}`}
      </p>

      {isLoadingPhotos && <PhotoGridSkeleton />}

      {showEmpty && <GalleryEmptyState />}

      {showGrid && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
          {photos.map((photo) => (
            <PhotoCard
              key={photo.photo_id}
              photo={photo}
              onOpen={onOpenPhoto}
            />
          ))}
        </div>
      )}
    </section>
  );
}

interface GalleryLightboxProps {
  photo: SearchResultItem;
  onClose: () => void;
}

function GalleryLightbox({ photo, onClose }: GalleryLightboxProps): JSX.Element {
  const [fullImageFailed, setFullImageFailed] = useState<boolean>(false);
  const fullImageUrl = api.getPhotoUrl(photo.photo_id);
  const displayName = trimDisplayPath(photo.file_path);

  useEffect(() => {
    setFullImageFailed(false);
  }, [photo.photo_id]);

  const handleBackdropClick = (event: MouseEvent<HTMLDivElement>): void => {
    if (event.target === event.currentTarget) {
      onClose();
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Photo viewer for ${displayName}`}
      className="fixed inset-0 z-50 flex animate-fade-in flex-col bg-slate-950/95 backdrop-blur-sm md:flex-row"
      onClick={handleBackdropClick}
    >
      <div className="relative flex h-full flex-1 items-center justify-center p-4">
        <button
          type="button"
          onClick={onClose}
          aria-label="Close photo viewer"
          className="absolute right-4 top-4 z-10 rounded-full bg-white/10 p-2.5 text-white outline-none transition-colors hover:bg-white/20"
        >
          <X className="h-5 w-5" aria-hidden="true" />
        </button>

        {!fullImageFailed ? (
          <img
            src={fullImageUrl}
            alt={`Full resolution: ${displayName}`}
            className="max-h-full max-w-full rounded-lg object-contain shadow-2xl"
            onError={() => setFullImageFailed(true)}
          />
        ) : (
          <div className="flex flex-col items-center gap-3 text-slate-400">
            <ImageOff className="h-12 w-12" aria-hidden="true" />
            <p className="text-sm font-medium">Full-resolution image unavailable</p>
          </div>
        )}
      </div>

      <aside className="flex h-full w-full shrink-0 flex-col justify-between border-t border-slate-800 bg-slate-900 p-6 text-slate-200 md:w-80 md:border-l md:border-t-0">
        <div>
          <h3 className="text-lg font-semibold text-white">Photo Details</h3>
          <p className="mt-2 text-sm text-slate-400">
            Photo ID: {photo.photo_id}
          </p>
          <p
            className="mt-4 select-all break-all rounded-xl border border-slate-800 bg-slate-950 p-3 font-mono text-xs text-slate-400"
            title={photo.file_path}
          >
            {photo.file_path}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="mt-6 w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-sm font-semibold text-slate-200 transition-colors hover:bg-slate-700"
        >
          Close viewer
        </button>
      </aside>
    </div>
  );
}

export function Gallery(): JSX.Element {
  const { selectedPersonIds, setSelectedPersonIds, dataRefreshToken } =
    useAppContext();
  const [photos, setPhotos] = useState<SearchResultItem[]>([]);
  const [peopleForLabels, setPeopleForLabels] = useState<PersonSummaryItem[]>([]);
  const [aiFilter, setAiFilter] = useState<GalleryAiFilter>("all");
  const [isLoadingPhotos, setIsLoadingPhotos] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [activePhotoId, setActivePhotoId] = useState<number | null>(null);
  const photosFetchGenerationRef = useRef<number>(0);
  const peopleLabelsFetchGenerationRef = useRef<number>(0);

  const activePhoto = photos.find(
    (photo) => photo.photo_id === activePhotoId,
  );

  const buildGalleryQuery = useCallback((): GalleryQueryParams => {
    return {
      person_ids: selectedPersonIds,
      ai_filter: aiFilter,
    };
  }, [selectedPersonIds, aiFilter]);

  const fetchPhotos = useCallback(async (): Promise<void> => {
    const generation = photosFetchGenerationRef.current + 1;
    photosFetchGenerationRef.current = generation;

    setIsLoadingPhotos(true);
    setError(null);

    const query = buildGalleryQuery();

    try {
      const photoRows = await api.fetchGalleryPhotos(query);

      if (photosFetchGenerationRef.current !== generation) {
        return;
      }

      setPhotos(photoRows);
      setIsLoadingPhotos(false);
    } catch (fetchError: unknown) {
      if (photosFetchGenerationRef.current !== generation) {
        return;
      }

      setError(api.getApiErrorMessage(fetchError));
      setPhotos([]);
      setIsLoadingPhotos(false);
    }
  }, [buildGalleryQuery]);

  const fetchPeopleForLabels = useCallback(async (): Promise<void> => {
    const generation = peopleLabelsFetchGenerationRef.current + 1;
    peopleLabelsFetchGenerationRef.current = generation;

    try {
      const peopleRows = await api.getPeople();

      if (peopleLabelsFetchGenerationRef.current !== generation) {
        return;
      }

      setPeopleForLabels(peopleRows);
    } catch {
      if (peopleLabelsFetchGenerationRef.current !== generation) {
        return;
      }
      setPeopleForLabels([]);
    }
  }, []);

  useEffect(() => {
    void fetchPeopleForLabels();

    return () => {
      peopleLabelsFetchGenerationRef.current += 1;
    };
  }, [fetchPeopleForLabels, dataRefreshToken]);

  useEffect(() => {
    void fetchPhotos();

    return () => {
      photosFetchGenerationRef.current += 1;
    };
  }, [fetchPhotos, dataRefreshToken]);

  useEffect(() => {
    if (activePhotoId === null) {
      return;
    }

    const handleEscapeKey = (event: globalThis.KeyboardEvent): void => {
      if (event.key === "Escape") {
        setActivePhotoId(null);
      }
    };

    window.addEventListener("keydown", handleEscapeKey);

    return () => {
      window.removeEventListener("keydown", handleEscapeKey);
    };
  }, [activePhotoId]);

  useEffect(() => {
    if (
      activePhotoId !== null &&
      !photos.some((photo) => photo.photo_id === activePhotoId)
    ) {
      setActivePhotoId(null);
    }
  }, [photos, activePhotoId]);

  useEffect(() => {
    if (activePhotoId === null) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [activePhotoId]);

  const handleRetryPhotos = (): void => {
    void fetchPhotos();
  };

  const handleTogglePersonFilter = (personId: number): void => {
    setSelectedPersonIds((previous) => {
      if (previous.includes(personId)) {
        return previous.filter((id) => id !== personId);
      }
      return [...previous, personId];
    });
  };

  const handleAiFilterChange = (filter: GalleryAiFilter): void => {
    setAiFilter(filter);
  };

  const handleClearPersonFilter = (): void => {
    setSelectedPersonIds([]);
  };

  const handleClearFilters = (): void => {
    setSelectedPersonIds([]);
    setAiFilter("all");
  };

  const selectedPeople = peopleForLabels.filter((person) =>
    selectedPersonIds.includes(person.id),
  );

  const personFilterActive = selectedPersonIds.length > 0;

  const handleOpenPhoto = (photoId: number): void => {
    setActivePhotoId(photoId);
  };

  const handleCloseLightbox = (): void => {
    setActivePhotoId(null);
  };

  return (
    <div className="mx-auto flex h-full max-w-7xl flex-col gap-6 p-6">
      <GalleryPageHeader />

      {error !== null && (
        <GalleryErrorBanner
          message={error}
          onRetryPhotos={handleRetryPhotos}
        />
      )}

      {personFilterActive && (
        <GalleryActiveFilterBadge
          selectedPeople={selectedPeople}
          photoCount={photos.length}
          isLoadingPhotos={isLoadingPhotos}
          onClearPersonFilter={handleClearPersonFilter}
        />
      )}

      <div className="flex h-full flex-col gap-6 md:flex-row">
        <GallerySidebar
          aiFilter={aiFilter}
          selectedPersonIds={selectedPersonIds}
          isLoadingPhotos={isLoadingPhotos}
          peopleGridRefreshKey={dataRefreshToken}
          onAiFilterChange={handleAiFilterChange}
          onTogglePerson={handleTogglePersonFilter}
          onClearFilters={handleClearFilters}
        />

        <GalleryMainContent
          photos={photos}
          isLoadingPhotos={isLoadingPhotos}
          error={error}
          onOpenPhoto={handleOpenPhoto}
        />
      </div>

      {activePhoto !== undefined && (
        <GalleryLightbox photo={activePhoto} onClose={handleCloseLightbox} />
      )}
    </div>
  );
}
