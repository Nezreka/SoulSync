import type {
  VideoDiagnostics,
  VideoEpisode,
  VideoMedia,
  VideoMovieItem,
  VideoShowItem,
  VideoWishRowBase,
} from './-video.types';

import { shortStamp } from './-wishlist.helpers';

/*
 * Ports of the vanilla video wishlist's pure functions. Each one is checked
 * against the frozen original in -video.helpers.differential.test.ts, so the
 * wording and the edge cases are the old page's, not a re-reading of it.
 */

/** the pill label per status (the vanilla STATUS map's labels). */
export const VIDEO_STATUS: Record<string, string> = {
  wanted: 'Wanted',
  searching: 'Searching',
  downloading: 'Downloading',
  downloaded: 'Done',
  failed: 'Failed',
  monitored: 'Not out yet',
  upgrade: '⇪ Upgrading',
  muted: 'Muted',
};

/**
 * the status that's actually true: an active download wins, then the upgrade
 * watch, then a muted show (its episodes aren't searched), then the column.
 */
export function liveStatus(it: VideoWishRowBase, showMuted?: boolean): string {
  if (it.downloading) return 'downloading';
  if (it.upgrade_from) return 'upgrade';
  if ((showMuted || it.muted) && (!it.status || it.status === 'wanted')) return 'muted';
  return it.status && VIDEO_STATUS[it.status] ? it.status : 'wanted';
}

/** what each source did on the last search, one line each, in chain order. */
export function sourceLines(row: VideoWishRowBase | null | undefined): string[] {
  const snap = row && row.search_snapshot;
  if (!snap || !snap.sources) return [];
  const order = snap.chain && snap.chain.length ? snap.chain : Object.keys(snap.sources);
  const out: string[] = [];
  for (const name of order) {
    const s = snap.sources[name];
    if (!s) continue;
    if (!s.ran) {
      out.push(`• ${name}: could not search — ${s.reason || 'unknown'}`);
      continue;
    }
    if (!s.results) {
      out.push(`• ${name}: found nothing`);
      continue;
    }
    if (s.accepted) {
      out.push(`• ${name}: ${s.accepted} usable of ${s.results}`);
      continue;
    }
    out.push(`• ${name}: ${s.results} found, none accepted${s.reason ? ` — ${s.reason}` : ''}`);
  }
  return out;
}

/** why a row keeps coming back empty: the count, then the per-source story. */
export function failWhy(row: VideoWishRowBase | null | undefined, fails: number): string {
  let out = `${fails} searches without a grab`;
  if (row && row.last_search_at) out += ` · last tried ${row.last_search_at}`;
  const per = sourceLines(row);
  if (per.length) out += `\n\n${per.join('\n')}`;
  else if (row && row.last_refusal) return `${out}\n${row.last_refusal}`;
  else return `${out} — try Search now, or a different quality profile`;
  return out;
}

/** repeated searches without a grab, and only while it's still being hunted. */
export function isFailingItem(x: VideoWishRowBase, showMuted?: boolean): boolean {
  const st = liveStatus(x, showMuted);
  return (Number(x.search_attempts) || 0) >= 3 && (st === 'wanted' || st === 'upgrade');
}

/**
 * ask for a right-sized image: ?w= on the poster proxy, a small /t/p/ bucket
 * on TMDB. full-size art behind 110px orbs was the old wishlist lag.
 */
