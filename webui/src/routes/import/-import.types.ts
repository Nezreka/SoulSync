import { z } from 'zod';

export const IMPORT_AUTO_FILTER_VALUES = ['all', 'pending', 'imported', 'failed'] as const;
export type ImportAutoFilter = (typeof IMPORT_AUTO_FILTER_VALUES)[number];

export const importAutoSearchSchema = z.object({
  autoFilter: z.enum(IMPORT_AUTO_FILTER_VALUES).default('all').catch('all'),
});

export type ImportAutoSearch = z.infer<typeof importAutoSearchSchema>;

export interface ImportStagingFile {
  filename: string;
  rel_path?: string;
  full_path: string;
  title?: string | null;
  artist?: string | null;
  album?: string | null;
  track_number?: string | number | null;
  disc_number?: string | number | null;
  extension?: string | null;
  size?: number | null;
  manual_match?: ImportTrackResult;
}

export interface ImportStagingFilesPayload {
  success: boolean;
  files?: ImportStagingFile[];
  staging_path?: string;
  error?: string;
}

export interface ImportStagingGroup {
  album: string;
  artist: string;
  file_count: number;
  files?: Array<{
    filename: string;
    full_path: string;
    title?: string | null;
    track_number?: string | number | null;
  }>;
  file_paths: string[];
}

export interface ImportStagingGroupsPayload {
  success: boolean;
  groups?: ImportStagingGroup[];
  error?: string;
}

export interface ImportAlbumResult {
  id: string;
  name: string;
  artist: string;
  source?: string | null;
  image_url?: string | null;
  total_tracks?: number | null;
  release_date?: string | null;
}

export interface ImportAlbumSearchPayload {
  success: boolean;
  albums?: ImportAlbumResult[];
  suggestions?: ImportAlbumResult[];
  ready?: boolean;
  error?: string;
}

export interface ImportTrackResult {
  id: string;
  name: string;
  artist: string;
  album?: string | null;
  source?: string | null;
  image_url?: string | null;
  duration_ms?: number | null;
}

export interface ImportTrackSearchPayload {
  success: boolean;
  tracks?: ImportTrackResult[];
  error?: string;
}

export interface ImportAlbum {
  id?: string | number | null;
  name: string;
  artist: string;
  source?: string | null;
  image_url?: string | null;
  total_tracks?: number | null;
  release_date?: string | null;
}

export interface ImportAlbumTrack {
  id?: string | number | null;
  name?: string | null;
  title?: string | null;
  track_number?: string | number | null;
  trackNumber?: string | number | null;
  disc_number?: number | null;
}

export interface ImportAlbumMatch {
  track?: ImportAlbumTrack | null;
  spotify_track?: ImportAlbumTrack | null;
  staging_file?: ImportStagingFile | null;
  confidence: number;
}

export interface ImportAlbumMatchPayload {
  success: boolean;
  album?: ImportAlbum;
  matches?: ImportAlbumMatch[];
  error?: string;
}

export interface ImportProcessPayload {
  success: boolean;
  processed?: number;
  total?: number;
  errors?: string[];
  error?: string;
}

export interface ImportAutoImportActiveItem {
  folder_hash?: string | null;
  folder_name?: string | null;
  status?: string | null;
  track_index?: number | null;
  track_total?: number | null;
  track_name?: string | null;
}

export interface ImportAutoImportStatusPayload {
  success: boolean;
  running?: boolean;
  paused?: boolean;
  current_status?: string | null;
  last_scan_time?: string | null;
  active_imports?: ImportAutoImportActiveItem[];
  stats?: {
    scanned?: number;
    auto_processed?: number;
    pending_review?: number;
    failed?: number;
  };
  error?: string;
}

export interface ImportAutoImportSettingsPayload {
  success: boolean;
  enabled?: boolean;
  scan_interval?: number;
  confidence_threshold?: number;
  auto_process?: boolean;
  error?: string;
}

export interface ImportAutoImportMatchData {
  matched_count?: number;
  total_tracks?: number;
  matches?: Array<{
    track_name?: string | null;
    track?: { name?: string | null };
    file?: string | null;
    confidence?: number | null;
  }>;
}

export interface ImportAutoImportResult {
  id: number;
  status: string;
  folder_hash?: string | null;
  folder_name: string;
  album_name?: string | null;
  artist_name?: string | null;
  image_url?: string | null;
  confidence?: number | null;
  total_files?: number | null;
  identification_method?: string | null;
  match_data?: string | ImportAutoImportMatchData | null;
  error_message?: string | null;
  created_at?: string | null;
}

export interface ImportAutoImportResultsPayload {
  success: boolean;
  results?: ImportAutoImportResult[];
  error?: string;
}

export type ImportQueueStatus = 'running' | 'done' | 'error';
export type ImportQueueJobType = 'album' | 'singles';

export interface ImportQueueEntry {
  id: number;
  type: ImportQueueJobType;
  label: string;
  sublabel: string;
  imageUrl?: string | null;
  status: ImportQueueStatus;
  processed: number;
  total: number;
  errors: string[];
}

export interface ImportAlbumQueueJob {
  type: 'album';
  label: string;
  sublabel: string;
  imageUrl?: string | null;
  items: ImportAlbumMatch[];
  albumData: ImportAlbum;
}

export interface ImportSinglesQueueJob {
  type: 'singles';
  label: string;
  sublabel: string;
  imageUrl?: string | null;
  items: ImportStagingFile[];
}

export type ImportQueueJob = ImportAlbumQueueJob | ImportSinglesQueueJob;
