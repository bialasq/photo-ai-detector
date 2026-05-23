import type {
  DevSimulateScanRequest,
  GalleryQueryParams,
  IdentifyClusterRequest,
  IdentifyClusterResponse,
  MergePeopleResponse,
  NoiseFaceItem,
  PersonSummaryItem,
  ScanFolderResponse,
  ScanPhase,
  ScanStatusResponse,
  SearchResultItem,
  UnnamedClusterSummaryItem,
} from "@/types/api";

function normalizeScanStatus(raw: ScanStatusResponse): ScanStatusResponse {
  const phase: ScanPhase =
    raw.phase ??
    (raw.is_active
      ? raw.processed >= raw.total && raw.total > 0
        ? "clustering"
        : "scanning"
      : "idle");

  return {
    processed: raw.processed,
    total: raw.total,
    is_active: raw.is_active,
    phase,
    current_file: raw.current_file ?? null,
    last_error: raw.last_error ?? null,
  };
}

export const BASE_URL = "http://127.0.0.1:8000";

interface HealthCheckResponse {
  status: string;
}

interface FastApiValidationErrorItem {
  loc: (string | number)[];
  msg: string;
  type: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isHealthCheckResponse(value: unknown): value is HealthCheckResponse {
  return isRecord(value) && typeof value.status === "string";
}

function isFastApiValidationErrorItem(
  value: unknown,
): value is FastApiValidationErrorItem {
  return (
    isRecord(value) &&
    Array.isArray(value.loc) &&
    typeof value.msg === "string" &&
    typeof value.type === "string"
  );
}

function extractDetailMessage(body: unknown): string | null {
  if (!isRecord(body)) {
    return null;
  }

  const detail = body.detail;

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    const messages: string[] = [];
    for (const item of detail) {
      if (isFastApiValidationErrorItem(item)) {
        messages.push(item.msg);
      }
    }
    if (messages.length > 0) {
      return messages.join("; ");
    }
  }

  return null;
}

async function parseErrorMessage(response: Response): Promise<string> {
  const fallback = `Request failed with status ${response.status} ${response.statusText}`;

  try {
    const body: unknown = await response.json();
    const detail = extractDetailMessage(body);
    if (detail !== null) {
      return detail;
    }
  } catch {
    // Response body is not JSON or could not be read.
  }

  return fallback;
}

