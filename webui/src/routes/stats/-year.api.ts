import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type { SoundtrackTrack } from './-year.soundtrack';
import type { YearInListening, YearInListeningPayload } from './-year.types';

import { STATS_QUERY_KEY } from './-stats.api';

export async function fetchYearInListening(): Promise<YearInListening> {
  const payload = await readJson<YearInListeningPayload>(apiClient.get('stats/year'));
  if (!payload.success) {
    throw new Error(payload.error || 'Failed to load your year in listening');
  }
  const { success: _success, error: _error, ...year } = payload;
  return year;
}

/** A library row shaped the way `window.playTrackList` expects. */
export interface PlayableTrack {
  id: number | string;
  title: string;
  artist: string | null;
  album: string | null;
  file_path: string;
  bitrate?: number | null;
  artist_id?: number | string | null;
  album_id?: number | string | null;
  image_url?: string | null;
}

/**
 * The owned tracks of an album, ready for the player's queue.
 *
 * Fetched at click time rather than shipped with the year: the story shows
 * four albums and the reader plays at most one, so loading every tracklist up
 * front would be four queries nobody asked for.
 */
export async function fetchAlbumPlayTracks(albumId: string | number): Promise<PlayableTrack[]> {
  const payload = await readJson<{ success: boolean; tracks?: PlayableTrack[]; error?: string }>(
    apiClient.get(`stats/album-tracks/${albumId}`),
  );
  if (!payload.success) {
    throw new Error(payload.error || 'Could not load that album');
  }
  return payload.tracks ?? [];
}

/**
 * Fetched only when the story is opened, never with the page.
 *
 * The year is a full pass over listening_history on a cache miss, and the vast
 * majority of stats-page visits never open it. Hanging it off the page load
 * would make every visit pay for a screen most of them will not look at.
 */
export function yearInListeningQueryOptions() {
  return queryOptions({
    queryKey: [...STATS_QUERY_KEY, 'year'],
    queryFn: fetchYearInListening,
    // The window only moves at a month boundary and the worker rebuilds every
    // 30 minutes — re-fetching on every re-open would be pure waste.
    staleTime: 5 * 60 * 1000,
  });
}

/** The story's soundtrack: the year's top songs you own, in the player's
 *  queue shape, each with where to start it. */
export async function fetchYearSoundtrack(): Promise<SoundtrackTrack[]> {
  const payload = await readJson<{ success: boolean; tracks?: SoundtrackTrack[] }>(
    apiClient.get('stats/year/soundtrack'),
  );
  return payload.success ? (payload.tracks ?? []) : [];
}

export interface SavedYearPlaylist {
  id: number;
  name: string;
  track_count: number;
  created: boolean;
}

/** Save the year's top `size` songs into "Your <year>"; again updates it. */
export async function saveYearPlaylist(size: 50 | 100): Promise<SavedYearPlaylist> {
  const res = await fetch('/api/stats/year/playlist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ size }),
  });
  const data = (await res.json().catch(() => null)) as
    | (SavedYearPlaylist & { success: boolean; error?: string })
    | null;
  if (!res.ok || !data?.success) throw new Error(data?.error || 'Could not save the playlist');
  return { id: data.id, name: data.name, track_count: data.track_count, created: data.created };
}

export interface YearBanner {
  show: boolean;
  year: number | null;
}

export async function fetchYearBanner(): Promise<YearBanner | null> {
  try {
    const res = await fetch('/api/stats/year/banner');
    if (!res.ok) return null;
    return (await res.json()) as YearBanner;
  } catch {
    return null;
  }
}

export async function dismissYearBanner(): Promise<boolean> {
  try {
    return (await fetch('/api/stats/year/banner/dismiss', { method: 'POST' })).ok;
  } catch {
    return false;
  }
}
