import { queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

/** you and one artist, from your own listening history */
export interface ArtistYourPlays {
  play_count: number;
  first_played: string | null;
  last_played: string | null;
  plays_30d: number;
  /** 1 = the artist you play most */
  rank: number;
  artists_played: number;
  /** the last 12 months, oldest first, 'YYYY-MM' local */
  months: Array<{ month: string; plays: number }>;
  top_tracks: Array<{
    title: string;
    album: string;
    play_count: number;
    last_played: string | null;
    track_id: number | null;
    album_id: number | null;
    thumb_url: string;
  }>;
}

type Response = { success: boolean; error?: string; listening?: ArtistYourPlays | null };

export async function fetchArtistYourPlays(artistId: number): Promise<ArtistYourPlays | null> {
  const payload = await readJson<Response>(
    apiClient.get(`library/v2/artists/${artistId}/your-plays`),
  );
  if (!payload.success) throw new Error(payload.error || 'Could not read your plays');
  return payload.listening ?? null;
}

export function artistYourPlaysQueryOptions(artistId: number) {
  return queryOptions({
    queryKey: ['library-v2', 'artist-your-plays', artistId],
    queryFn: () => fetchArtistYourPlays(artistId),
    enabled: artistId > 0,
    staleTime: 60_000,
  });
}

/** a stored play time (utc "YYYY-MM-DD HH:MM:SS") as a Date */
export function playDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(`${value.replace(' ', 'T')}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "today", "yesterday", "3 days ago", "5 weeks ago", "Mar 2019" */
export function sinceLabel(value: string | null | undefined, now: Date = new Date()): string {
  const date = playDate(value);
  if (!date) return '';
  const day = 86_400_000;
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(date)) / day);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 14) return `${days} days ago`;
  if (days < 60) return `${Math.round(days / 7)} weeks ago`;
  return monthYear(value);
}

/** "Mar 2019" */
export function monthYear(value: string | null | undefined): string {
  const date = playDate(value);
  if (!date) return '';
  return date.toLocaleString('en-US', { month: 'short', year: 'numeric' });
}

/** "Your #3 artist of 410", or "Your most played artist" */
export function rankLabel(rank: number, of: number): string {
  if (rank <= 1) return 'Your most played artist';
  return `Your #${rank.toLocaleString('en-US')} artist of ${of.toLocaleString('en-US')}`;
}
