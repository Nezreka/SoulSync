import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

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
