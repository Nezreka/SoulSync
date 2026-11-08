/**
 * Pure helpers for the video downloads view — a straight port of the logic
 * that lived in webui/static/video/video-downloads-page.js, so the React
 * rewrite behaves identically and every branch is unit-testable.
 */

import type { VdlBlockEntry, VdlDownload, VdlFilter, VdlSearchCtx, VdlView } from './-vdl.types';

import { VDL_ACTIVE_STATUSES, VDL_FAILED_STATUSES, VDL_REMOVABLE_STATUSES } from './-vdl.types';

/* ── kinds / statuses ──────────────────────────────────────────────────── */

/** Collapse the kinds into the three colour groups (movie / tv / youtube). */
export function vdlType(kind: string | null | undefined): 'movie' | 'tv' | 'youtube' {
  const k = (kind || '').toLowerCase();
  return k === 'youtube' ? 'youtube' : k === 'movie' ? 'movie' : 'tv';
}

/**
 * The row's real type. a YouTube video grabbed from the download panel is
 * saved with kind 'movie' (the grab form's default), so kind alone called it
 * a movie and its card offered "Open movie". the source says youtube.
 */
export function vdlRowType(
  d: Pick<VdlDownload, 'kind' | 'media_source' | 'source'>,
): 'movie' | 'tv' | 'youtube' {
  if (d.media_source === 'youtube' || d.source === 'youtube') return 'youtube';
  return vdlType(d.kind);
}

export const VDL_KIND_ICON: Record<string, string> = {
  movie: '🎬',
  show: '📺',
  episode: '📺',
  season: '📺',
  series: '📺',
  youtube: '▶️',
};

export function vdlKindIcon(kind: string | null | undefined): string {
  return VDL_KIND_ICON[(kind || '').toLowerCase()] || '🎬';
}

/** Status → label + the music .adl-row-/.adl-status-dot class it reuses. */
export const VDL_STATUS: Record<string, { label: string; cls: string }> = {
  downloading: { label: 'Downloading', cls: 'active' },
  queued: { label: 'Queued', cls: 'queued' },
  searching: { label: 'Searching', cls: 'active' },
  importing: { label: 'Importing', cls: 'active' },
  completed: { label: 'Completed', cls: 'completed' },
  failed: { label: 'Failed', cls: 'failed' },
  import_failed: { label: 'Import failed', cls: 'failed' },
  cancelled: { label: 'Cancelled', cls: 'cancelled' },
};

export function vdlStatusOf(status: string | null | undefined): { label: string; cls: string } {
  return VDL_STATUS[status || ''] || VDL_STATUS.downloading;
}

export function vdlIsActive(status: string | null | undefined): boolean {
  return (VDL_ACTIVE_STATUSES as readonly string[]).includes(status || '');
}

export function vdlIsFail(status: string | null | undefined): boolean {
  return (VDL_FAILED_STATUSES as readonly string[]).includes(status || '');
}

/** A failed or cancelled row: 'Remove from list' and 'Clear failed' take it. */
export function vdlIsRemovable(status: string | null | undefined): boolean {
  return (VDL_REMOVABLE_STATUSES as readonly string[]).includes(status || '');
}

/** The old page's pill matching: 'all' | 'active' | 'completed' | 'failed'. */
export function vdlMatchesFilter(status: string | null | undefined, filter: VdlFilter): boolean {
  return (
    filter === 'all' ||
    (filter === 'active' && vdlIsActive(status)) ||
    (filter === 'completed' && status === 'completed') ||
    (filter === 'failed' && vdlIsFail(status))
  );
}

/** Which of the three top-level views a value belongs to. */
export function vdlViewOf(view: VdlView): VdlView {
  return view;
}

/* ── search_ctx ────────────────────────────────────────────────────────── */

/** The download's search_ctx (peer/season/episode/channel/…) — string or object. */
export function vdlParseCtx(d: Pick<VdlDownload, 'search_ctx'>): VdlSearchCtx {
  try {
    const raw = d.search_ctx;
    if (!raw) return {};
    const parsed = (typeof raw === 'string' ? JSON.parse(raw) : raw) as Record<string, unknown>;
    const num = (v: unknown): number | null => {
      if (v == null || v === '') return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };
    return {
      season: num(parsed.season),
      episode: num(parsed.episode),
      scope: typeof parsed.scope === 'string' ? parsed.scope : null,
      peer: (parsed.peer as VdlSearchCtx['peer']) ?? null,
      channel: typeof parsed.channel === 'string' ? parsed.channel : null,
      channel_title: typeof parsed.channel_title === 'string' ? parsed.channel_title : null,
      channel_id: typeof parsed.channel_id === 'string' ? parsed.channel_id : null,
      published_at: typeof parsed.published_at === 'string' ? parsed.published_at : null,
      description: typeof parsed.description === 'string' ? parsed.description : null,
    };
  } catch {
    return {};
  }
}

