/**
 * The Server Manager tab's pure core + wire calls (pages-extra.js, whole file).
 *
 * This tab has NO engine coupling: it never touches downloads.js,
 * spotifyPlaylists or the discovery machinery, so unlike the account tabs it
 * needs no window bridge. `_serverEditorState` is script-scoped but only this
 * one file reads it, so it becomes ordinary React state.
 */

export interface ServerPlaylist {
  id: string;
  name: string;
  track_count?: number;
  /** Set by the split below, not by the backend. */
  _synced?: boolean;
}

export interface ServerPlaylistsResponse {
  success?: boolean;
  error?: string;
  server_type?: string;
  playlists?: ServerPlaylist[];
}

/**
 * Split the server's playlists into SYNCED and Other (59-73).
 *
 * A playlist counts as synced when its name appears in the mirrored list OR in
 * the sync history, matched trimmed and lower-cased. The returned order is
 * synced-then-unsynced, which is also the order `_serverPlaylists` keeps (75).
 */
export function splitServerPlaylists(
  playlists: readonly ServerPlaylist[],
  mirroredNames: readonly string[],
  historyNames: readonly string[],
): { synced: ServerPlaylist[]; unsynced: ServerPlaylist[] } {
  const key = (s: string) => s.trim().toLowerCase();
  const mirrored = new Set(mirroredNames.map(key));
  const history = new Set(historyNames.map(key));
  const synced: ServerPlaylist[] = [];
  const unsynced: ServerPlaylist[] = [];
  for (const pl of playlists) {
    const k = key(pl.name);
    if (mirrored.has(k) || history.has(k)) synced.push({ ...pl, _synced: true });
    else unsynced.push({ ...pl, _synced: false });
  }
  return { synced, unsynced };
}

/** 'Server Playlists (Plex)' — the server type, first letter upper (76-78). */
export function serverTabTitle(serverType: string | null | undefined): string {
  const name = serverType ? serverType.charAt(0).toUpperCase() + serverType.slice(1) : '';
  return `Server Playlists (${name})`;
}

/**
 * The card's hue, cycled per index (94). Cards continue the sequence across the
 * two sections — the Other section starts at synced.length (145), so the two
 * grids never repeat a colour side by side.
 */
export function serverCardHue(index: number): number {
  return (index * 37 + 200) % 360;
}

/**
 * ms → m:ss, or '' for nothing (_formatDurationMs, 484).
 *
 * ROUNDS to the nearest second — not floor. 1500ms reads '0:02' here where the
 * page's other duration helpers would say '0:01'. Transcribed, not unified.
 */
