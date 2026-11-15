/**
 * What the Downloads tab shows and saves, worked out the way settings.js did
 * (SETTINGS_PORT.md, step 5). The download chain (Source Settings) is not
 * here: it moves with the Sources tab.
 */

import type { SettingsPayload } from '../settings-store';

export const STREAM_SOURCES = [
  { value: 'youtube', label: 'YouTube (Instant)' },
  { value: 'active', label: 'Active Download Source' },
  { value: 'deezer_preview', label: 'Deezer 30s Preview (Instant)' },
] as const;

export const CONCURRENCY = [
  { value: '1', label: '1' },
  { value: '2', label: '2' },
  { value: '3', label: '3 (Default)' },
  { value: '4', label: '4' },
  { value: '5', label: '5' },
  { value: '6', label: '6' },
  { value: '8', label: '8' },
  { value: '10', label: '10' },
] as const;

export const PREFERRED_VERSIONS = [
  { value: '', label: 'Off — take what the source asked for' },
  { value: 'extended', label: 'Extended mix / long version' },
  { value: 'radio', label: 'Radio edit / radio version' },
  { value: 'remix', label: 'Remix' },
  { value: 'live', label: 'Live' },
  { value: 'acoustic', label: 'Acoustic' },
  { value: 'instrumental', label: 'Instrumental' },
  { value: 'demo', label: 'Demo' },
] as const;

export interface DownloadsValues {
  streamSource: string;
  maxConcurrent: string;
  searchTimeout: string;
  retryNext: boolean;
  retryExhaustive: boolean;
  retriesPerQuery: string;
  versionFallback: boolean;
  versionMinCount: string;
  atomicPublish: boolean;
  atomicRecover: boolean;
  allowExplicit: boolean;
  preferExplicit: boolean;
  excludeClean: boolean;
  maxMbPerMinute: string;
  preferredVersion: string;
  genreWhitelist: boolean;
  genres: string[];
}

/** a saved number or string, as the field's text */
const text = (value: unknown, fallback: number | string) =>
  typeof value === 'number' || typeof value === 'string' ? String(value) : String(fallback);

export function resolveDownloads(settings: SettingsPayload): DownloadsValues {
  const ds = settings.download_source ?? {};
  const pp = settings.post_processing ?? {};
  const albums = settings.album_downloads ?? {};
  const filter = settings.content_filter ?? {};
  const gw = settings.genre_whitelist ?? {};
  return {
    streamSource: (ds.stream_source as string) || 'youtube',
    maxConcurrent: String((ds.max_concurrent as number | string) || '3'),
    // #1056: 0 or blank = each source's built-in default
    searchTimeout: ds.source_search_timeout ? text(ds.source_search_timeout, '') : '',
    retryNext: pp.retry_next_candidate_on_mismatch !== false,
    retryExhaustive: pp.retry_exhaustive === true,
    retriesPerQuery: text(pp.retries_per_query, 5),
    versionFallback: pp.accept_version_mismatch_fallback === true,
    versionMinCount: text(pp.version_mismatch_min_count, 2),
    atomicPublish: albums.atomic_publish === true,
    // on unless switched off: an absent value means recover
    atomicRecover: albums.atomic_recover_orphans !== false,
    allowExplicit: filter.allow_explicit !== false,
    preferExplicit: filter.prefer_explicit === true,
    excludeClean: filter.exclude_clean === true,
    maxMbPerMinute: text(ds.max_mb_per_minute, 0),
    preferredVersion: (settings.soulseek?.preferred_version as string) || '',
    genreWhitelist: gw.enabled === true,
    genres: Array.isArray(gw.genres) ? (gw.genres as string[]) : [],
  };
}

/** the Filtering hint settings.js wrote once the settings had loaded */
export function filteringSummary(values: DownloadsValues): string {
  const bits = [values.allowExplicit ? 'explicit allowed' : 'explicit blocked'];
  if (values.genreWhitelist) bits.push('genre whitelist on');
  return bits.join(' · ');
}

/** settings.js _cfgInt / _cfgFloat: what a number field saves */
export const toInt = (raw: string, fallback: number) => {
  const n = parseInt(raw, 10);
  return Number.isFinite(n) ? n : fallback;
};
export const toFloat = (raw: string, fallback: number) => {
  const n = parseFloat(raw);
  return Number.isFinite(n) ? n : fallback;
};

export function addGenre(genres: string[], genre: string): string[] {
  const name = genre.trim();
  if (!name || genres.some((g) => g.toLowerCase() === name.toLowerCase())) return genres;
  return [...genres, name].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
}
