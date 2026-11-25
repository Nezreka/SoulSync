/**
 * #2193: move a track you already have into an album's missing slot. The
 * file moves (never a copy), the old row goes, the tags are rewritten and the
 * file is filed into the album's folder. Backed by
 * /api/library/v2/tracks/<missing id>/move-into[/preview].
 */

import { apiClient, readJson } from '@/app/api-client';

import type { LibrarySong } from './-library-v2.songs';

export interface SlotMoveTrack {
  id: number;
  title: string | null;
  track_number: number | null;
  disc_number: number | null;
  album_id: number;
  album_title: string | null;
  album_type: string | null;
  artist_name: string | null;
}

export interface SlotMoveFile {
  file_id: number;
  path: string;
  format: string;
  bitrate?: number | null;
  sample_rate?: number | null;
  bit_depth?: number | null;
  size?: number | null;
}

export interface SlotMoveTagChange {
  field: string;
  from: unknown;
  to: unknown;
}

export interface SlotMovePreview {
  success: boolean;
  error?: string;
  source: SlotMoveTrack & { files: SlotMoveFile[] };
  target: SlotMoveTrack;
  tags: { changes: SlotMoveTagChange[]; error?: string | null };
  path: {
    current: string;
    new: string | null;
    unchanged?: boolean;
    collision?: boolean;
    reason?: string | null;
  };
  organize: boolean;
  source_album_left_empty: boolean;
}

export interface SlotMoveResult {
  success: boolean;
  error?: string;
  track_id: number;
  removed_track_id: number;
  moved_file_ids: number[];
  source_album: 'deleted' | 'forgotten' | null;
  tags: { written?: number; skipped?: number; failed?: number; errors?: unknown[] };
  organize: { queued?: boolean; reason?: string };
}

async function post<T extends { success?: boolean; error?: string }>(
  path: string,
  sourceTrackId: number,
  fallback: string,
): Promise<T> {
  const payload = await readJson<T>(
    apiClient.post(path, { json: { source_track_id: sourceTrackId } }),
  );
  if (!payload.success) throw new Error(payload.error || fallback);
  return payload;
}

export function previewSlotMove(
  missingTrackId: number,
  sourceTrackId: number,
): Promise<SlotMovePreview> {
  return post<SlotMovePreview>(
    `library/v2/tracks/${missingTrackId}/move-into/preview`,
    sourceTrackId,
    'Could not preview the move',
  );
}

export function applySlotMove(
  missingTrackId: number,
  sourceTrackId: number,
): Promise<SlotMoveResult> {
  return post<SlotMoveResult>(
    `library/v2/tracks/${missingTrackId}/move-into`,
    sourceTrackId,
    'Could not move the track',
  );
}

/** Songs in the library matching the search, with a file to move. */
export async function searchSlotMoveCandidates(
  query: string,
  missingTrackId: number,
): Promise<LibrarySong[]> {
  const payload = await readJson<{ success?: boolean; error?: string; songs?: LibrarySong[] }>(
    apiClient.get('library/v2/songs', { searchParams: { search: query, page: 1, limit: 25 } }),
  );
  if (payload.success === false) throw new Error(payload.error || 'Search failed');
  return (payload.songs ?? []).filter(
    (song) => Number(song.track_id) !== missingTrackId && Boolean(song.file_path),
  );
}

/** "FLAC · 24/96" / "MP3 · 320 kbps" for a candidate row. */
export function candidateQuality(song: LibrarySong): string {
  const parts: string[] = [];
  if (song.format) parts.push(song.format);
  if (song.bit_depth && song.sample_rate)
    parts.push(`${song.bit_depth}/${Math.round(song.sample_rate / 1000)}`);
  else if (song.bitrate) parts.push(`${song.bitrate} kbps`);
  return parts.join(' · ');
}

/** "m:ss" from milliseconds. */
export function formatClock(ms: number | null | undefined): string {
  if (!ms) return '';
  return `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, '0')}`;
}

/** A tag value for the preview: lists joined, blanks shown as a dash. */
export function tagValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((v) => tagValue(v)).join(', ') || '—';
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value as string | number | boolean);
}
