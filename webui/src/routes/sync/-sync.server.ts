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
    /**
     * Both are read ONLY by the Find & Add body (924, 929) — nothing renders
     * them. They carry the durable manual match (#787) back to the backend.
     */
    source_track_id?: string;
    source?: string;
  } | null;
  server_track?: {
    id?: string;
    title?: string;
    artist?: string;
    thumb?: string;
    duration?: number;
    /**
     * Written by the in-place patch (953) and never rendered by this tab. Kept
     * because the patched object is the same shape a later full load returns.
     */
    album?: string;
  } | null;
  /** Set by the in-place patch only (959); no renderer reads it. */
  override?: boolean;
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

/* ── Search / Replace / Remove (746-1020) ─────────────────────────────────── */

export type ServerSearchMode = 'replace' | 'add';

/** A /api/library/search-tracks row (web_server.py 22459-22470). */
export interface LibrarySearchTrack {
  /** A database row id — a NUMBER over the wire, which is why every comparison
   *  and every request body coerces it with String() (863, 947). */
  id: string | number;
  title?: string;
  artist_name?: string;
  album_title?: string;
  album_thumb_url?: string;
  file_path?: string;
  bitrate?: number;
  duration?: number;
}

export interface LibrarySearchResponse {
  success?: boolean;
  error?: string;
  tracks?: LibrarySearchTrack[];
}

/** The three write endpoints all answer in this shape (web_server.py 22099/22290/22383). */
export interface ServerMutationResponse {
  success?: boolean;
  message?: string;
  error?: string;
  /** Plex DELETES AND RECREATES the playlist, so the id can change under us (939). */
  new_playlist_id?: string;
}

/** 771: the same overlay serves both entry points, titled by mode. */
export function searchModalTitle(mode: ServerSearchMode): string {
  return mode === 'replace' ? 'Swap Track' : 'Add Track to Server';
}

export interface SearchSeed {
  query: string;
  contextArtist: string;
  contextName: string;
}

/**
 * What the overlay opens with (750-759).
 *
 * The query is the track NAME ALONE — deliberately, per the comment at 752: an
 * "artist title" blob searches worse than a title. The artist is kept
 * separately and sent as a relevance HINT (the backend ranks with it, it does
 * not filter — web_server.py 22424-22426), which is what stops "bad guy" by
 * Billie Eilish being buried under same-title tracks by other artists.
 *
 * The source side wins on every field, with the server side as the fallback;
 * note the query falls back on an EMPTY src.name, not merely a missing one.
 */
export function searchSeed(track: CompareTrack): SearchSeed {
  const src = track.source_track ?? {};
  const svr = track.server_track ?? {};
  return {
    query: src.name ? src.name.trim() : (svr.title || '').trim(),
    contextArtist: src.artist || svr.artist || '',
    contextName: src.name || svr.title || '',
  };
}

/** 859: only these seven extensions get a badge, and M4A is shown as AAC. */
const SEARCH_FORMATS = ['FLAC', 'MP3', 'OPUS', 'OGG', 'M4A', 'AAC', 'WAV'];

export function searchFormatBadge(filePath: string | null | undefined): string {
  const ext = (filePath || '').split('.').pop()?.toUpperCase() ?? '';
  if (!SEARCH_FORMATS.includes(ext)) return '';
  return ext === 'M4A' ? 'AAC' : ext;
}

/** 861. */
export function searchBitrateText(bitrate: number | null | undefined): string {
  return bitrate ? `${bitrate}k` : '';
}

/** 855 — pluralised, and 1 result really does read '1 result'. */
export function searchResultsHeaderText(count: number): string {
  return `${count} result${count !== 1 ? 's' : ''}`;
}

/**
 * Where a Find & Add lands on the server (908-911).
 *
 * The compare columns are in SOURCE order and missing rows occupy a slot on the
 * left with nothing on the right, so the server-side index is NOT the row
 * index: it is the count of rows before this one that actually have a server
 * track.
 */
