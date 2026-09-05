import type {
  StatsArtistRow,
  StatsCachedPayload,
  StatsDbStorageTable,
  StatsHealth,
  StatsOverview,
  StatsRange,
} from './-stats.types';

export const EMPTY_STATS_OVERVIEW: StatsOverview = {
  total_plays: 0,
  total_time_ms: 0,
  unique_artists: 0,
  unique_albums: 0,
  unique_tracks: 0,
};

export const EMPTY_STATS_PAYLOAD: Required<
  Pick<
    StatsCachedPayload,
    'overview' | 'top_artists' | 'top_albums' | 'top_tracks' | 'timeline' | 'genres' | 'recent'
  >
> & { health: StatsHealth } = {
  overview: EMPTY_STATS_OVERVIEW,
  top_artists: [],
  top_albums: [],
  top_tracks: [],
  timeline: [],
  genres: [],
  recent: [],
  health: {},
};

export const STATS_GENRE_COLORS = [
  '#1db954',
  '#1ed760',
  '#4ade80',
  '#7c3aed',
  '#a855f7',
  '#ec4899',
  '#f43f5e',
  '#f97316',
  '#eab308',
  '#06b6d4',
] as const;

export const STATS_DB_STORAGE_COLORS = [
  '#3b82f6',
  '#f97316',
  '#a855f7',
  '#14b8a6',
  '#eab308',
  '#ec4899',
  '#6366f1',
  '#22c55e',
  '#555555',
] as const;

export const STATS_ENRICHMENT_SERVICES = [
  { key: 'spotify', label: 'Spotify', color: '#1db954' },
  { key: 'musicbrainz', label: 'MusicBrainz', color: '#ba55d3' },
  { key: 'deezer', label: 'Deezer', color: '#a238ff' },
  { key: 'jiosaavn', label: 'JioSaavn', color: '#2bc5b4' },
  { key: 'lastfm', label: 'Last.fm', color: '#d51007' },
  { key: 'itunes', label: 'iTunes', color: '#fc3c44' },
  { key: 'audiodb', label: 'AudioDB', color: '#1a9fff' },
  { key: 'genius', label: 'Genius', color: '#ffff64' },
  { key: 'tidal', label: 'Tidal', color: '#00ffff' },
  { key: 'qobuz', label: 'Qobuz', color: '#4285f4' },
  { key: 'bandcamp', label: 'Bandcamp', color: '#1da0c3' },
] as const;

export function visibleStatsEnrichmentServices(jiosaavnEnabled: boolean, bandcampEnabled: boolean) {
  return STATS_ENRICHMENT_SERVICES.filter((service) => {
    if (service.key === 'jiosaavn') return jiosaavnEnabled;
    if (service.key === 'bandcamp') return bandcampEnabled;
    return true;
  });
}

export function getStatsRangeLabel(range: StatsRange): string {
  switch (range) {
    case '7d':
      return '7 Days';
    case '30d':
      return '30 Days';
    case '12m':
      return '12 Months';
    case 'all':
      return 'All Time';
  }
}

export function hasStatsData(overview: Partial<StatsOverview> | undefined): boolean {
  return (overview?.total_plays ?? 0) > 0;
}

export function formatCompactNumber(value: number | null | undefined): string {
  if (!value) return '0';
  if (value >= 1_000_000) return `${stripTrailingZero((value / 1_000_000).toFixed(1))}M`;
  if (value >= 1_000) return `${stripTrailingZero((value / 1_000).toFixed(1))}K`;
  return value.toLocaleString('en-US');
}