export function formatDurationMs(ms: number | null | undefined): string {
  if (!ms) return '';
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

/* ── Wire calls ───────────────────────────────────────────────────────────── */

/**
 * The three PARALLEL loads (41-52). The mirrored and history responses are
 * each tolerated independently: the vanilla wraps both in try/catch and falls
 * back to [], so one failing endpoint still renders the list — it just cannot
 * mark anything synced.
 */
export async function fetchServerPlaylistData(): Promise<{
  data: ServerPlaylistsResponse;
  mirroredNames: string[];
  historyNames: string[];
}> {
  const [serverRes, mirroredRes, historyRes] = await Promise.all([
    fetch('/api/server/playlists'),
    fetch('/api/mirrored-playlists'),
    fetch('/api/sync/history/names'),
  ]);
  const data = (await serverRes.json()) as ServerPlaylistsResponse;

  let mirroredNames: string[] = [];
  try {
    const mirrored = await mirroredRes.json();
    if (Array.isArray(mirrored)) {
      mirroredNames = (mirrored as { name?: string }[]).map((p) => p.name ?? '');
    }
  } catch {
    // 48: a failed mirrored fetch must not sink the list.
  }

  let historyNames: string[] = [];
  try {
    const history = await historyRes.json();
    if (Array.isArray(history)) historyNames = history as string[];
  } catch {
    // 51: same tolerance for the history names.
  }

  return { data, mirroredNames, historyNames };
}

/** The mirrored rows a server playlist's NAME matches (openServerPlaylistEditor, 158). */
export interface MirroredMatch {
  id: number;
  name: string;
  source?: string;
  owner?: string;
  track_count?: number;
  updated_at?: string;
  mirrored_at?: string;
}

/**
 * A failed lookup is SWALLOWED (168-170): the vanilla logs and carries on with
 * an empty list, which lands the user in the server-only view rather than an
 * error. Reproduced — throwing here would change a working path into a dead end.
 *
 * Declared divergence: `p.name` is guarded. The vanilla calls .trim() on it
 * unguarded (164), so a nameless row would throw and take the whole lookup with
 * it into that same empty-list catch.
 */
export async function fetchMirroredMatches(playlistName: string): Promise<MirroredMatch[]> {
  try {
    const response = await fetch('/api/mirrored-playlists');
    const rows = await response.json();
    if (!Array.isArray(rows)) return [];
    const key = playlistName.trim().toLowerCase();
    return (rows as MirroredMatch[]).filter((p) => (p.name ?? '').trim().toLowerCase() === key);
  } catch {
    return [];
  }
}

/**
 * selectDisambigPlaylist re-fetches the FULL mirrored playlist by id before
 * opening the compare view (237-239) — the disambiguation list rows are only
 * list entries, and the compare view needs the whole object.
 */
export async function fetchMirroredPlaylistById(mirroredId: number): Promise<MirroredMatch> {
  const response = await fetch(`/api/mirrored-playlists/${mirroredId}`);
  return (await response.json()) as MirroredMatch;
}

/* ── The compare editor (247-354, 356-383, 490-583) ───────────────────────── */

export interface CompareTrack {
  /**
   * 'matched' | 'missing' | 'extra' in practice, but typed as a string: the
   * value is echoed into a CSS class and a data-status attribute (508), and the
   * filter compares against it directly, so an unrecognised status must flow
   * through rather than be narrowed away.
   */
  match_status: string;
  confidence?: number | null;
  source_track?: {
    position?: number;
    name?: string;
    artist?: string;
    image_url?: string;
    duration_ms?: number;
  } | null;
  server_track?: {
    id?: string;
    title?: string;
    artist?: string;
    thumb?: string;
    duration?: number;
  } | null;
}

export interface CompareResponse {
  success?: boolean;
  error?: string;
  server_type?: string;
  server_track_count?: number;
  source_track_count?: number;
  order_status?: { out_of_order?: boolean } | null;
  server_order?: unknown[];
  tracks?: CompareTrack[];
}

/**
 * The source-column icons (313) — the SAME six as the disambiguation modal
 * (193), deliberately kept as one table because they are one table in the
 * vanilla too. Distinct from the SERVER icons below and from the mirrored tab's.
 */
export const COMPARE_SOURCE_ICONS: Readonly<Record<string, string>> = {
  spotify: '🟢',
  tidal: '🌊',
  youtube: '▶️',
  beatport: '🎛️',
  deezer: '🟣',
  file: '📄',
};

/** The server-column icons (314) — a third, smaller table. */
export const COMPARE_SERVER_ICONS: Readonly<Record<string, string>> = {
  plex: '🟠',
  jellyfin: '🟣',
  navidrome: '🔵',
};

/** 323: no mirrored playlist at all also falls back to the clipboard. */
export function compareSourceIcon(source: string | null | undefined): string {
  return COMPARE_SOURCE_ICONS[source ?? ''] ?? '📋';
}

/** 326: an unknown server type gets the laptop, not the Plex mark. */
export function compareServerIcon(serverType: string | null | undefined): string {
  return COMPARE_SERVER_ICONS[serverType ?? ''] ?? '💻';
}

/** 298 / 312 — 'Server' and 'Source' are the fallbacks, not empty strings. */
export function compareServerLabel(serverType: string | null | undefined): string {
  return serverType ? serverType.charAt(0).toUpperCase() + serverType.slice(1) : 'Server';
}

export function compareSourceLabel(
  source: string | null | undefined,
  hasMirrored: boolean,
): string {
  if (!hasMirrored) return 'Source';
  const s = source || 'source';
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export interface CompareStats {
  matched: number;
  missing: number;
  extra: number;
  total: number;
}

/** The three counts every label in the editor is derived from (356-359). */
export function compareStats(tracks: readonly CompareTrack[]): CompareStats {
  return {
    matched: tracks.filter((t) => t.match_status === 'matched').length,
    missing: tracks.filter((t) => t.match_status === 'missing').length,
    extra: tracks.filter((t) => t.match_status === 'extra').length,
    total: tracks.length,
  };
}

/**
 * The footer line (382). The denominator is matched+missing — NOT the total —
 * so extra server tracks never dilute the ratio; they get their own clause.
 */
export function compareFooterText(stats: CompareStats): string {
  const base = `${stats.matched}/${stats.matched + stats.missing} matched`;
  return stats.extra > 0 ? `${base} · ${stats.extra} extra on server` : base;
}

/** The four pill labels (373-378). */
export function compareFilterLabel(filter: string, stats: CompareStats): string {
  if (filter === 'all') return `All (${stats.total})`;
  if (filter === 'matched') return `Matched (${stats.matched})`;
  if (filter === 'missing') return `Missing (${stats.missing})`;
  if (filter === 'extra') return `Extra (${stats.extra})`;
  return filter;
}

/** The header meta line (301). */
export function compareMetaText(data: CompareResponse): string {
  return (
    `${compareServerLabel(data.server_type)} · ` +
    `${data.server_track_count || 0} server tracks · ` +
    `${data.source_track_count || 0} source tracks`
  );
}

/**
 * The confidence badge (534-540). It renders ONLY for a matched row that
 * carries a confidence — null is different from 0 here, which is why the
 * vanilla tests `!= null` rather than truthiness.
 */
export function compareConfidenceBadge(
  track: CompareTrack,
): { percent: number; className: string } | null {
  if (track.match_status !== 'matched') return null;
  if (track.confidence == null) return null;
  const percent = Math.round(track.confidence * 100);
  const className = percent >= 100 ? 'exact' : percent >= 90 ? 'high' : 'fuzzy';
  return { percent, className };
}

/** The 'Find & add' hint under a missing row (566). */
export function compareMissingHint(track: CompareTrack): string {
  const src = track.source_track;
  return src ? `${src.artist || ''} — ${src.name}` : '';
}

/** GET the compare payload (276-283). */
export async function fetchComparePlaylist(
  playlistId: string,
  playlistName: string,
  mirroredId?: number,
): Promise<CompareResponse> {
  let url = `/api/server/playlist/${playlistId}/tracks?name=${encodeURIComponent(playlistName)}`;
  if (mirroredId) url += `&mirrored_playlist_id=${mirroredId}`;
  return (await (await fetch(url)).json()) as CompareResponse;
}