export function addTrackPosition(tracks: readonly CompareTrack[], trackIndex: number): number {
  let position = 0;
  for (let k = 0; k < trackIndex; k++) {
    if (tracks[k]?.server_track) position++;
  }
  return position;
}

/**
 * The in-place patch after a successful swap/add (946-967).
 *
 * #1005: one Find & Add used to re-fetch and re-match the whole playlist,
 * throwing a 2,000-track compare back to 'Loading comparison...'. The write
 * already succeeded and the picked library track IS the server track (same id
 * space), so the pair is patched instead. Order status and the header counts
 * deliberately go stale until the next full open.
 *
 * The link case at the end is the subtle one: the picked track may ALREADY sit
 * in the list as an 'extra' row, because the backend links rather than
 * duplicating. That row is dropped, or the track shows twice.
 *
 * Declared divergence: the vanilla mutates the row objects and the array in
 * place; this returns new ones because React re-renders off identity. The
 * resulting list is the same either way.
 */
export function applyPickedTrack(
  tracks: readonly CompareTrack[],
  trackIndex: number,
  newTrackId: string,
  picked: LibrarySearchTrack,
): CompareTrack[] {
  const track = tracks[trackIndex];
  if (!track) return tracks.slice();
  const next = tracks.slice();
  next[trackIndex] = {
    ...track,
    server_track: {
      id: String(newTrackId),
      title: picked.title || '',
      artist: picked.artist_name || '',
      album: picked.album_title || '',
      duration: picked.duration || 0,
      thumb: picked.album_thumb_url || '',
    },
    match_status: 'matched',
    confidence: 1.0,
    override: true,
  };
  const dupIndex = next.findIndex(
    (row, index) =>
      index !== trackIndex &&
      !row.source_track &&
      row.server_track &&
      String(row.server_track.id) === String(newTrackId),
  );
  if (dupIndex >= 0) next.splice(dupIndex, 1);
  return next;
}

/**
 * The in-place patch after a successful remove (1006-1012).
 *
 * A matched pair keeps its source side and becomes 'missing' — the row stays,
 * so the two columns stay paired. An extra row has no source side to keep, so
 * it goes entirely.
 */
export function applyRemovedTrack(
  tracks: readonly CompareTrack[],
  trackIndex: number,
): CompareTrack[] {
  const track = tracks[trackIndex];
  // The vanilla's splice() on an out-of-range index removes nothing, so an
  // unchanged list is the same outcome.
  if (!track) return tracks.slice();
  const next = tracks.slice();
  if (track.source_track) {
    next[trackIndex] = { ...track, server_track: null, match_status: 'missing', confidence: 0.0 };
  } else {
    next.splice(trackIndex, 1);
  }
  return next;
}

/** 837-838: limit is fixed at 20; the artist hint is omitted when empty. */
export async function searchLibraryTracks(
  query: string,
  artistHint: string,
): Promise<LibrarySearchResponse> {
  const url =
    `/api/library/search-tracks?q=${encodeURIComponent(query)}&limit=20` +
    (artistHint ? `&artist=${encodeURIComponent(artistHint)}` : '');
  return (await (await fetch(url)).json()) as LibrarySearchResponse;
}

/** 896-904. */
export async function replaceServerTrack(
  playlistId: string,
  playlistName: string,
  oldTrackId: string | undefined,
  newTrackId: string,
): Promise<ServerMutationResponse> {
  const response = await fetch(`/api/server/playlist/${playlistId}/replace-track`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      old_track_id: oldTrackId,
      new_track_id: newTrackId,
      playlist_name: playlistName,
    }),
  });
  return (await response.json()) as ServerMutationResponse;
}

/**
 * 917-931. The four source_* fields are what make the pick DURABLE: the
 * backend stores them as a manual match override (#787) so later syncs pair the
 * two automatically. `source` falls back to the mirrored playlist's provider
 * because a source track only carries its own when the compare came from a
 * mirrored playlist.
 */