export function sized<T extends string | null | undefined>(url: T, w: number): T {
  if (!url) return url;
  if (url.indexOf('/api/video/poster/') !== -1) {
    return `${url}${url.indexOf('?') === -1 ? '?' : '&'}w=${w}` as T;
  }
  if (url.indexOf('image.tmdb.org') !== -1) {
    const b = w <= 185 ? 185 : w <= 342 ? 342 : w <= 500 ? 500 : 780;
    return url.replace(/\/t\/p\/[^/]+\//, `/t/p/w${b}/`) as T;
  }
  return url;
}

/** youtube cdn art goes through our proxy (port of VideoYoutube.img). */
export function ytImg<T extends string | null | undefined>(url: T): T {
  if (!url) return url;
  let u: string = url;
  if (u.indexOf('//') === 0) u = `https:${u}`;
  if (/^https:\/\/([\w-]+\.)*(ytimg\.com|ggpht\.com|googleusercontent\.com)\//i.test(u))
    return `/api/video/img?u=${encodeURIComponent(u)}` as T;
  return u as T;
}

const MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "Mar 4" from an iso date, no timezone games (air dates are calendar dates). */
export function fmtDate(iso: string | null | undefined): string {
  const p = String(iso || '').split('-');
  if (p.length < 3) return '';
  return `${MO[(+p[1] || 1) - 1]} ${+p[2] || 1}`;
}

export function initials(s: string | null | undefined): string {
  const w = String(s || '')
    .replace(/[^A-Za-z0-9 ]/g, '')
    .split(' ')
    .filter(Boolean);
  const i = w
    .slice(0, 2)
    .map((x) => x[0])
    .join('');
  return (i || String(s || '?').slice(0, 2)).toUpperCase();
}

/** mm:ss / h:mm:ss (port of VideoYoutube.fmtDuration). */
export function fmtDuration(secIn: unknown): string {
  const sec = parseInt(String(secIn), 10);
  if (!sec || sec < 0) return '';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const mm = h && m < 10 ? `0${m}` : `${m}`;
  const ss = s < 10 ? `0${s}` : `${s}`;
  return `${h ? `${h}:${mm}` : `${m}`}:${ss}`;
}

/** 1.2K / 3.4M (port of VideoYoutube.compactCount). */
export function compactCount(nIn: unknown): string {
  const n = parseInt(String(nIn), 10);
  if (!n && n !== 0) return '';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1).replace(/\.0$/, '')}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}K`;
  return `${n}`;
}

/* ── the diagnostics drawer, as data ─────────────────────────────────────── */

export interface DiagLine {
  k: string;
  v: string;
  /** '' | 'gap' (a missing id) | 'live' (already downloading) */
  tone: '' | 'gap' | 'live';
}

export interface DiagSource {
  head: string;
  releases: { title: string; verdict: string; ok: boolean }[];
}

/** the drawer's facts and per-source releases (port of diagRowsHTML). */
export function diagModel(d: VideoDiagnostics): { lines: DiagLine[]; sources: DiagSource[] } {
  const row = d.row || {};
  const ids = d.ids || {};
  const dash = (v: string | number | null | undefined) => (v == null || v === '' ? '—' : String(v));
  const lines: DiagLine[] = [
    { k: 'Attempts', v: dash(`${row.search_attempts || 0} searches`), tone: '' },
    { k: 'Last tried', v: dash(row.last_search_at), tone: '' },
    { k: 'Latest reason', v: dash(row.last_refusal), tone: '' },
    { k: 'TMDB', v: dash(ids.tmdb_id), tone: '' },
    { k: 'TVDB', v: dash(ids.tvdb_id), tone: ids.tvdb_id ? '' : 'gap' },
    { k: 'IMDb', v: dash(ids.imdb_id), tone: ids.imdb_id ? '' : 'gap' },
    { k: 'Would land in', v: dash(d.target_dir), tone: '' },
  ];
  for (const dl of d.downloads || []) {
    lines.push({
      k: 'In flight',
      v: dash(
        (dl.status || '?') +
          (dl.progress ? ` · ${Math.round(dl.progress)}%` : '') +
          (dl.release_title ? ` · ${dl.release_title}` : ''),
      ),
      tone: 'live',
    });
  }
  const snap = row.search_snapshot || {};
  const srcs = snap.sources || {};
  const order = snap.chain && snap.chain.length ? snap.chain : Object.keys(srcs);
  const sources: DiagSource[] = [];
  for (const name of order) {
    const s = srcs[name];
    if (!s) continue;
    sources.push({
      head: `${name} — ${
        !s.ran
          ? `could not search: ${s.reason || 'unknown'}`
          : `${s.results || 0} found, ${s.accepted || 0} usable`
      }`,
      releases: (s.samples || []).map((x) => ({
        title: x.title || '?',
        verdict: x.accepted ? 'usable' : x.rejected || '—',
        ok: !!x.accepted,
      })),
    });
  }
  return { lines, sources };
}

/* ── zones: the music page's grouping, fed by video's own facts ──────────── */

export type VideoZoneKind =
  | 'unavailable'
  | 'refused'
  | 'notfound'
  | 'unsearchable'
  | 'failed'
  | 'downloading'
  | 'awaiting'
  | 'hunting'
  | 'upgrade'
  | 'notyet'
  | 'muted'
  | 'done';

/** worst first: the order zones render in and the order a show is filed by. */
export const VIDEO_ZONE_ORDER: VideoZoneKind[] = [
  'unavailable',
  'refused',
  'notfound',
  'unsearchable',
  'failed',
  'downloading',
  'awaiting',
  'hunting',
  'upgrade',
  'notyet',
  'muted',
  'done',
];

/** the zones that mean something is wrong, for the stuck count and the callout. */
export const VIDEO_STUCK_ZONES: ReadonlySet<VideoZoneKind> = new Set([
  'unavailable',
  'refused',
  'notfound',
  'unsearchable',
  'failed',
]);

const ZONE_COPY: Record<VideoZoneKind, { title: string; sub: string }> = {
  unavailable: { title: 'Unavailable', sub: 'deleted, private or members-only' },
  refused: { title: 'Found, but turned down', sub: 'a rule of yours refused every release' },
  notfound: { title: 'Nothing found', sub: 'no source had a release yet' },
  unsearchable: { title: "Couldn't search", sub: "a source isn't set up or is down" },
  failed: { title: 'Keeps coming back empty', sub: 'searched three times or more' },
  downloading: { title: 'Downloading now', sub: 'on the way' },
  awaiting: { title: 'Not posted yet', sub: "the title is out there, this one isn't yet" },
  hunting: { title: 'Still hunting', sub: 'in line for the next pass' },
  upgrade: { title: 'Upgrading', sub: 'owned, watching for a better copy' },
  notyet: { title: 'Not out yet', sub: "grabs automatically once it's released" },
  muted: { title: 'Not followed', sub: "the show is muted, so it isn't searched" },
  done: { title: 'Done', sub: 'downloaded' },
};

export function videoZoneCopy(kind: VideoZoneKind): { title: string; sub: string } {
  return ZONE_COPY[kind];
}

const TROUBLE_ZONE: Record<string, VideoZoneKind> = {
  refused: 'refused',
  notfound: 'notfound',
  unsearchable: 'unsearchable',
  awaiting: 'awaiting',
};

/** which zone one movie / episode / video belongs in. */
export function rowZone(
  row: VideoWishRowBase & { unavailable?: boolean },
  opts: { muted?: boolean; youtube?: boolean } = {},
): VideoZoneKind {
  const st = liveStatus(row, opts.muted);
  if (st === 'downloading') return 'downloading';
  if (opts.youtube && row.unavailable) return 'unavailable';
  if (isFailingItem(row, opts.muted)) {
    return (row.trouble && TROUBLE_ZONE[row.trouble]) || 'failed';
  }
  if (st === 'upgrade') return 'upgrade';
  if (st === 'monitored') return 'notyet';
  if (st === 'muted') return 'muted';
  if (st === 'downloaded') return 'done';
  return 'hunting';
}

export function showEpisodes(show: VideoShowItem): VideoEpisode[] {
  return (show.seasons || []).flatMap((season) => season.episodes || []);
}

/** a show / channel is filed under its worst episode. */
export function showZone(show: VideoShowItem): VideoZoneKind {
  const youtube = show.source === 'youtube';
  let worst = VIDEO_ZONE_ORDER.length - 1;
  for (const ep of showEpisodes(show)) {
    const z = rowZone(ep, { muted: show.muted, youtube });
    worst = Math.min(worst, VIDEO_ZONE_ORDER.indexOf(z));
  }
  return VIDEO_ZONE_ORDER[worst];
}

export function movieZone(movie: VideoMovieItem): VideoZoneKind {
  return rowZone(movie);
}

export interface VideoZone<T> {
  kind: VideoZoneKind;
  title: string;
  sub: string;
  items: T[];
}

const NOUN: Record<VideoMedia, [string, string]> = {
  movies: ['movie', 'movies'],
  tv: ['show', 'shows'],
  youtube: ['channel', 'channels'],
};

/** split items into zones, worst first, keeping the server's order inside each. */
export function videoZones<T>(
  items: T[],
  zoneOf: (item: T) => VideoZoneKind,
  media: VideoMedia,
): VideoZone<T>[] {
  const buckets = new Map<VideoZoneKind, T[]>();
  for (const item of items) {
    const kind = zoneOf(item);
    const list = buckets.get(kind) ?? [];
    list.push(item);
    buckets.set(kind, list);
  }
  return VIDEO_ZONE_ORDER.filter((kind) => buckets.has(kind)).map((kind) => {
    const zoneItems = buckets.get(kind)!;
    const [one, many] = NOUN[media];
    return {
      kind,
      title: ZONE_COPY[kind].title,
      sub: `${zoneItems.length} ${zoneItems.length === 1 ? one : many} · ${ZONE_COPY[kind].sub}`,
      items: zoneItems,
    };
  });
}

/* ── words for one row ───────────────────────────────────────────────────── */

const HEADLINE: Partial<Record<VideoZoneKind, string>> = {
  unavailable: 'Unavailable on YouTube',
  refused: 'Found, but turned down',
  notfound: 'Nothing found',
  unsearchable: "Couldn't search",
  failed: 'Keeps coming back empty',
  awaiting: 'Not posted yet',
  downloading: 'Downloading now',
  upgrade: 'Upgrading',
  notyet: 'Not out yet',
  muted: 'Not followed',
  done: 'Downloaded',
};

/**
 * the panel's sentence for one row: a headline from its zone, then the
 * evidence the drain kept (the refusal line, or what each source did).
 */
export function describeVideoRow(
  row: VideoWishRowBase & { unavailable?: boolean },
  opts: { muted?: boolean; youtube?: boolean } = {},
): { zone: VideoZoneKind; headline: string; detail: string } {
  const zone = rowZone(row, opts);
  const fails = Number(row.search_attempts) || 0;
  const last = shortStamp(row.last_search_at || '');
  const tries = fails
    ? `${fails} ${fails === 1 ? 'search' : 'searches'}${last ? `, last ${last}` : ''}.`
    : '';
  let detail = '';
  if (zone === 'unavailable') {
    detail = row.last_refusal || 'Deleted, private or members-only.';
  } else if (zone === 'unsearchable') {
    const lines = sourceLines(row).filter((l) => l.includes('could not search'));
    detail = lines.length
      ? lines.map((l) => l.replace(/^• /, '')).join('. ')
      : row.last_refusal || '';
  } else if (VIDEO_STUCK_ZONES.has(zone) || zone === 'awaiting') {
    detail =
      row.last_refusal ||
      (zone === 'notfound'
        ? 'None of your sources had a release yet.'
        : 'Try Search now, or a different quality profile.');
  } else if (zone === 'upgrade') {
    detail = `Own it in ${row.upgrade_from}, watching for a better copy.`;
  } else if (zone === 'notyet') {
    detail = "Not released yet, grabs automatically once it's out.";
  } else if (zone === 'muted') {
    detail = "The show isn't followed, so its episodes aren't searched.";
  } else if (zone === 'hunting') {
    detail = fails ? '' : 'Not searched yet.';
  }
  return {
    zone,
    headline: HEADLINE[zone] ?? 'Wanted',
    detail: [
      detail,
      VIDEO_STUCK_ZONES.has(zone) || zone === 'awaiting' || zone === 'hunting' ? tries : '',
    ]
      .filter(Boolean)
      .join(' ')
      .trim(),
  };
}

/* ── toasts, lifted out of the vanilla click handlers ────────────────────── */

export function searchNowToast(res: {
  queued?: number;
  total?: number;
  missing_target?: string | null;
}): { message: string; type: 'success' | 'info' | 'error' } {
  if (res.missing_target) {
    return {
      message: `Set the ${res.missing_target === 'movie' ? 'Movies' : 'TV'} library folder in Video Settings first`,
      type: 'error',
    };
  }
  if (res.queued) {
    return {
      message: `Searching for ${res.queued} item${res.queued === 1 ? '' : 's'}… grabs land in Downloads`,
      type: 'success',
    };
  }
  return {
    message: res.total
      ? 'Already downloading or being searched'
      : 'Nothing on the wishlist for that',
    type: 'info',
  };
}

export function retryToast(res: { queued?: number }): {
  message: string;
  type: 'success' | 'info';
} {
  return res.queued
    ? { message: 'Retrying with all sources — grabs land in Downloads', type: 'success' }
    : { message: 'Nothing queued for retry', type: 'info' };
}

export function searchAllToast(kinds: { movie?: string; episode?: string } = {}): {
  message: string;
  type: 'success' | 'info' | 'error';
} {
  const going: string[] = [];
  const missing: string[] = [];
  if (kinds.movie === 'started') going.push('movies');
  if (kinds.episode === 'started') going.push('episodes');
  if (kinds.movie === 'unconfigured') missing.push('Movies');
  if (kinds.episode === 'unconfigured') missing.push('TV');
  if (going.length) {
    return {
      message: `Searching wishlist ${going.join(' + ')} now — up to your Max downloads per run land in Downloads`,
      type: 'success',
    };
  }
  if (missing.length) {
    return {
      message: `Set the ${missing.join(' and ')} library folder${missing.length === 1 ? '' : 's'} in Video Settings first`,
      type: 'error',
    };
  }
  return {
    message:
      kinds.movie === 'busy' || kinds.episode === 'busy'
        ? 'A wishlist search is already running'
        : 'Nothing eligible to search right now',
    type: 'info',
  };
}

export function downloadAllToast(res: {
  queued?: number;
  started?: number;
  refused?: number;
  already?: number;
  unavailable?: number;
}): { message: string; type: 'success' | 'info' } {
  let message: string;
  if (res.queued) {
    message = `Queued ${res.queued} video${res.queued === 1 ? '' : 's'} — ${res.started} downloading now, the rest drain automatically`;
  } else if (res.refused) {
    message = `Nothing queued — the disk-space guard held ${res.refused} back`;
  } else if (res.already) {
    message = `${res.already} already downloading`;
  } else if (res.unavailable) {
    message = `Nothing to queue — ${res.unavailable} video(s) are unavailable (deleted, private or members-only)`;
  } else {
    message = 'Nothing waiting to download';
  }
  return { message, type: res.queued ? 'success' : 'info' };
}

/** "Movies" / "TV episodes" / "YouTube videos" for the clear-all confirm. */
export function clearLabel(media: VideoMedia): string {
  return media === 'movies' ? 'movies' : media === 'tv' ? 'TV episodes' : 'YouTube videos';
}

/** the empty-state title per tab (the vanilla page's wording). */
export function emptyTitle(media: VideoMedia, searching: boolean): string {
  if (searching) return 'No matches';
  if (media === 'movies') return 'No movies on your wishlist yet';
  if (media === 'tv') return 'No TV episodes on your wishlist yet';
  return 'No channels followed yet — paste a channel link on the Search page';
}

/** whether a show still needs its art backfilled (the vanilla rule). */
export function showsNeedArt(items: VideoShowItem[]): boolean {
  return items.some((sh) =>
    (sh.seasons || []).some(
      (se) => !se.poster_url || (se.episodes || []).some((e) => !e.still_url || !e.overview),
    ),
  );
}

export function moviesNeedArt(items: VideoMovieItem[]): boolean {
  return items.some((it) => !it.poster_url);
}

/* ── captions under an orb or poster ─────────────────────────────────────── */

function latestStamp(rows: VideoWishRowBase[]): string {
  let best = '';
  for (const r of rows) {
    const s = r.last_search_at || '';
    if (s > best) best = s;
  }
  return shortStamp(best);
}

/** the quiet line under a show / channel orb: what's wrong, else how much is wanted. */
export function showMeta(show: VideoShowItem): string {
  const youtube = show.source === 'youtube';
  const eps = showEpisodes(show);
  const stuck = eps.filter((e) =>
    VIDEO_STUCK_ZONES.has(rowZone(e, { muted: show.muted, youtube })),
  );
  if (stuck.length) {
    const head =
      eps.length === 1
        ? stuck[0].unavailable && youtube
          ? 'unavailable'
          : `${Number(stuck[0].search_attempts) || 0} tries`
        : `${stuck.length} of ${eps.length} stuck`;
    const when = latestStamp(stuck);
    return when ? `${head} · ${when}` : head;
  }
  const zone = showZone(show);
  const noun = youtube ? 'video' : 'episode';
  if (zone === 'downloading') {
    const n = eps.filter((e) => liveStatus(e, show.muted) === 'downloading').length;
    return `${n} downloading`;
  }
  if (zone === 'notyet') return 'not out yet';
  if (zone === 'muted') return 'not followed';
  if (zone === 'done') return 'all done';
  const wanted = show.wanted || eps.length;
  return `${wanted} ${wanted === 1 ? noun : `${noun}s`}${show.done ? ` · ${show.done} done` : ''}`;
}

/** the line under a movie poster. */
export function movieMeta(movie: VideoMovieItem): string {
  const zone = movieZone(movie);
  let tail: string;
  if (VIDEO_STUCK_ZONES.has(zone)) {
    const when = shortStamp(movie.last_search_at || '');
    tail = `${Number(movie.search_attempts) || 0} tries${when ? ` · ${when}` : ''}`;
  } else if (zone === 'downloading') tail = 'downloading';
  else if (zone === 'upgrade') tail = `⇪ from ${movie.upgrade_from}`;
  else if (zone === 'notyet') tail = 'not out yet';
  else if (zone === 'done') tail = 'done';
  else tail = movie.library_id != null ? 'in library' : 'wanted';
  return movie.year ? `${movie.year} · ${tail}` : tail;
}

/** "keep only the stuck bits": the failing filter, as the vanilla page applied it. */
export function failingOnlyMovies(items: VideoMovieItem[]): VideoMovieItem[] {
  return items.filter((it) => isFailingItem(it));
}

export function failingOnlyShows(items: VideoShowItem[]): VideoShowItem[] {
  return items
    .map((sh) => {
      const seasons = (sh.seasons || [])
        .map((se) => {
          const eps = (se.episodes || []).filter((e) => isFailingItem(e, sh.muted));
          return eps.length ? { ...se, episodes: eps } : null;
        })
        .filter((se): se is NonNullable<typeof se> => se !== null);
      return seasons.length ? { ...sh, seasons } : null;
    })
    .filter((sh): sh is VideoShowItem => sh !== null);
}