export function getApiErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  const detail = extractDetailMessage(error);
  if (detail !== null) {
    return detail;
  }

  if (typeof error === "string") {
    return error;
  }

  return "An unexpected error occurred.";
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);

  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new Error(message);
  }

  const body: unknown = await response.json();
  return body as T;
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${BASE_URL}/health`);

    if (!response.ok) {
      return false;
    }

    const body: unknown = await response.json();

    if (isHealthCheckResponse(body) && body.status === "ok") {
      return true;
    }

    return false;
  } catch {
    return false;
  }
}

export async function scanFolder(folderPath: string): Promise<ScanFolderResponse> {
  return requestJson<ScanFolderResponse>("/api/scan-folder", {
    method: "POST",
    body: JSON.stringify({ folder_path: folderPath }),
  });
}

export async function getScanStatus(): Promise<ScanStatusResponse> {
  const raw = await requestJson<ScanStatusResponse>("/api/scan-status");
  return normalizeScanStatus(raw);
}

export async function getUnnamedClusters(): Promise<UnnamedClusterSummaryItem[]> {
  return requestJson<UnnamedClusterSummaryItem[]>("/api/clusters/unnamed");
}

export async function getClusterPhotos(clusterId: number): Promise<SearchResultItem[]> {
  return requestJson<SearchResultItem[]>(`/api/clusters/${clusterId}/photos`);
}

/**
 * Assign a display name to an unnamed DBSCAN cluster.
 * FastAPI: POST /api/clusters/identify — body `{ cluster_id, name }`.
 * (There is no `/api/people/name` route; naming always goes through cluster identify.)
 */
export async function getNoiseFaces(): Promise<NoiseFaceItem[]> {
  return requestJson<NoiseFaceItem[]>("/api/clusters/noise");
}

export async function identifyCluster(
  clusterId: number,
  name: string,
): Promise<IdentifyClusterResponse> {
  return requestJson<IdentifyClusterResponse>("/api/clusters/identify", {
    method: "POST",
    body: JSON.stringify({
      cluster_id: clusterId,
      name,
    } satisfies IdentifyClusterRequest),
  });
}

export async function identifyNoiseFaceNew(
  faceId: number,
  name: string,
): Promise<IdentifyClusterResponse> {
  return requestJson<IdentifyClusterResponse>("/api/clusters/identify", {
    method: "POST",
    body: JSON.stringify({
      face_id: faceId,
      name,
    } satisfies IdentifyClusterRequest),
  });
}

export async function identifyNoiseFaceExisting(
  faceId: number,
  personId: number,
): Promise<IdentifyClusterResponse> {
  return requestJson<IdentifyClusterResponse>("/api/clusters/identify", {
    method: "POST",
    body: JSON.stringify({
      face_id: faceId,
      person_id: personId,
    } satisfies IdentifyClusterRequest),
  });
}

export function getNoiseFaceThumbnailUrl(
  faceId: number,
  width?: number,
): string {
  const thumbnailWidth = width ?? 96;
  return `${BASE_URL}/api/faces/${faceId}/thumbnail?width=${thumbnailWidth}`;
}

/** Prefer API-provided relative thumbnail_url when present. */
export function resolveNoiseFaceThumbnailUrl(
  item: NoiseFaceItem,
  width?: number,
): string {
  if (item.thumbnail_url.startsWith("http")) {
    return item.thumbnail_url;
  }
  const suffix = width !== undefined ? `?width=${width}` : "";
  const path = item.thumbnail_url.includes("?")
    ? item.thumbnail_url.replace(/\?.*$/, `?width=${width ?? 96}`)
    : `${item.thumbnail_url}${suffix}`;
  return `${BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export async function simulateDevScan(
  params?: DevSimulateScanRequest,
): Promise<ScanFolderResponse> {
  return requestJson<ScanFolderResponse>("/api/dev/simulate-scan", {
    method: "POST",
    body: JSON.stringify({
      reset_first: true,
      ...params,
    } satisfies DevSimulateScanRequest),
  });
}

export async function getPeople(): Promise<PersonSummaryItem[]> {
  return requestJson<PersonSummaryItem[]>("/api/people");
}

export interface MergePeopleParams {
  person_ids: number[];
}

export async function mergePeople(params: MergePeopleParams): Promise<void> {
  const { person_ids } = params;

  if (person_ids.length < 2) {
    throw new Error("Merge requires at least two person IDs");
  }

  const [targetPersonId, ...sourcePersonIds] = person_ids;

  for (const sourcePersonId of sourcePersonIds) {
    await requestJson<MergePeopleResponse>("/api/people/merge", {
      method: "POST",
      body: JSON.stringify({
        target_person_id: targetPersonId,
        source_person_id: sourcePersonId,
      }),
    });
  }
}

export async function searchPhotos(names: string[]): Promise<SearchResultItem[]> {
  const namesQuery = names.join(",");
  const path = `/api/search?names=${encodeURIComponent(namesQuery)}`;
  return requestJson<SearchResultItem[]>(path);
}

export async function fetchGalleryPhotos(
  params: GalleryQueryParams,
): Promise<SearchResultItem[]> {
  const personIdsQuery = params.person_ids.join(",");
  const searchParams = new URLSearchParams();
  searchParams.set("person_ids", personIdsQuery);
  searchParams.set("ai_status", params.ai_filter);
  const path = `/api/gallery?${searchParams.toString()}`;
  return requestJson<SearchResultItem[]>(path);
}

export function getThumbnailUrl(photoId: number, width?: number): string {
  const thumbnailWidth = width ?? 300;
  return `${BASE_URL}/api/photos/${photoId}/thumbnail?width=${thumbnailWidth}`;
}

export function getClusterThumbnailUrl(clusterId: number, width?: number): string {
  const thumbnailWidth = width ?? 300;
  return `${BASE_URL}/api/clusters/${clusterId}/thumbnail?width=${thumbnailWidth}`;
}

export function getFaceCropThumbnailUrl(faceId: number, width?: number): string {
  const thumbnailWidth = width ?? 128;
  return `${BASE_URL}/api/faces/${faceId}/thumbnail?crop=1&width=${thumbnailWidth}`;
}

export function resolvePersonThumbnailUrl(
  person: PersonSummaryItem,
  width?: number,
): string {
  if (person.exemplar_face_id !== null) {
    return getFaceCropThumbnailUrl(person.exemplar_face_id, width);
  }
  return getPersonThumbnailUrl(person.id, width);
}

/** Turn a relative API thumbnail path into a full URL for `<img src>`. */
export function resolveApiThumbnailUrl(path: string): string {
  if (path.startsWith("http")) {
    return path;
  }
  return `${BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export function getPersonThumbnailUrl(personId: number, width?: number): string {
  const thumbnailWidth = width ?? 300;
  return `${BASE_URL}/api/people/${personId}/thumbnail?width=${thumbnailWidth}`;
}

export function getPhotoUrl(photoId: number): string {
  return `${BASE_URL}/api/photos/${photoId}/file`;
}