/* ── season-pack grouping ───────────────────────────────────────────────── */

/**
 * A season PACK belongs to the same group as the episodes it produces: it is
 * the batch. `is_pack` comes from the server; the search_ctx fallback only
 * covers a payload from an older build.
 */
export function vdlIsPackRow(d: VdlDownload): boolean {
  if (typeof d.is_pack === 'boolean') return d.is_pack;
  const c = vdlParseCtx(d);
  const scope = (c.scope || '').toLowerCase();
  if (scope === 'season' || scope === 'series') return true;
  return (d.kind || '').toLowerCase() === 'show' && c.season != null && c.episode == null;
}

/** Downloads of the same show + season collapse into one parent card. */
export function vdlGroupKey(d: VdlDownload): string | null {
  if ((d.kind || '').toLowerCase() !== 'show') return null;
  const c = vdlParseCtx(d);
  if (c.season == null) return null;
  if (c.episode == null && !vdlIsPackRow(d)) return null;
  return `g:${d.media_id ?? d.title ?? '?'}:s${c.season}`;
}

/** Split a group's rows into its batch head (the pack, if any) and the episode cards. */
export function vdlSplitGroup(members: VdlDownload[]): {
  pack: VdlDownload | null;
  episodes: VdlDownload[];
} {
  let pack: VdlDownload | null = null;
  const episodes: VdlDownload[] = [];
  for (const d of members || []) {
    if (!pack && vdlIsPackRow(d)) pack = d;
    else episodes.push(d);
  }
  return { pack, episodes };
}

/**
 * A group is worth drawing when it has ≥2 rows, OR when a pack is present —
 * a pack that has downloaded but imported nothing yet is still a batch, and
 * showing it as a bare card was the bug this fixes.
 */
export function vdlGroupWorthy(members: VdlDownload[]): boolean {
  return members.length >= 2 || vdlSplitGroup(members).pack !== null;
}

/* ── formatting ────────────────────────────────────────────────────────── */

export function vdlFmtSize(bytes: number | null | undefined): string {
  const gb = (bytes || 0) / (1024 * 1024 * 1024);
  return gb >= 0.1
    ? `${Math.round(gb * 10) / 10} GB`
    : `${Math.round((bytes || 0) / (1024 * 1024))} MB`;
}