export async function addServerTrack(
  playlistId: string,
  playlistName: string,
  newTrackId: string,
  position: number,
  sourceTrack: NonNullable<CompareTrack['source_track']> | null | undefined,
  mirroredSource: string | null | undefined,
): Promise<ServerMutationResponse> {
  const src = sourceTrack ?? {};
  const response = await fetch(`/api/server/playlist/${playlistId}/add-track`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      track_id: newTrackId,
      playlist_name: playlistName,
      position,
      source_track_id: src.source_track_id || '',
      source_title: src.name || '',
      source_artist: src.artist || '',
      source: src.source || mirroredSource || '',
    }),
  });
  return (await response.json()) as ServerMutationResponse;
}

/** 991-998. */
export async function removeServerTrack(
  playlistId: string,
  playlistName: string,
  trackId: string,
): Promise<ServerMutationResponse> {
  const response = await fetch(`/api/server/playlist/${playlistId}/remove-track`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ track_id: trackId, playlist_name: playlistName }),
  });
  return (await response.json()) as ServerMutationResponse;
}

/* ── The order view + align (385-482) ─────────────────────────────────────── */

/** A row of `server_order` — the server's ACTUAL sequence (295, 394-406). */
export interface ServerOrderTrack {
  title?: string;
  artist?: string;
  thumb?: string;
}

/**
 * 412: the three servers whose clients implement a reorder primitive. The
 * backend gates on the SAME three (web_server.py 22014) — checked, not assumed;
 * its docstring still says "Navidrome only for now" and is simply stale.
 */
export const ALIGNABLE_SERVERS: readonly string[] = ['navidrome', 'plex', 'jellyfin'];

export function canAlignServer(serverType: string | null | undefined): boolean {
  return ALIGNABLE_SERVERS.includes(serverType ?? '');
}

/**
 * 390-391: the heading's label. The vanilla defaults the TYPE to 'server' and
 * then capitalises, where compareServerLabel defaults the LABEL to 'Server';
 * both land on 'Server', so this reuses that one rather than carrying a second
 * copy of the same output.
 */
export function orderModalTitle(serverType: string | null | undefined): string {
  return `${compareServerLabel(serverType)} playlist order`;
}

/**
 * The ids the align POST carries (454-456).
 *
 * MATCHED rows only, in SOURCE order — which is the whole point: the backend
 * rewrites the playlist into exactly this sequence. Missing rows have no server
 * track to name and extras are governed by keep_extras instead, so neither
 * belongs here. `id != null` keeps an id of 0 or '', which `id &&` would drop.
 */
export function alignMatchedIds(tracks: readonly CompareTrack[]): string[] {
  return tracks
    .filter((t) => t.match_status === 'matched' && t.server_track && t.server_track.id != null)
    .map((t) => String(t.server_track?.id));
}

export interface AlignResponse {
  success?: boolean;
  error?: string;
  track_count?: number;
  kept_extras?: boolean;
}

/** 462-470. */
export async function alignServerPlaylist(
  playlistId: string,
  playlistName: string,
  matchedIds: readonly string[],
  keepExtras: boolean,
): Promise<AlignResponse> {
  const response = await fetch(`/api/server/playlist/${playlistId}/align`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      playlist_name: playlistName || '',
      matched_ids: matchedIds,
      keep_extras: !!keepExtras,
    }),
  });
  return (await response.json()) as AlignResponse;
}

/** 988: the confirm copy, verbatim, with the server track's own title. */
export function removeConfirmOptions(track: CompareTrack | undefined): {
  title: string;
  message: string;
  confirmText: string;
  destructive: boolean;
} {
  return {
    title: 'Remove Track',
    message: `Remove "${track?.server_track?.title || 'this track'}" from this playlist?`,
    confirmText: 'Remove',
    destructive: true,
  };
}
