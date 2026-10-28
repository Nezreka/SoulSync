import { keepPreviousData, queryOptions } from '@tanstack/react-query';

import { apiClient, readJson } from '@/app/api-client';

import type { CalendarEpisode, CalendarPayload, CalendarScope } from './-calendar.types';

export const CALENDAR_QUERY_KEY = ['video-calendar'] as const;

export interface CalendarParams {
  start: string;
  days: number;
  scope: CalendarScope;
  specials: boolean;
}

/** Something in flight moves every few seconds; nothing in flight, a calendar
 * changes when the automations run. */
export const LIVE_POLL_MS = 15_000;
export const IDLE_POLL_MS = 5 * 60_000;

export function pollInterval(data: CalendarPayload | undefined): number {
  const live =
    (data?.episodes ?? []).some((e) => e.acq === 'downloading' || e.acq === 'queued') ||
    (data?.movies ?? []).some((m) => m.acq === 'downloading' || m.acq === 'queued');
  return live ? LIVE_POLL_MS : IDLE_POLL_MS;
}

function searchParams(p: CalendarParams) {
  return { start: p.start, days: p.days, scope: p.scope, specials: p.specials ? 1 : 0 };
}

export function calendarQueryOptions(profileId: number, p: CalendarParams) {
  return queryOptions({
    queryKey: [
      ...CALENDAR_QUERY_KEY,
      'window',
      profileId,
      p.start,
      p.days,
      p.scope,
      p.specials,
    ] as const,
    queryFn: () =>
      readJson<CalendarPayload>(
        apiClient.get('video/calendar', { searchParams: searchParams(p), timeout: 30_000 }),
      ),
    // stepping weeks keeps the old grid on screen until the next one lands
    placeholderData: keepPreviousData,
    refetchOnWindowFocus: true,
    refetchInterval: (q) => pollInterval(q.state.data),
  });
}

/** Followed shows you don't own, from TMDB. Slower, so the grid never waits on it. */
export function followedQueryOptions(profileId: number, p: CalendarParams) {
  return queryOptions({
    queryKey: [...CALENDAR_QUERY_KEY, 'followed', profileId, p.start, p.days, p.specials] as const,
    // asks TMDB for each followed show: a cold first pass outlasts ky's 10s
    queryFn: () =>
      readJson<{ episodes: CalendarEpisode[] }>(
        apiClient.get('video/calendar/followed', {
          searchParams: searchParams(p),
          timeout: 60_000,
        }),
      ),
    staleTime: 10 * 60_000,
    placeholderData: keepPreviousData,
  });
}

export function wishlistMissing(body: {
  start: string;
  end: string;
  scope: CalendarScope;
  specials: boolean;
}) {
  return readJson<{ success: boolean; added: number; shows: number; error?: string }>(
    apiClient.post('video/calendar/wishlist-missing', { json: body }),
  );
}

function showPoster(ep: CalendarEpisode): string | null {
  if (ep.not_in_library) return ep.poster_url ?? null;
  return ep.show_has_poster && ep.show_id != null ? `/api/video/poster/show/${ep.show_id}` : null;
}

/** One episode onto the wishlist, the same payload the vanilla modal sent. */
export function wishEpisode(ep: CalendarEpisode) {
  return readJson<{ success?: boolean; error?: string }>(
    apiClient.post('video/wishlist/add', {
      json: {
        show: {
          tmdb_id: ep.show_tmdb_id,
          title: ep.show_title,
          poster_url: showPoster(ep),
          library_id: ep.show_id,
        },
        episodes: [
          {
            season_number: ep.season_number,
            episode_number: ep.episode_number,
            title: ep.title,
            air_date: ep.air_date,
          },
        ],
      },
    }),
  );
}

/** Clear the backoff on a failed episode and search every source again. */
export function retryEpisode(ep: CalendarEpisode) {
  return readJson<{ success?: boolean; error?: string }>(
    apiClient.post('video/wishlist/retry', {
      json: {
        scope: 'episode',
        tmdb_id: ep.show_tmdb_id,
        season_number: ep.season_number,
        episode_number: ep.episode_number,
      },
    }),
  );
}

/** Stop monitoring one episode: nothing hunts it and it stops counting as missing. */
export function ignoreEpisode(ep: CalendarEpisode) {
  return readJson<{ success?: boolean; error?: string }>(
    apiClient.post('video/episode/monitor', {
      json: {
        tmdb_id: ep.show_tmdb_id,
        season_number: ep.season_number,
        episode_number: ep.episode_number,
        monitored: false,
      },
    }),
  );
}

export function feedUrl(rotate = false) {
  const req = rotate
    ? apiClient.post('video/calendar/feed-url')
    : apiClient.get('video/calendar/feed-url');
  return readJson<{ success: boolean; path?: string; error?: string }>(req);
}

export interface ShowDetail {
  overview?: string | null;
  genres?: string[];
  network?: string | null;
  year?: number | null;
  status?: string | null;
  content_rating?: string | null;
}

export function showDetailQueryOptions(showId: number | null) {
  return queryOptions({
    queryKey: [...CALENDAR_QUERY_KEY, 'show', showId] as const,
    queryFn: () => readJson<ShowDetail>(apiClient.get(`video/detail/show/${showId}`)),
    enabled: showId != null,
    staleTime: 30 * 60_000,
  });
}
