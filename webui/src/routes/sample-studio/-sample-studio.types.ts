/**
 * Sample Studio — shared types.
 *
 * Track rows come from GET /api/library/tracks and
 * GET /api/library/tracks/recent (serialize_track shape), normalized by
 * toStudioTrack in -sample-studio.api.ts. NOTE: `duration` is SECONDS here —
 * serialize_track ships milliseconds (the tracks.duration DB unit) and the
 * mapper converts. Analysis/peaks come from the Phase 1 api/sample.py
 * endpoints.
 */

/**
 * A native catalogue track ID, normalized to text by the API mapper.
 * Media-server identifiers remain in Library-v2 mappings.
 */
export type TrackId = string;

export interface StudioTrack {
  id: TrackId;
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

/**
 * Why the analysis request itself failed. The server's own words when it
 * answered (readJson puts them on the error), the connection only when it
 * didn't. This used to blame the connection for every failure, which hid a
 * 400 on every jellyfin and navidrome track.
 */
export function analysisLoadError(error: unknown): string {
  const offline =
    'Could not reach SoulSync to load the analysis. Check your connection and try again.';
  if (!(error instanceof Error)) return offline;
  if (error instanceof TypeError || error.name === 'TimeoutError') return offline;
  return error.message.trim() || offline;
}

export interface SampleAnalysis {
  track_id: TrackId;
  status: AnalysisStatus;
  bpm: number | null;
  onsets: number[];
  duration_s: number | null;
  /** Detected musical key (analyzer v3) — null when unknowable (silence/DC). */
  key: SampleKey | null;
}

/** Detected key: {"name": "C minor", "confidence": 0.82}. The UI shows
 *  "key uncertain" below 0.5 confidence and hides the key when null. */
export interface SampleKey {
  name: string;
  confidence: number;
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

/** Beat-synced delay note values the backend accepts. */
export type DelayTime = '1/4' | '1/8' | '1/2';

export interface DelayParams {
  time: DelayTime;
  feedback: number;
  mix: number;
}

/**
 * Render-funnel FX recipe. Sent identically on preview and save — the
 * backend funnels both through the same manipulations, and the stash entry
 * echoes the recipe back so it fully describes its sound.
 */
export interface RenderFx {
  /** Peak normalize ("peak"), omitted when false. */
  normalize: boolean;
  /** Edge fade, ms — 0.5..1000, always on, default 5. */
  fadeMs: number;
  reverse: boolean;
  /** Reverb T60 seconds — 0.2..1.5, null = off. */
  space: number | null;
  /** Beat-synced delay — null = off. Needs a known BPM or the backend 409s. */
  delay: DelayParams | null;
}

export const DEFAULT_FX: RenderFx = {
  normalize: false,
  fadeMs: 5,
  reverse: false,
  space: null,
  delay: null,
};

/** Separation lifecycle: idle|queued|running|done, or `error: …` on failure. */
export type StemsStatus = string;

export interface StemsInfo {
  track_id: TrackId;
  status: StemsStatus;
  stems: StemName[];
  backend?: string;
  /** The method of the most recent separation request (backend is method-aware). */
  method?: string | null;
  /** Honest display labels for the stems (backend STEM_LABELS). */
  labels?: Record<string, string> | null;
  /** False when the server can't run separation — the UI must not offer it. */
  stems_available: boolean;
  /** 0..1 while a separation runs. */
  progress?: number | null;
}

export interface StashEntry {
  id: number;
  name: string;
  tags: string[];
  track_id: TrackId | null;
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
  /** Which stem the chop was cut from — null means the full mix. */
  stem?: StemName | null;
  /** Render-funnel recipe the backend echoes back (persisted on the row). */
  normalize?: 'peak' | null;
  fade_ms?: number;
  reverse?: boolean;
  space?: number | null;
  delay?: DelayParams | null;
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
