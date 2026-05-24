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
  { key: 'lastfm', label: 'Last.fm', color: '#d51007' },
  { key: 'itunes', label: 'iTunes', color: '#fc3c44' },
  { key: 'audiodb', label: 'AudioDB', color: '#1a9fff' },
  { key: 'genius', label: 'Genius', color: '#ffff64' },
  { key: 'tidal', label: 'Tidal', color: '#00ffff' },
  { key: 'qobuz', label: 'Qobuz', color: '#4285f4' },
] as const;

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
  return value.toLocaleString();
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
  return `${size.toLocaleString()} rows`;
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
