import { appURL } from '@/platform/url-base';

import type { CalendarEpisode, CalendarMovie } from './-calendar.types';

/** The episode still, else the show backdrop. A followed show you don't own
 * has no proxy, so its TMDB still is used as is. */
export function stillOf(ep: CalendarEpisode, w = 500): string | null {
  if (ep.not_in_library) return ep.still_url || null;
  if (ep.has_still) return appURL(`/api/video/poster/episode/${ep.id}?w=${w}`);
  return backdropOf(ep, w);
}

export function backdropOf(ep: CalendarEpisode, w = 1280): string | null {
  if (ep.not_in_library) return ep.still_url || null;
  if (ep.show_has_backdrop && ep.show_id != null)
    return appURL(`/api/video/backdrop/show/${ep.show_id}?w=${w}`);
  return null;
}

export function moviePoster(m: CalendarMovie): string | null {
  return m.poster_url || null;
}

/** Real links, so cmd-click opens a tab; a plain click goes through the shell. */
export function showHref(ep: CalendarEpisode): string {
  return ep.not_in_library || ep.show_id == null
    ? appURL(`/video-detail/tmdb/show/${ep.show_tmdb_id}`)
    : appURL(`/video-detail/library/show/${ep.show_id}`);
}

export function movieHref(m: CalendarMovie): string {
  return m.owned && m.library_id
    ? appURL(`/video-detail/library/movie/${m.library_id}`)
    : appURL(`/video-detail/tmdb/movie/${m.tmdb_id}`);
}

/** video-side.js opens detail pages from this event, the one way the video
 * side navigates into a title. */
export function openShowDetail(ep: CalendarEpisode): void {
  const detail =
    ep.not_in_library || ep.show_id == null
      ? { kind: 'show', id: ep.show_tmdb_id, source: 'tmdb' }
      : { kind: 'show', id: ep.show_id, source: 'library' };
  document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', { detail }));
}

export function openMovieDetail(m: CalendarMovie): void {
  const detail =
    m.owned && m.library_id
      ? { kind: 'movie', id: m.library_id, source: 'library' }
      : { kind: 'movie', id: m.tmdb_id, source: 'tmdb' };
  document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', { detail }));
}
