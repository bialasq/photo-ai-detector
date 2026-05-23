export interface BoundingBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface PersonSummaryItem {
  id: number;
  name: string | null;
  face_count: number;
  exemplar_photo_path: string | null;
  exemplar_face_id: number | null;
  bounding_box: BoundingBox | null;
}

export interface UnnamedClusterSummaryItem {
  cluster_id: number;
  exemplar_face_id: number;
  photo_id: number;
  bounding_box: BoundingBox;
  face_count: number;
  thumbnail_url: string;
}

export interface SearchResultItem {
  photo_id: number;
  file_path: string;
}

export type GalleryAiFilter = "all" | "processed" | "unprocessed" | "faceless";

export interface GalleryQueryParams {
  person_ids: number[];
  ai_filter: GalleryAiFilter;
}

export type ScanPhase = "idle" | "scanning" | "clustering";

export interface ScanStatusResponse {
  processed: number;
  total: number;
  is_active: boolean;
  phase: ScanPhase;
  current_file: string | null;
  last_error: string | null;
}

export interface ScanFolderResponse {
  status: string;
  total_files: number;
}

export interface ScanFolderRequest {
  folder_path: string;
}

export interface NoiseFaceItem {
  face_id: number;
  photo_id: number;
  bounding_box: BoundingBox;
  thumbnail_url: string;
}

export interface FacePreviewItem {
  face_id: number;
  photo_id: number;
  bounding_box: BoundingBox;
  thumbnail_url: string;
}

export interface IdentifyClusterResponse {
  status: string;
  person_id: number;
  name: string;
  cluster_id?: number;
  face_id?: number;
}

export interface IdentifyClusterRequest {
  cluster_id?: number;
  face_id?: number;
  name?: string;
  person_id?: number;
}

export interface DevSimulateScanRequest {
  folder_path?: string;
  reset_first?: boolean;
}

export interface DevResetLibraryResponse {
  status: string;
  removed: Record<string, number>;
}

export interface MergePeopleResponse {
  status: string;
  target_person_id: number;
  source_person_id: number;
  faces_moved: number;
}

export interface MergePeopleRequest {
  target_person_id: number;
  source_person_id: number;
}
