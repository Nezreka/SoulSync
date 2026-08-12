/**
 * The listening-history band's pure core.
 *
 * Rows come from /api/stats/recent — the same listening_history spine the
 * stats page reads (media-server plays via the listening-stats worker +
 * SoulSync web-player plays). This module shapes them for the rail: parse,
 * time-ago, source label. Timestamps are DB UTC strings, parsed with the
 * same rule -dash.autosync uses.
 */

import { parseDbUtc } from './-dash.autosync';

export interface RecentPlayRow {
  title?: string | null;
  artist?: string | null;
  album?: string | null;
  played_at?: string | null;
  server_source?: string | null;
  image_url?: string | null;
  artist_db_id?: number | string | null;
}

export interface RecentPlay {
  key: string;
  title: string;
  artist: string;
  imageUrl: string | null;
  /** '' until the timestamp parses — the row renders without a time. */
  ago: string;
  source: string;
  /** Library artist PK when the play was matched; null → resolve by name. */
  artistDbId: number | string | null;
}

/** "just now" → "3h ago" → "2d ago". Coarse on purpose: the band is a vibe,
 *  the stats page is the ledger. */
export function timeAgo(playedAt: string | null | undefined, now: Date): string {
  if (!playedAt) return '';
  // parseDbUtc returns MILLISECONDS (NaN when unparseable), not a Date.
  const thenMs = parseDbUtc(playedAt);
  if (!Number.isFinite(thenMs)) return '';
  const seconds = Math.floor((now.getTime() - thenMs) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

const SOURCE_LABELS: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  navidrome: 'Navidrome',
  soulsync: 'SoulSync',
  web_player: 'SoulSync',
};

export function sourceLabel(source: string | null | undefined): string {
  if (!source) return '';
  return SOURCE_LABELS[source.toLowerCase()] ?? source;
}

/** Shape the API rows for the rail. Untitled rows are dropped — a play with
 *  no title renders as an empty tile and says nothing. */
export function toRecentPlays(rows: RecentPlayRow[], now: Date, limit: number): RecentPlay[] {
  const out: RecentPlay[] = [];
  for (const row of rows) {
    const title = (row.title ?? '').trim();
    if (!title) continue;
    out.push({
      key: `${title}|${row.artist ?? ''}|${row.played_at ?? ''}`,
      title,
      artist: (row.artist ?? '').trim(),
      imageUrl: row.image_url || null,
      ago: timeAgo(row.played_at, now),
      source: sourceLabel(row.server_source),
      artistDbId: row.artist_db_id ?? null,
    });
    if (out.length >= limit) break;
  }
  return out;
}
