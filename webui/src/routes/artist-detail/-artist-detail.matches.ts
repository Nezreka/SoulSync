/**
 * The "Wrong match?" panel: one row per metadata source, what the artist is
 * matched to there, and how it got that way.
 *
 * Built from the /api/artist/<id>/record row rather than the page payload.
 * The standard view's artist carries the ids but none of the
 * `*_match_status` / `*_last_attempted` columns, and the enhanced payload
 * only exists once the admin has switched views. The record has everything
 * and is always fresh.
 */

import { getServiceUrl } from './-artist-detail.enhanced-album';
import { ARTIST_MATCH_SERVICES } from './-artist-detail.meta';

export type MatchState = 'matched' | 'not_found' | 'error' | 'pending' | 'never';

export interface MatchRow {
  svc: string;
  label: string;
  /** Column on the artists row that holds this source's id (or url). */
  idKey: string;
  /** The stored id, or null when the source is unmatched. */
  value: string | null;
  /** Where the stored match points on the source's own site, when it has one. */
  url: string | null;
  state: MatchState;
  stateLabel: string;
  /** ISO-ish timestamp of the last automatic attempt, raw from the row. */
  attempted: string | null;
  /**
   * Whether "Auto" (re-run the source's own lookup) makes sense: only while
   * unmatched, since every worker keeps a stored id rather than re-searching,
   * and only for sources /api/library/enrich accepts (amazon is not one).
   */
  canAutoLookup: boolean;
}

/** Services /api/library/enrich will run on demand. */
const AUTO_LOOKUP_SERVICES = new Set([
  'audiodb',
  'deezer',
  'musicbrainz',
  'spotify',
  'itunes',
  'lastfm',
  'genius',
  'tidal',
  'qobuz',
  'discogs',
  'jiosaavn',
]);

/** artists-table id column per service, artist level only (mirrors _SERVICE_ID_COLUMNS). */
export const ARTIST_ID_COLUMNS: Record<string, string> = {
  spotify: 'spotify_artist_id',
  musicbrainz: 'musicbrainz_id',
  deezer: 'deezer_id',
  jiosaavn: 'jiosaavn_id',
  audiodb: 'audiodb_id',
  discogs: 'discogs_id',
  itunes: 'itunes_artist_id',
  lastfm: 'lastfm_url',
  genius: 'genius_id',
  tidal: 'tidal_id',
  qobuz: 'qobuz_id',
  amazon: 'amazon_id',
};

const STATE_LABELS: Record<MatchState, string> = {
  matched: 'Matched',
  not_found: 'Not found',
  error: 'Lookup failed',
  pending: 'Queued',
  never: 'Not tried yet',
};

/**
 * The stored id wins over the status column. The id is what every lookup
 * actually uses, so a row with an id and a stale "not_found" is matched for
 * all practical purposes, and a "matched" row whose id was cleared is not.
 */
export function matchState(value: string | null, status: unknown): MatchState {
  if (value) return 'matched';
  if (status === 'not_found' || status === 'error' || status === 'pending') return status;
  return 'never';
}

/** Services in chip order, jiosaavn filtered the same way the chips filter it. */
export function visibleMatchServices(): (typeof ARTIST_MATCH_SERVICES)[number][] {
  const filter = window.filterJiosaavnServiceEntries;
  if (typeof filter === 'function') {
    return filter([...ARTIST_MATCH_SERVICES], 'svc') as (typeof ARTIST_MATCH_SERVICES)[number][];
  }
  return ARTIST_MATCH_SERVICES.filter((s) => s.svc !== 'jiosaavn');
}

export function buildMatchRows(record: Record<string, unknown>): MatchRow[] {
  return visibleMatchServices().map((service) => {
    const idKey = ARTIST_ID_COLUMNS[service.svc] ?? `${service.svc}_id`;
    const raw = record[idKey];
    // A 0 id counts as absent, as the id badges treat it.
    const value = raw ? String(raw) : null;
    const state = matchState(value, record[service.key]);
    // genius stores a numeric id AND the page url; the url is the link.
    const linkValue = service.svc === 'genius' ? record.genius_url || value : value;
    const url = value ? getServiceUrl(service.svc, 'artist', linkValue) : null;
    const attempted = record[service.attempted];
    return {
      svc: service.svc,
      label: service.label,
      idKey,
      value,
      url,
      state,
      stateLabel: STATE_LABELS[state],
      attempted: attempted ? String(attempted) : null,
      canAutoLookup: state !== 'matched' && AUTO_LOOKUP_SERVICES.has(service.svc),
    };
  });
}

export function matchSummary(rows: MatchRow[]): { matched: number; total: number } {
  return { matched: rows.filter((r) => r.state === 'matched').length, total: rows.length };
}

/**
 * sqlite's CURRENT_TIMESTAMP is "YYYY-MM-DD HH:MM:SS" in UTC with no zone
 * marker, which `new Date` reads as LOCAL time. Pin it to UTC before parsing
 * so "2 hours ago" is not off by the user's offset.
 */
export function parseAttempted(value: string | null): Date | null {
  if (!value) return null;
  const text = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
    ? `${value.replace(' ', 'T')}Z`
    : value;
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function relativeTime(value: string | null, now: number = Date.now()): string {
  const date = parseAttempted(value);
  if (!date) return '';
  const seconds = Math.max(0, Math.round((now - date.getTime()) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 60) return `${days}d ago`;
  const months = Math.round(days / 30);
  if (months < 24) return `${months}mo ago`;
  return `${Math.round(months / 12)}y ago`;
}

/** Only an admin on a library artist gets the button, same gate as the enhanced view. */
export function showsFixMatchButton(
  artist: { id?: unknown } | undefined,
  isSourceArtist: boolean,
  isAdmin: boolean,
): boolean {
  return Boolean(artist?.id) && !isSourceArtist && isAdmin;
}
