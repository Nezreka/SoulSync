/**
 * The video dashboard, pure parts: payload shapes and every number and word
 * the page shows. Ported from the vanilla video-dashboard.js, same behaviour.
 */

import { appURL } from '@/platform/url-base';

import type { AcqState, CalendarEpisode } from '../../calendar/-calendar.types';

// ── payloads ────────────────────────────────────────────────────────────────

export interface RecentItem {
  id: number;
  kind: 'movie' | 'show';
  title: string;
  year?: number | null;
  /** sqlite utc 'YYYY-MM-DD HH:MM:SS' */
  added_at?: string | null;
}

/** GET /api/video/dashboard (api/video/dashboard.py) */
export interface VideoDashboardStats {
  library: { movies: number; shows: number; episodes: number; size_bytes: number };
  downloads: { active: number; finished: number; speed_bps: number };
  watchlist: number;
  wishlist: number;
  server: string | null;
  recent: RecentItem[];
}

/** GET /api/video/dashboard/continue-watching */
export interface ContinueItem {
  kind: 'movie' | 'show';
  /** the movie id, or the episode id for a show row */
  id: number;
  show_id?: number | null;
  title: string;
  subtitle?: string | null;
  image_url?: string | null;
  runtime_minutes?: number | null;
  view_offset_ms?: number | null;
  /** 'in_progress' | 'up_next' */
  reason?: string;
}

/** one row of GET /api/video/downloads/active */
export interface ActiveDownload {
  id: number | string;
  status: string;
  title?: string | null;
  release_title?: string | null;
  year?: number | null;
  progress?: number | string | null;
  speed_bps?: number | null;
  eta_seconds?: number | string | null;
  import_phase?: string | null;
  poster_url?: string | null;
  media_source?: string | null;
  media_id?: number | string | null;
  kind?: string | null;
}

/** GET /api/video/scan/status (video-scan.js emits the same shape) */
export interface ScanStatus {
  /** 'idle' | 'scanning' | 'error' */
  state: string;
  last_scan_at?: string | null;
  phase?: string | null;
  percent?: number | null;
  movies?: number | null;
  shows?: number | null;
  mode?: string | null;
  error?: string | null;
}

/** GET /api/video/enrichment/coverage */
export interface Coverage {
  movies?: { total?: number; tmdb_enriched?: number };
  shows?: { total?: number; tmdb_enriched?: number; tvdb_matched?: number };
}

// ── hero ────────────────────────────────────────────────────────────────────