export function formatListeningTime(totalMs: number | null | undefined): string {
  if (!totalMs) return '0h';
  const hours = Math.floor(totalMs / 3_600_000);
  const minutes = Math.floor((totalMs % 3_600_000) / 60_000);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

export function formatTotalDuration(totalMs: number | null | undefined): string {
  if (!totalMs) return '0h';
  return `${Math.floor(totalMs / 3_600_000)}h`;
}

export function formatRelativePlayedAt(
  dateStr: string | null | undefined,
  now = Date.now(),
): string {
  if (!dateStr) return '';
  const diff = now - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

export function formatBytes(value: number | null | undefined): string {
  if (!value || value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  let next = value;
  while (next >= 1024 && index < units.length - 1) {
    next /= 1024;
    index += 1;
  }
  return `${next.toFixed(next < 10 ? 2 : 1)} ${units[index]}`;
}

export function groupDbStorageTables(tables: StatsDbStorageTable[]): StatsDbStorageTable[] {
  const top = tables.slice(0, 8);
  const rest = tables.slice(8);
  const restSize = rest.reduce((sum, table) => sum + table.size, 0);
  return restSize > 0 ? [...top, { name: 'Other', size: restSize }] : top;
}

export function formatDbStorageValue(size: number, method: string | null | undefined): string {
  if (method === 'dbstat') {
    if (size > 1_048_576) return `${(size / 1_048_576).toFixed(1)} MB`;
    return `${Math.round(size / 1024)} KB`;
  }
  return `${size.toLocaleString('en-US')} rows`;
}

export function getTopArtistBubbles(artists: StatsArtistRow[]) {
  const top = artists.slice(0, 5);
  const maxPlays = top[0]?.play_count || 1;

  return top.map((artist, index) => ({
    artist,
    percent: Math.round((artist.play_count / maxPlays) * 100),
    size: 44 + (4 - index) * 6,
  }));
}

function stripTrailingZero(value: string): string {
  return value.replace(/\.0$/, '');
}

/**
 * A stat's change against the equivalent previous period.
 *
 * The stats page printed totals with nothing to measure them against — "1,247
 * plays" says nothing about whether that is a lot. The delta is what turns a
 * trivia number into a signal.
 *
 * Returns null whenever a percentage would be a lie rather than a fact:
 *
 * - `previous` is null — the range is 'all', which has no period before it.
 * - `previous` is 0 — "up from nothing" has no honest percentage. Growth from
 *   0 to 5 is not 500%, it is not a ratio at all. The caller shows "new"
 *   instead of inventing ∞.
 * - either side is missing/NaN — a partial payload must not render a delta.
 *
 * Zero change returns a delta with pct 0 and direction 'flat' — "no change" is
 * a real answer and worth showing, unlike the cases above.
 */
export interface StatDelta {
  pct: number;
  direction: 'up' | 'down' | 'flat';
}

export function statDelta(
  current: number | undefined | null,
  previous: number | undefined | null,
): StatDelta | null {
  if (typeof current !== 'number' || !Number.isFinite(current)) return null;
  if (typeof previous !== 'number' || !Number.isFinite(previous)) return null;
  // Up from zero is not a percentage. Callers render "new".
  if (previous === 0) return null;

  const change = ((current - previous) / previous) * 100;
  if (!Number.isFinite(change)) return null;

  const pct = Math.round(Math.abs(change));
  // Round BEFORE deciding direction: a +0.4% change displays as "0%", and an
  // arrow next to "0%" reads as a rendering bug.
  if (pct === 0) return { pct: 0, direction: 'flat' };
  return { pct, direction: change > 0 ? 'up' : 'down' };
}

/** True when there is a previous period and it was empty — "new", not "+∞%". */
export function isNewSincePrevious(
  current: number | undefined | null,
  previous: number | undefined | null,
): boolean {
  return previous === 0 && typeof current === 'number' && current > 0;
}

// ── When you listen (stats P3) ───────────────────────────────────────────────

/** Sunday-first, matching SQLite's strftime('%w'). */
export const WEEKDAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

/** "9pm", "12am", "1pm" — a heatmap axis nobody has to decode. */
export function formatHourLabel(hour: number): string {
  const h = ((hour % 24) + 24) % 24;
  if (h === 0) return '12am';
  if (h === 12) return '12pm';
  return h < 12 ? `${h}am` : `${h - 12}pm`;
}

/**
 * Cell shade, 0..1, relative to the busiest cell.
 *
 * Relative and not absolute: an absolute scale makes every heatmap of a small
 * library look empty, and the question is "when do YOU listen", not "how do
 * you compare to someone else".
 *
 * A cell with ANY plays gets a visible floor — the difference between one play
 * and none is the most interesting one on the chart, and a linear ramp from 0
 * renders it invisible next to a peak of 300.
 */
export function heatIntensity(plays: number, peak: number): number {
  if (plays <= 0 || peak <= 0) return 0;
  const FLOOR = 0.18;
  return FLOOR + (1 - FLOOR) * Math.sqrt(plays / peak);
}

/** "Wed 9pm" — the one-line answer to when you listen most. */
export function formatPeakSlot(
  weekday: number | null | undefined,
  hour: number | null | undefined,
): string | null {
  if (typeof weekday !== 'number' || typeof hour !== 'number') return null;
  const day = WEEKDAY_LABELS[weekday];
  if (!day) return null;
  return `${day} ${formatHourLabel(hour)}`;
}