export function vdlFmtElapsed(ms: number | null | undefined): string {
  const total = Math.floor((ms || 0) / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m ? `${m}m${s < 10 ? '0' : ''}${s}s` : `${s}s`;
}

export function vdlFmtRuntime(minutes: number | null | undefined): string {
  const m = parseInt(String(minutes), 10);
  if (!m) return '';
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return h ? `${h}h${mm ? ` ${mm}m` : ''}` : `${mm}m`;
}

export function vdlFmtViews(n: number | null | undefined): string {
  const v = Number(n) || 0;
  if (v >= 1e6) return `${Math.round(v / 1e5) / 10}M`;
  if (v >= 1e3) return `${Math.round(v / 100) / 10}K`;
  return String(v);
}

export function vdlFmtSpeed(bps: number | null | undefined): string {
  const b = Number(bps) || 0;
  if (!b) return '';
  return b >= 1e6 ? `${Math.round(b / 1e5) / 10} MB/s` : `${Math.max(1, Math.round(b / 1e3))} KB/s`;
}

/** Remaining-time estimate from the monitor (slskd averageSpeed / client eta). */
export function vdlFmtEta(secs: number | null | undefined): string {
  const s = parseInt(String(secs), 10);
  if (!s || s <= 0 || Number.isNaN(s)) return '';
  if (s < 60) return `~${s}s left`;
  if (s < 3600) return `~${Math.round(s / 60)}m left`;
  return `~${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m left`;
}

export function vdlPad2(n: number | null | undefined): string {
  const v = parseInt(String(n), 10) || 0;
  return (v < 10 ? '0' : '') + v;
}

/** Human age for the recycle bin ("age_seconds" → "3h ago"). */
export function vdlFmtAge(sec: number | null | undefined): string {
  if (sec == null) return 'age unknown';
  const s = Number(sec);
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** Byte size with units, for the recycle bin ("—" when unknown). */
export function vdlFmtBytes(bytes: number | null | undefined): string {
  const b = Number(bytes) || 0;
  if (b <= 0) return '';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = b;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

/* ── meta URLs ─────────────────────────────────────────────────────────── */

/**
 * The lazy detail URL for an expanded card: TMDB for movie/TV, the cached
 * YouTube metadata for youtube rows. Null for owned re-grabs (media_id isn't
 * a TMDB id there) — the drawer renders the download facts without it.
 */
export function vdlMetaURL(d: VdlDownload): string | null {
  const t = vdlRowType(d);
  if (t === 'youtube')
    return `/api/video/downloads/yt-meta/${encodeURIComponent(String(d.media_id))}`;
  if (d.media_source === 'library') return null;
  const kind = t === 'movie' ? 'movie' : 'show';
  let url = `/api/video/downloads/meta/${kind}/${encodeURIComponent(String(d.media_id))}`;
  if (d.kind === 'episode') {
    const c = vdlParseCtx(d);
    if (c.season != null && c.episode != null)
      url += `?season=${encodeURIComponent(c.season)}&episode=${encodeURIComponent(c.episode)}`;
  }
  return url;
}

/* ── history modal formatting ──────────────────────────────────────────── */

/** History-row size: 1 decimal only at GB and up (the history modal's rule). */
export function vdlFmtHistorySize(b: number | null | undefined): string {
  const v = Number(b) || 0;
  if (v <= 0) return '';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(u.length - 1, Math.floor(Math.log(v) / Math.log(1024)));
  return `${(v / Math.pow(1024, i)).toFixed(i >= 3 ? 1 : 0)} ${u[i]}`;
}

const VDL_MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/** "2026-10-11 14:30:00" → "Oct 11, 2026 · 14:30". */
export function vdlFmtWhen(iso: string | null | undefined): string {
  if (!iso) return '';
  const p = String(iso).replace('T', ' ').split(/[- :]/);
  if (p.length < 3) return String(iso);
  const mo = VDL_MONTHS[(+p[1] || 1) - 1];
  const t = p.length >= 5 ? ` · ${p[3]}:${p[4]}` : '';
  return `${mo} ${+p[2]}, ${p[0]}${t}`;
}

/** "2026-10-11" — the day-grouping key for the timeline. */
export function vdlDayKey(iso: string | null | undefined): string {
  const p = String(iso || '')
    .replace('T', ' ')
    .split(/[- :]/);
  if (p.length < 3) return '';
  return `${p[0]}-${p[1]}-${p[2]}`;
}

/** "2026-10-11 14:30:00" → "Oct 11, 2026" (the day header). */
export function vdlDayLabel(iso: string | null | undefined): string {
  const p = String(iso || '')
    .replace('T', ' ')
    .split(/[- :]/);
  if (p.length < 3) return 'Earlier';
  return `${VDL_MONTHS[(+p[1] || 1) - 1]} ${+p[2]}, ${p[0]}`;
}

/** History outcome → [label, css class]. */
export function vdlHistoryOutcome(outcome: string | null | undefined): [string, string] {
  switch (outcome) {
    case 'import_failed':
      return ['Needs import', 'vdh-oc--warn'];
    case 'failed':
      return ['Failed', 'vdh-oc--fail'];
    case 'cancelled':
      return ['Cancelled', 'vdh-oc--muted'];
    default:
      return ['Imported', 'vdh-oc--ok'];
  }
}

/* ── row text ──────────────────────────────────────────────────────────── */

/** The collapsed card's title — episodes disambiguate with SxxExx. */
export function vdlCardTitle(d: VdlDownload): string {
  const ctx = vdlParseCtx(d);
  const base = d.title || d.release_title || 'Download';
  if (ctx.season != null && ctx.episode != null) {
    return `${d.title || d.release_title || 'Episode'} · S${vdlPad2(ctx.season)}E${vdlPad2(ctx.episode)}`;
  }
  return base + (d.year ? `  (${d.year})` : '');
}

/** "Movies" etc. can't be re-grabbed when cancelled deliberately / import-failed / YouTube. */
export function vdlIsRetryable(d: VdlDownload): boolean {
  return d.status === 'failed' && Boolean(d.username) && Boolean(d.filename);
}

/** The block-release button: failed/import_failed, not YouTube, known release. */
export function vdlCanBlock(d: VdlDownload): boolean {
  return (
    (d.status === 'failed' || d.status === 'import_failed') &&
    vdlType(d.kind) !== 'youtube' &&
    Boolean(d.username) &&
    Boolean(d.filename)
  );
}

/** Strip the timestamp prefix the bin names carry ("20260101_120000_(1)_name" → "name"). */
export function vdlBinDisplayName(name: string): string {
  return name.replace(/^\d{8}_\d{6}_(\(\d+\)_)?/, '');
}

/** Blocklist row's "what" line: release title, filename, uploader, or unknown. */
export function vdlBlockWhat(b: VdlBlockEntry): string {
  return b.release_title || b.filename || b.username || 'Unknown release';
}

/** Blocklist row's "S01E02"-style suffix. */
export function vdlBlockSxe(b: VdlBlockEntry): string {
  return b.season_number != null && b.episode_number != null
    ? `S${vdlPad2(b.season_number)}E${vdlPad2(b.episode_number)}`
    : '';
}