export function formatBytes(n: number | null | undefined): string {
  const v = Number(n) || 0;
  if (v <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0;
  let x = v;
  while (x >= 1024 && i < units.length - 1) {
    x /= 1024;
    i += 1;
  }
  return `${x >= 100 || i === 0 ? Math.round(x) : x.toFixed(1)} ${units[i]}`;
}

export function formatSpeed(bps: number | null | undefined): string {
  return `${formatBytes(bps)}/s`;
}

/** 12,345 below a hundred thousand, 105.7K above, so the numbers keep one width */
export function compactCount(n: number): string {
  if (n < 100_000) return n.toLocaleString('en-US');
  if (n < 1_000_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}K`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
}

export interface HeroNumber {
  id: 'movies' | 'shows' | 'episodes' | 'size';
  label: string;
  value: string;
}

/** the hero's readouts. an empty library says nothing rather than four zeros. */
export function videoHeroNumbers(stats: VideoDashboardStats | null | undefined): HeroNumber[] {
  const lib = stats?.library;
  if (!lib) return [];
  if (!lib.movies && !lib.shows) return [];
  const out: HeroNumber[] = [
    { id: 'movies', label: 'Movies', value: compactCount(lib.movies) },
    { id: 'shows', label: 'Shows', value: compactCount(lib.shows) },
    { id: 'episodes', label: 'Episodes', value: compactCount(lib.episodes) },
  ];
  if (lib.size_bytes > 0)
    out.push({ id: 'size', label: 'On disk', value: formatBytes(lib.size_bytes) });
  return out;
}

function parseUtc(ts: string): number {
  const s = String(ts).trim();
  // sqlite utc stamps carry no zone; iso ones do
  const iso = s.includes('T') ? s : s.replace(' ', 'T');
  return Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
}

/** '2h ago' for a utc stamp; '' when unknown or older than a month */
export function ago(ts: string | null | undefined, now: number): string {
  if (!ts) return '';
  const t = parseUtc(ts);
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, (now - t) / 1000);
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)}d ago`;
  return '';
}

export function serverLabel(server: string | null | undefined): string {
  if (!server) return 'No server';
  if (server === 'plex') return 'Plex';
  if (server === 'jellyfin') return 'Jellyfin';
  if (server === 'emby') return 'Emby';
  return server.charAt(0).toUpperCase() + server.slice(1);
}

export interface LibraryStripView {
  state: 'checking' | 'scanning' | 'no-server' | 'empty' | 'ready';
  title: string;
  subtitle: string;
}

/** the console strip under the numbers: what the library is doing right now */
export function libraryStrip(
  stats: VideoDashboardStats | null | undefined,
  scan: ScanStatus | null | undefined,
  now: number,
): LibraryStripView {
  if (scan?.state === 'scanning') {
    const phase = scan.phase
      ? scan.phase.charAt(0).toUpperCase() + scan.phase.slice(1)
      : 'Scanning';
    return { state: 'scanning', title: 'Library scan', subtitle: `${phase}…` };
  }
  // not loaded yet is not "no server": the stats can take seconds on a big library
  if (!stats) return { state: 'checking', title: 'Library', subtitle: 'Checking your library…' };
  const server = stats.server ?? null;
  if (!server) {
    return {
      state: 'no-server',
      title: 'No media server',
      subtitle: 'Connect Plex or Jellyfin in Settings',
    };
  }
  const name = serverLabel(server);
  if (!stats.library.movies && !stats.library.shows) {
    return {
      state: 'empty',
      title: `${name} connected`,
      subtitle: 'Run a scan to fill the library',
    };
  }
  const when = ago(scan?.last_scan_at ?? null, now);
  return {
    state: 'ready',
    title: `${name} library`,
    subtitle: when ? `Last scanned ${when}` : 'Up to date with your server',
  };
}

// ── continue watching ───────────────────────────────────────────────────────
// the card answers how much is LEFT. "47 minutes in" needs the runtime to mean
// anything, "23 min left" is already the answer.

export function remaining(it: ContinueItem): string {
  const total = Number(it.runtime_minutes || 0) * 60000;
  const off = Number(it.view_offset_ms || 0);
  if (!total) return '';
  const mins = Math.round(Math.max(0, total - off) / 60000);
  if (mins <= 0) return '';
  if (mins < 60) return `${mins} min left`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h}h ${m}m left` : `${h}h left`;
}

export function watchedPct(it: ContinueItem): number {
  const total = Number(it.runtime_minutes || 0) * 60000;
  const off = Number(it.view_offset_ms || 0);
  if (!total || off <= 0) return 0;
  return Math.max(1, Math.min(100, Math.round((off / total) * 100)));
}

/** a real url, so middle-click and new tab work */
export function continueHref(it: ContinueItem): string {
  return it.kind === 'show'
    ? appURL(`/video-detail/library/show/${it.show_id}`)
    : appURL(`/video-detail/library/movie/${it.id}`);
}

// ── downloading now ─────────────────────────────────────────────────────────
// /downloads/active returns the last 100 rows of ANY status, so filter rather
// than trust its name.

export const DL_POLL_ACTIVE_MS = 3000;
export const DL_POLL_IDLE_MS = 15000;
// a rail card, so a short list; the downloads page has the rest
export const DL_MAX_ROWS = 4;

const IN_FLIGHT = new Set(['downloading', 'queued', 'searching', 'importing']);

export function inFlight(rows: ActiveDownload[] | null | undefined): ActiveDownload[] {
  return (rows ?? []).filter((r) => IN_FLIGHT.has(String(r.status)));
}

/** 'downloading' is obvious from the bar, so the pill says the phase instead */
export function dlStatus(d: ActiveDownload): string {
  const st = String(d.status || '');
  if (st === 'importing') return d.import_phase ? String(d.import_phase) : 'Importing';
  if (st === 'searching') return 'Searching';
  if (st === 'queued') return 'Queued';
  return 'Downloading';
}

/** some writers store a 0-1 fraction, some a percent */
export function dlPct(d: ActiveDownload): number {
  let p = Number(d.progress);
  if (!Number.isFinite(p) || p <= 0) return 0;
  if (p <= 1) p *= 100;
  return Math.max(0, Math.min(100, Math.round(p)));
}

/** queued and searching have nothing to put on a bar; a 0% bar reads as stalled */
export function dlHasBar(d: ActiveDownload): boolean {
  return d.status === 'downloading' || d.status === 'importing';
}

export function dlSub(d: ActiveDownload): string {
  const bits: string[] = [];
  if (d.status === 'downloading' && Number(d.speed_bps) > 0) bits.push(formatSpeed(d.speed_bps));
  const eta = parseInt(String(d.eta_seconds ?? ''), 10);
  if (eta > 0) {
    bits.push(
      eta < 60
        ? `~${eta}s left`
        : eta < 3600
          ? `~${Math.round(eta / 60)}m left`
          : `~${Math.floor(eta / 3600)}h ${Math.round((eta % 3600) / 60)}m left`,
    );
  }
  return bits.join(' · ');
}

/** poster_url is whatever the grab passed (wishlist grabs pass nothing); a
 * library row can always be resolved from its own id */
export function dlArt(d: ActiveDownload): string {
  if (d.poster_url) return String(d.poster_url);
  if (d.media_source === 'library' && d.media_id && d.kind) {
    return appURL(
      `/api/video/poster/${encodeURIComponent(d.kind)}/${encodeURIComponent(String(d.media_id))}?w=120`,
    );
  }
  return '';
}

export function dlTitle(d: ActiveDownload): string {
  return d.title || d.release_title || 'Unknown';
}

// ── recently added ──────────────────────────────────────────────────────────

export function posterOf(it: { kind: string; id: number }, w = 300): string {
  return appURL(`/api/video/poster/${it.kind}/${it.id}?w=${w}`);
}

export function recentHref(it: RecentItem): string {
  return appURL(`/video-detail/library/${it.kind}/${it.id}`);
}

export function recentSub(it: RecentItem, now: number): string {
  const bits: string[] = [];
  if (it.year) bits.push(String(it.year));
  const when = ago(it.added_at ?? null, now);
  if (when) bits.push(when);
  return bits.join(' · ');
}

// ── this week ───────────────────────────────────────────────────────────────
// the rail's question: did what aired this week land? an aired episode you do
// not have is the auto-wishlist falling behind, and this is where it shows.

export const WEEK_BACK_DAYS = 7;
export const WEEK_AHEAD_DAYS = 7;

/** the meter's segments, in reading order */
export const WEEK_ORDER: AcqState[] = [
  'owned',
  'downloading',
  'queued',
  'wanted',
  'failed',
  'missing',
  'unmatched',
  'ignored',
];

export interface WeekSummary {
  aired: number;
  owned: number;
  missing: number;
  upcoming: number;
  segments: { state: AcqState; count: number }[];
}

/** episodes from the past week that have aired, and what's still to come */
export function weekSummary(eps: CalendarEpisode[] | null | undefined, today: string): WeekSummary {
  const aired: CalendarEpisode[] = [];
  let upcoming = 0;
  for (const e of eps ?? []) {
    if (e.acq === 'unaired' || e.air_date > today) upcoming += 1;
    else aired.push(e);
  }
  const counts = new Map<AcqState, number>();
  for (const e of aired) counts.set(e.acq, (counts.get(e.acq) ?? 0) + 1);
  return {
    aired: aired.length,
    owned: counts.get('owned') ?? 0,
    missing: counts.get('missing') ?? 0,
    upcoming,
    segments: WEEK_ORDER.filter((st) => counts.get(st)).map((state) => ({
      state,
      count: counts.get(state) ?? 0,
    })),
  };
}

export function weekLine(w: WeekSummary): string {
  if (!w.aired) return 'Nothing on your watchlist aired this past week.';
  if (w.owned >= w.aired) return `All ${w.aired} that aired are in your library.`;
  return `${w.owned} of ${w.aired} that aired are in your library.`;
}

// ── coverage ────────────────────────────────────────────────────────────────

export interface CoverageRing {
  key: string;
  provider: string;
  kind: string;
  pct: number;
}

export function coverageRings(d: Coverage | null | undefined): CoverageRing[] {
  const m = d?.movies ?? {};
  const s = d?.shows ?? {};
  const pct = (done: number | undefined, total: number | undefined) =>
    total ? Math.round(((done || 0) / total) * 100) : 0;
  const out: CoverageRing[] = [];
  if (m.total)
    out.push({
      key: 'tmdb-movies',
      provider: 'TMDB',
      kind: 'Movies',
      pct: pct(m.tmdb_enriched, m.total),
    });
  if (s.total) {
    out.push({
      key: 'tmdb-shows',
      provider: 'TMDB',
      kind: 'Shows',
      pct: pct(s.tmdb_enriched, s.total),
    });
    out.push({
      key: 'tvdb-shows',
      provider: 'TVDB',
      kind: 'Shows',
      pct: pct(s.tvdb_matched, s.total),
    });
  }
  return out;
}
