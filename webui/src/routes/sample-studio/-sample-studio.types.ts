/**
 * Sample Studio — shared types.
 *
 * Track rows come from GET /api/library/tracks and
 * GET /api/library/recently-added (serialize_track shape).
 * Analysis/peaks come from the Phase 1 api/sample.py endpoints.
 */

export interface StudioTrack {
  id: number;
  title: string;
  artist_name?: string | null;
  album_title?: string | null;
  duration?: number | null;
  file_path?: string | null;
  bitrate?: number | null;
  bpm?: number | null;
}

/** Analysis lifecycle: done|queued|pending|analyzing, or `error: …` on failure. */
export type AnalysisStatus = string;

/** True when the analysis worker reported a failure (`error: …`). */
export function isAnalysisError(status: AnalysisStatus): boolean {
  return status.startsWith('error');
}

/** Human-readable tail of an `error: …` analysis status. */
export function analysisErrorMessage(status: AnalysisStatus): string {
  const tail = status.slice('error:'.length).trim();
  return tail || 'Analysis failed';
}

export interface SampleAnalysis {
  track_id: number;
  status: AnalysisStatus;
  bpm: number | null;
  onsets: number[];
  duration_s: number | null;
}

export interface SamplePeaks {
  buckets: number;
  duration_s: number;
  min: number[];
  max: number[];
}

export type QualityFilter = 'all' | 'hires' | 'lossless' | 'high' | 'other';
export type TempoFilter = 'all' | 'slow' | 'mid' | 'fast' | 'fastest';
export type LengthFilter = 'all' | 'short' | 'medium' | 'long';

export interface StudioFilters {
  quality: QualityFilter;
  tempo: TempoFilter;
  length: LengthFilter;
}

export const DEFAULT_FILTERS: StudioFilters = {
  quality: 'all',
  tempo: 'all',
  length: 'all',
};

/** One saved chop: the rendered file record + the lightweight bookmark. */
export type StashFormat = 'wav16' | 'wav24' | 'flac';

export const STASH_FORMAT_LABEL: Record<StashFormat, string> = {
  wav16: 'WAV 16-bit',
  wav24: 'WAV 24-bit',
  flac: 'FLAC 24-bit',
};

/** The four Demucs stems. Order matches the backend STEMS tuple. */
export type StemName = 'drums' | 'vocals' | 'bass' | 'other';

export const STEM_NAMES: StemName[] = ['drums', 'vocals', 'bass', 'other'];

export const STEM_LABEL: Record<StemName, string> = {
  drums: 'Drums',
  vocals: 'Vocals',
  bass: 'Bass',
  other: 'Other',
};

/** Separation lifecycle: idle|queued|running|done, or `error: …` on failure. */
export type StemsStatus = string;

export interface StemsInfo {
  track_id: number;
  status: StemsStatus;
  stems: StemName[];
  backend?: string;
}

export interface StashEntry {
  id: number;
  name: string;
  tags: string[];
  track_id: number;
  track_title: string;
  artist_name: string;
  start_s: number;
  end_s: number;
  pitch_st: number;
  target_bpm: number | null;
  format: StashFormat;
  file_path: string;
  created_at: number;
  duration_s?: number;
  engine?: string;
  /** Configured sample folder the chop was saved to (absolute path). */
  folder: string | null;
}

/** One transient slice of the in/out region, for the chop tray. */
export interface ChopSlice {
  index: number;
  start: number;
  end: number;
}

export interface PreviewResult {
  preview_id: string;
  engine: string;
  duration_s: number;
}
